from datetime import date

import httpx

from jobradar.adapters import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from jobradar.models import Source


def _adapter(cls, handler):
    return cls(httpx.Client(transport=httpx.MockTransport(handler)), delay=0)


def _source(url):
    return Source(name="acme", company="Acme", url=url, locations=["Singapore"])


def test_greenhouse_reads_offices_and_unescapes_content():
    def handler(request):
        assert str(request.url) == "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
        job = {
            "id": 123,
            "title": " Account Executive ",
            "location": {"name": "Hybrid"},
            "offices": [{"name": "Singapore", "location": "Singapore"}],
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
            "first_published": "2026-09-07T21:25:04-04:00",
            "content": "&lt;p&gt;Grow &amp;amp; manage accounts&lt;/p&gt;",
        }
        return httpx.Response(200, json={"jobs": [job]})

    [job] = _adapter(GreenhouseAdapter, handler).list_jobs(_source("https://job-boards.greenhouse.io/acme"))

    assert (job.job_id, job.title, job.location) == ("123", "Account Executive", "Hybrid / Singapore")
    assert job.posted_date == date(2026, 9, 7)
    assert job.description == "Grow & manage accounts"


def test_ashby_skips_unlisted_and_merges_locations():
    def handler(request):
        assert request.url.path == "/posting-api/job-board/acme"
        jobs = [
            {
                "id": "a1",
                "title": "Solutions Lead",
                "location": "Tokyo",
                "secondaryLocations": [{"location": "Singapore"}],
                "address": {"postalAddress": {"addressCountry": "Japan"}},
                "jobUrl": "https://jobs.ashbyhq.com/acme/a1",
                "publishedAt": "2026-02-19T23:46:48.031+00:00",
                "descriptionPlain": "Lead AI deployments",
            },
            {"id": "a2", "title": "Hidden", "isListed": False, "jobUrl": "x"},
        ]
        return httpx.Response(200, json={"jobs": jobs})

    [job] = _adapter(AshbyAdapter, handler).list_jobs(_source("https://jobs.ashbyhq.com/acme"))

    assert (job.job_id, job.location, job.description) == ("a1", "Tokyo / Singapore / Japan", "Lead AI deployments")
    assert job.posted_date == date(2026, 2, 19)


def test_lever_builds_description_from_sections():
    def handler(request):
        assert str(request.url) == "https://api.lever.co/v0/postings/acme?mode=json"
        posting = {
            "id": "l1",
            "text": "Account Director - (Taiwan Base)",
            "categories": {"location": "Taipei, Taiwan", "allLocations": ["Taipei, Taiwan"]},
            "hostedUrl": "https://jobs.lever.co/acme/l1",
            "createdAt": 1749706271720,
            "descriptionPlain": "Intro",
            "lists": [{"text": "What You Will Do", "content": "<li>Own accounts</li><li>Grow revenue</li>"}],
            "additionalPlain": "Benefits",
        }
        return httpx.Response(200, json=[posting])

    [job] = _adapter(LeverAdapter, handler).list_jobs(_source("https://jobs.lever.co/acme"))

    assert (job.job_id, job.location) == ("l1", "Taipei, Taiwan")
    assert job.posted_date == date(2025, 6, 12)
    assert job.description == "Intro\n\nWhat You Will Do\n\n• Own accounts\n• Grow revenue\n\nBenefits"
