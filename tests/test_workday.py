import json
from datetime import date

import httpx
import pytest

from jobradar.adapters.base import SourceUnavailable, UnexpectedResponse
from jobradar.adapters.workday import WorkdayAdapter, _posted_date, match_location_facets, parse_site
from jobradar.models import Job, Source

BASE = "https://acme.wd3.myworkdayjobs.com/careers"

FACETS = [
    {
        "facetParameter": "Location_Country",
        "values": [
            {"descriptor": "Singapore", "id": "sg", "count": 2},
            {"descriptor": "Taiwan", "id": "tw", "count": 1},
            {"descriptor": "United States of America", "id": "us", "count": 50},
        ],
    },
    {
        "facetParameter": "locationMainGroup",
        "values": [
            {
                "facetParameter": "locations",
                "descriptor": "Locations",
                "values": [
                    {"descriptor": "Singapore", "id": "sg-city", "count": 2},
                    {"descriptor": "Hong Kong", "id": "hk-city", "count": 1},
                ],
            }
        ],
    },
    {"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Taiwan Ops", "id": "fam", "count": 1}]},
]


def _source(locations):
    return Source(name="acme", company="Acme", url=BASE, locations=locations)


def _posting(title, path, loc="Singapore", posted="Posted Today"):
    return {"title": title, "externalPath": path, "locationsText": loc, "postedOn": posted}


def _adapter(handler):
    return WorkdayAdapter(httpx.Client(transport=httpx.MockTransport(handler)), delay=0)


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://abbott.wd5.myworkdayjobs.com/abbottcareers",
            ("abbott", "abbottcareers", "https://abbott.wd5.myworkdayjobs.com/wday/cxs/abbott/abbottcareers"),
        ),
        (
            "https://abbott.wd5.myworkdayjobs.com/en-US/abbottcareers/job/Singapore/X_1",
            ("abbott", "abbottcareers", "https://abbott.wd5.myworkdayjobs.com/wday/cxs/abbott/abbottcareers"),
        ),
        (
            "https://wd3.myworkdaysite.com/en-US/recruiting/acme/External",
            ("acme", "External", "https://wd3.myworkdaysite.com/wday/cxs/acme/External"),
        ),
    ],
)
def test_parse_site(url, expected):
    site = parse_site(url)
    assert (site.tenant, site.site, site.api_base) == expected


def test_parse_site_rejects_url_without_site():
    with pytest.raises(ValueError):
        parse_site("https://abbott.wd5.myworkdayjobs.com/")


def test_match_location_facets_prefers_country_and_reads_nested_groups():
    selection = match_location_facets(FACETS, ["Singapore", "Taiwan", "Hong Kong", "Japan"])
    assert selection.applied == {"Location_Country": ["sg", "tw"], "locations": ["hk-city"]}
    assert selection.missing == ["Japan"]


def test_match_location_facets_ignores_non_location_facets():
    assert match_location_facets([FACETS[2]], ["Taiwan"]) is None


def test_list_jobs_queries_each_facet_group_and_merges_results():
    applied = []

    def handler(request):
        facets = json.loads(request.content)["appliedFacets"]
        applied.append(facets)
        if not facets:
            return httpx.Response(200, json={"total": 99, "jobPostings": [], "facets": FACETS})
        if "Location_Country" in facets:
            postings = [
                _posting("Risk Manager", "/job/Singapore/Risk-Manager_R100"),
                _posting("AI Lead", "/job/Taipei/AI-Lead_R200", loc="2  Locations"),
            ]
        else:
            postings = [
                _posting("Risk Manager", "/job/Singapore/Risk-Manager_R100"),  # 兩次查詢都出現
                _posting("Strategy Manager", "/job/HK/Strategy-Manager_R300", loc="Hong Kong"),
            ]
        return httpx.Response(200, json={"total": 2, "jobPostings": postings})

    jobs = _adapter(handler).list_jobs(_source(["Singapore", "Taiwan", "Hong Kong"]))

    assert applied == [{}, {"Location_Country": ["sg", "tw"]}, {"locations": ["hk-city"]}]
    assert [j.job_id for j in jobs] == ["R100", "R200", "R300"]
    assert jobs[1].url == f"{BASE}/job/Taipei/AI-Lead_R200"
    assert jobs[1].location == "2 Locations"


def test_paging_stops_when_workday_repeats_the_last_page():
    page1 = [_posting(f"Job {i}", f"/job/x/Job_{i}") for i in range(20)]
    page2 = [_posting(f"Job {i}", f"/job/x/Job_{i}") for i in range(20, 25)]
    offsets = []

    def handler(request):
        offset = json.loads(request.content)["offset"]
        offsets.append(offset)
        if offset == 0:
            return httpx.Response(200, json={"total": 30, "jobPostings": page1})
        return httpx.Response(200, json={"total": 0, "jobPostings": page2})

    jobs = _adapter(handler).list_jobs(_source([]))

    assert len(jobs) == 25
    assert offsets == [0, 20, 25]


def test_fetch_detail_converts_html_and_merges_locations():
    def handler(request):
        assert request.url.path == "/wday/cxs/acme/careers/job/Taipei/AI-Lead_R200"
        info = {
            "jobDescription": "<p>&nbsp;</p><h2><b>About</b></h2><ul><li>Lead AI&nbsp;governance</li><li>Work with <i>risk</i></li></ul>",
            "location": "Taiwan  >  Taipei",
            "additionalLocations": ["Singapore"],
            "startDate": "2026-09-30",
        }
        return httpx.Response(200, json={"jobPostingInfo": info})

    job = Job(
        source="acme",
        ats="workday",
        company="Acme",
        job_id="R200",
        title="AI Lead",
        location="2 Locations",
        url=f"{BASE}/job/Taipei/AI-Lead_R200",
    )
    detailed = _adapter(handler).fetch_detail(job)

    assert detailed.description == "About\n\n• Lead AI governance\n• Work with risk"
    assert detailed.location == "Taiwan > Taipei / Singapore"
    assert detailed.posted_date == date(2026, 9, 30)


def test_without_location_facets_matches_text_and_resolves_multi_location_jobs():
    def handler(request):
        if request.method == "POST":
            postings = [
                _posting("A", "/job/x/A_1", loc="Singapore"),
                _posting("B", "/job/x/B_2", loc="Germany"),
                _posting("C", "/job/x/C_3", loc="2 Locations"),
            ]
            return httpx.Response(200, json={"total": 3, "jobPostings": postings, "facets": [FACETS[2]]})
        info = {"jobDescription": "<p>x</p>", "location": "Germany", "additionalLocations": ["Taiwan"]}
        return httpx.Response(200, json={"jobPostingInfo": info})

    adapter = _adapter(handler)
    jobs = adapter.list_jobs(_source(["Taiwan", "Singapore"]))

    assert [j.job_id for j in jobs] == ["1", "3"]
    assert jobs[1].description == "x"  # 已抓過詳細資料，之後不必再抓
    assert "沒有地區篩選選項" in adapter.notes[0]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Posted Today", date(2026, 10, 3)),
        ("Posted Yesterday", date(2026, 10, 2)),
        ("Posted 3 Days Ago", date(2026, 9, 30)),
        ("Posted 30+ Days Ago", None),
        (None, None),
    ],
)
def test_posted_date(text, expected):
    assert _posted_date(text, today=date(2026, 10, 3)) == expected


def test_location_aliases_match_coded_facets():
    facets = [{"facetParameter": "locations", "values": [{"descriptor": "TWN-Taipei-Office", "id": "t1", "count": 3}]}]
    assert match_location_facets(facets, ["Taiwan"]).applied == {"locations": ["t1"]}


def test_maintenance_redirect_raises_source_unavailable():
    def handler(request):
        if request.url.host == "static.community.workday.com":
            return httpx.Response(200, text="<html>maintenance</html>", headers={"content-type": "text/html"})
        return httpx.Response(303, headers={"Location": "https://static.community.workday.com/maintenance-page.html"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(SourceUnavailable, match="維護"):
        WorkdayAdapter(client, delay=0).list_jobs(_source(["Taiwan"]))


def test_html_instead_of_json_raises_clear_error():
    def handler(request):
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    with pytest.raises(UnexpectedResponse, match="預期 JSON"):
        _adapter(handler).list_jobs(_source([]))


def test_request_json_retries_on_server_error(monkeypatch):
    monkeypatch.setattr("jobradar.adapters.base.time.sleep", lambda _: None)
    statuses = iter([503, 200])

    def handler(request):
        status = next(statuses)
        return httpx.Response(status, json={"ok": status == 200})

    assert _adapter(handler).request_json("GET", f"{BASE}/x") == {"ok": True}
