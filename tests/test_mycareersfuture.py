import json
from datetime import date

import httpx
import pytest

from jobradar.adapters.mycareersfuture import MyCareersFutureAdapter
from jobradar.models import Job, Source


def _item(uuid, title, company="ACME PTE. LTD.", low=10000, high=15000, **metadata):
    return {
        "uuid": uuid,
        "title": title,
        "postedCompany": {"name": company},
        "hiringCompany": metadata.pop("hiring", None),
        "salary": {"minimum": low, "maximum": high, "type": {"salaryType": "Monthly"}},
        "employmentTypes": [{"employmentType": "Permanent"}, {"employmentType": "Full Time"}],
        "metadata": {"jobDetailsUrl": f"https://www.mycareersfuture.gov.sg/job/{uuid}", "newPostingDate": "2026-09-30", **metadata},
    }


def _source(**kwargs):
    return Source(name="mcf", company="MyCareersFuture", url="https://www.mycareersfuture.gov.sg/", **kwargs)


def _adapter(handler):
    return MyCareersFutureAdapter(httpx.Client(transport=httpx.MockTransport(handler)), delay=0)


def test_searches_are_merged_and_agencies_flagged():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if body["search"] == "AI governance":
            results = [
                _item("j1", "AI Governance Lead"),
                _item("j2", "Strategy Director", company="KORUHR", isPostedOnBehalf=True, hiring={"name": "KOERBER TECHNOLOGIES PTE. LTD."}),
            ]
        else:
            results = [
                _item("j1", "AI Governance Lead"),  # 兩個搜尋都出現
                _item("j3", "Risk Manager", company="HAYS SPECIALIST RECRUITMENT PTE. LTD."),
                _item("j4", "Below Target", low=5000, high=8000),
            ]
        return httpx.Response(200, json={"results": results, "total": len(results)})

    jobs = _adapter(handler).list_jobs(_source(searches=["AI governance", "technology risk"], min_salary=10000))

    assert [b["salary"] for b in bodies] == [10000, 10000]
    assert [j.job_id for j in jobs] == ["j1", "j2", "j3"]  # j4 月薪上限未達標
    lead, strategy, risk = jobs
    assert (lead.company, lead.via_agency, lead.salary) == ("ACME PTE. LTD.", False, "SGD 10,000–15,000 / 月")
    assert lead.employment_type == "Permanent, Full Time"
    assert (strategy.company, strategy.via_agency) == ("KOERBER TECHNOLOGIES PTE. LTD.", True)
    assert risk.via_agency


def test_paginates_until_total_reached():
    pages = []

    def handler(request):
        page = int(request.url.params["page"])
        pages.append(page)
        count = 100 if page == 0 else 30
        results = [_item(f"p{page}-{i}", "Job") for i in range(count)]
        return httpx.Response(200, json={"results": results, "total": 130})

    jobs = _adapter(handler).list_jobs(_source(searches=["strategy"]))

    assert pages == [0, 1]
    assert len(jobs) == 130


def test_fetch_detail_uses_original_posting_date():
    def handler(request):
        assert request.url.path == "/v2/jobs/j1"
        metadata = {"newPostingDate": "2026-09-30", "originalPostingDate": "2026-05-04", "totalNumberJobApplication": 3}
        return httpx.Response(200, json={"description": "<p>Lead <b>AI</b> governance</p>", "metadata": metadata})

    job = Job(source="mcf", ats="mycareersfuture", company="ACME", job_id="j1", title="Lead", url="https://x")
    detailed = _adapter(handler).fetch_detail(job)

    assert detailed.description == "Lead AI governance"
    assert detailed.posted_date == date(2026, 5, 4)
    assert detailed.applicants == 3


def test_requires_searches():
    with pytest.raises(ValueError, match="searches"):
        _adapter(lambda r: httpx.Response(200, json={})).list_jobs(_source())
