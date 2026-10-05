from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from jobradar import pipeline
from jobradar.adapters.base import Adapter, SourceUnavailable
from jobradar.config import NotificationSettings
from jobradar.models import Job, Source
from jobradar.pipeline import notification_kind, process_source, run, screen_jobs
from jobradar.store import KnownJob, SourceHistory

TODAY = date(2026, 10, 3)


class FakeAdapter(Adapter):
    ats = "workday"
    handles_location = True

    def __init__(self, jobs=None, error=None, broken_details=()):
        super().__init__(httpx.Client(), delay=0)
        self.jobs = jobs or []
        self.error = error
        self.broken_details = set(broken_details)
        self.detail_calls = []

    def list_jobs(self, source):
        if self.error:
            raise self.error
        return self.jobs

    def fetch_detail(self, job):
        self.detail_calls.append(job.job_id)
        if job.job_id in self.broken_details:
            raise httpx.ConnectError("boom")
        return job.model_copy(update={"description": "We need AI governance experience"})


class FakeStore:
    def __init__(self, history=None, known=None, notified=()):
        self.history = history or {}
        self.known = known or {}
        self.inserted, self.seen, self.closed, self.source_runs = [], [], [], []
        self.notifications = [{"job": job, "kind": kind} for job, kind in notified]
        self.finished = None
        self._next_id = 100

    def start_run(self, trigger):
        return 1

    def finish_run(self, run_id, status, summary):
        self.finished = (status, summary)

    def record_source_run(self, run_id, source, status, job_count=None, new_count=None, error=None):
        self.source_runs.append((source, status, job_count, new_count))

    def source_history(self, source):
        return self.history.get(source, SourceHistory(has_success=False, last_status=None, last_ok_count=None))

    def known_jobs(self, source):
        return self.known.get(source, [])

    def insert_jobs(self, rows):
        ids = {}
        for row in rows:
            self._next_id += 1
            ids[(row["company"], row["job_id"])] = self._next_id
            self.inserted.append(row)
        return ids

    def mark_seen(self, ids):
        self.seen += ids

    def mark_closed(self, ids):
        self.closed += ids

    def add_notifications(self, rows):
        existing = {(n["job"], n["kind"]) for n in self.notifications}
        added = [r for r in rows if (r["job"], r["kind"]) not in existing]
        self.notifications += added
        return added


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def _source(name="acme", **kwargs):
    return Source(name=name, company=kwargs.pop("company", "Acme"), url="https://acme.wd3.myworkdayjobs.com/careers", **kwargs)


def _job(job_id, title="Risk Manager", days_ago=None, company="Acme"):
    posted = TODAY - timedelta(days=days_ago) if days_ago is not None else None
    return Job(source="acme", ats="workday", company=company, job_id=job_id, title=title, url=f"https://x/{job_id}", posted_date=posted)


def _known(pk, job_id, days_ago=30, filter_status="passed", closed=False, location="Singapore"):
    first_seen = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return KnownJob(
        id=pk,
        company="Acme",
        job_id=job_id,
        filter_status=filter_status,
        location=location,
        posted_date=TODAY - timedelta(days=days_ago),
        first_seen_at=first_seen,
        closed_at=first_seen if closed else None,
    )


OK_HISTORY = SourceHistory(has_success=True, last_status="ok", last_ok_count=3)


@pytest.mark.parametrize("age, kind", [(0, "new"), (7, "new"), (8, None), (119, None), (120, "long_open"), (900, "long_open")])
def test_notification_kind(age, kind):
    assert notification_kind(age) == kind


def test_detail_fetched_only_for_jobs_passing_listing_screen():
    source = _source(keywords=["governance"], exclude=["intern"])
    adapter = FakeAdapter()

    results = screen_jobs(adapter, source, [_job("1"), _job("2", title="Summer Intern")])

    assert adapter.detail_calls == ["1"]
    assert [r.verdict.status for r in results] == ["passed", "rejected_exclude"]


def test_first_run_is_baseline_and_only_new_or_long_open_jobs_are_queued():
    store = FakeStore()
    adapter = FakeAdapter([_job("new", days_ago=3), _job("mid", days_ago=40), _job("old", days_ago=200), _job("intern", title="Intern", days_ago=1)])
    source = _source(exclude=["intern"])

    result = process_source(source, adapter, store, 1, [], TODAY)

    assert result.baseline and result.status == "ok"
    assert [r["filter_status"] for r in store.inserted] == ["passed", "passed", "passed", "rejected_exclude"]
    assert store.inserted[0]["description"] and store.inserted[3]["description"] is None
    queued = {(store.inserted[n["job"] - 101]["job_id"], n["kind"], n["baseline"]) for n in store.notifications}
    assert queued == {("new", "new", True), ("old", "long_open", True)}
    assert store.source_runs == [("acme", "ok", 4, 4)]


def test_later_run_tracks_seen_closed_and_jobs_turning_four_months_old():
    known = [
        _known(1, "still-open", days_ago=30),
        _known(2, "turned-old", days_ago=125),
        _known(3, "already-notified", days_ago=300),
        _known(4, "gone", days_ago=10),
        _known(5, "closed-before", days_ago=10, closed=True),
    ]
    store = FakeStore(history={"acme": OK_HISTORY}, known={"acme": known}, notified=[(3, "long_open")])
    adapter = FakeAdapter([_job("still-open"), _job("turned-old"), _job("already-notified"), _job("brand-new", days_ago=1)])

    result = process_source(_source(), adapter, store, 1, [], TODAY)

    assert not result.baseline
    assert sorted(store.seen) == [1, 2, 3]
    assert store.closed == [4]  # 5 之前就已標記關閉
    assert adapter.detail_calls == ["brand-new"]  # 看過的職缺不再抓內容
    assert dict(result.notifications) == {"new": 1, "long_open": 1}
    assert {(n["job"], n["kind"], n["baseline"]) for n in store.notifications[1:]} == {(101, "new", False), (2, "long_open", False)}


def test_search_source_skips_companies_with_their_own_source():
    store = FakeStore()
    adapter = FakeAdapter([_job("1", company="DBS BANK LTD.", days_ago=1), _job("2", company="ACME PTE. LTD.", days_ago=1)])

    process_source(_source(name="mcf", company="MyCareersFuture"), adapter, store, 1, ["DBS", "Citi"], TODAY)

    assert [(r["company"], r["filter_status"]) for r in store.inserted] == [("ACME PTE. LTD.", "passed"), ("DBS BANK LTD.", "duplicate")]
    assert adapter.detail_calls == ["2"]
    assert len(store.notifications) == 1


def test_failed_detail_is_not_stored_so_it_is_retried():
    store = FakeStore(history={"acme": OK_HISTORY})
    adapter = FakeAdapter([_job("1", days_ago=1), _job("2", days_ago=1)], broken_details=["2"])

    result = process_source(_source(), adapter, store, 1, [], TODAY)

    assert [r["job_id"] for r in store.inserted] == ["1"]
    assert result.detail_errors == 1


def test_zero_jobs_after_a_good_run_warns_and_closes_nothing():
    store = FakeStore(history={"acme": OK_HISTORY}, known={"acme": [_known(1, "a")]})

    result = process_source(_source(), FakeAdapter([]), store, 1, [], TODAY)

    assert "0 筆" in result.warning
    assert store.closed == []


class ClientSideAdapter(FakeAdapter):
    handles_location = False  # 例如 Greenhouse：程式自己比對地點


def test_new_only_locations_skip_long_open_notifications():
    rules = NotificationSettings(new_only_locations=["Japan"])
    source = _source(locations=["Singapore", "Japan"])
    jobs = [
        _job("tokyo-old", days_ago=200).model_copy(update={"location": "Tokyo"}),
        _job("tokyo-new", days_ago=2).model_copy(update={"location": "Tokyo, Japan"}),
        _job("both-old", days_ago=200).model_copy(update={"location": "Singapore / Tokyo"}),
    ]
    store = FakeStore(history={"acme": OK_HISTORY})

    process_source(source, ClientSideAdapter(jobs), store, 1, [], TODAY, rules)

    queued = {store.inserted[n["job"] - 101]["job_id"]: n["kind"] for n in store.notifications}
    assert queued == {"tokyo-new": "new", "both-old": "long_open"}


def test_jobs_rejected_for_location_are_rescreened_after_adding_a_region():
    known = [_known(1, "tokyo", filter_status="rejected_location", location="Tokyo")]
    store = FakeStore(history={"acme": OK_HISTORY}, known={"acme": known})
    job = _job("tokyo", days_ago=3).model_copy(update={"location": "Tokyo"})

    result = process_source(_source(locations=["Singapore", "Japan"]), ClientSideAdapter([job]), store, 1, [], TODAY)

    assert result.rescreened == 1
    assert [(r["job_id"], r["filter_status"]) for r in store.inserted] == [("tokyo", "passed")]
    assert dict(result.notifications) == {"new": 1}


def test_maintenance_warns_only_when_repeated():
    first = process_source(_source(), FakeAdapter(error=SourceUnavailable("維護中")), FakeStore(history={"acme": OK_HISTORY}), 1, [], TODAY)
    repeated_history = SourceHistory(has_success=True, last_status="unavailable", last_ok_count=3)
    second = process_source(_source(), FakeAdapter(error=SourceUnavailable("維護中")), FakeStore(history={"acme": repeated_history}), 1, [], TODAY)

    assert (first.status, first.warning) == ("unavailable", None)
    assert "連續兩次" in second.warning


def test_run_isolates_failures_and_sends_warning_and_baseline(monkeypatch):
    adapters = {"workday": FakeAdapter([_job("1", days_ago=2)]), "greenhouse": FakeAdapter(error=httpx.ConnectError("down"))}
    monkeypatch.setattr(pipeline, "get_adapter", lambda ats, client: adapters[ats])
    store, notifier = FakeStore(), FakeNotifier()
    sources = [_source("good", company="Good"), _source("bad", company="Bad", type="greenhouse")]

    report = run(sources, store, notifier, httpx.Client(), "local", TODAY)

    assert [r.status for r in report.results] == ["ok", "error"]
    assert report.status == "partial"
    assert store.finished[0] == "partial"
    assert len(notifier.sent) == 2
    assert "Bad" in notifier.sent[0] and "抓取失敗" in notifier.sent[0]
    assert "初始盤點" in notifier.sent[1] and "Good" in notifier.sent[1]
