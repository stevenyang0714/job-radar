import httpx

from jobradar.adapters.base import Adapter
from jobradar.models import Job, Source
from jobradar.pipeline import screen_jobs


class _FakeAdapter(Adapter):
    ats = "workday"
    handles_location = True

    def __init__(self):
        super().__init__(httpx.Client(), delay=0)
        self.detail_calls = []

    def list_jobs(self, source):
        return []

    def fetch_detail(self, job):
        self.detail_calls.append(job.job_id)
        return job.model_copy(update={"description": "We need AI governance experience"})


def _job(job_id, title):
    return Job(source="acme", ats="workday", company="Acme", job_id=job_id, title=title, url=f"https://x/{job_id}")


def test_detail_fetched_only_for_jobs_passing_listing_screen():
    source = Source(name="acme", company="Acme", url="https://x", keywords=["governance"], exclude=["intern"])
    adapter = _FakeAdapter()

    results = screen_jobs(adapter, source, [_job("1", "Risk Manager"), _job("2", "Summer Intern")])

    assert adapter.detail_calls == ["1"]
    assert [r.verdict.status for r in results] == ["passed", "rejected_exclude"]
    assert results[0].job.description == "We need AI governance experience"
