import httpx
import pytest

from jobradar.detect import detect, detect_from_url


@pytest.mark.parametrize(
    "url, ats",
    [
        ("https://abbott.wd5.myworkdayjobs.com/abbottcareers", "workday"),
        ("https://wd3.myworkdaysite.com/recruiting/acme/External", "workday"),
        ("https://boards.greenhouse.io/acme", "greenhouse"),
        ("https://job-boards.greenhouse.io/acme", "greenhouse"),
        ("https://jobs.lever.co/acme", "lever"),
        ("https://jobs.ashbyhq.com/acme", "ashby"),
        ("https://careers.acme.com/jobs", None),
    ],
)
def test_detect_from_url(url, ats):
    assert detect_from_url(url) == ats


def test_detect_finds_ats_link_embedded_in_company_page():
    html = '<a href="https://acme.wd1.myworkdayjobs.com/en-US/External/login?utm_source=site&amp;x=1">Open roles</a>'
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html)))
    result = detect("https://careers.acme.com", client)
    assert (result.ats, result.url, result.how) == (
        "workday",
        "https://acme.wd1.myworkdayjobs.com/en-US/External",
        "頁面內容",
    )


def test_detect_follows_redirect_to_ats():
    def handler(request):
        if request.url.host == "careers.acme.com":
            return httpx.Response(302, headers={"Location": "https://jobs.lever.co/acme"})
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    result = detect("https://careers.acme.com", client)
    assert (result.ats, result.url, result.how) == ("lever", "https://jobs.lever.co/acme", "轉址")


def test_detect_falls_back_to_generic():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>")))
    assert detect("https://careers.acme.com", client).ats == "generic"
