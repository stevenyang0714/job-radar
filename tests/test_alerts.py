from datetime import date, datetime, timezone
from types import SimpleNamespace


from jobradar.alerts import AlertReport, dispatch, score_pending
from jobradar.config import NotificationSettings, ScoringSettings
from jobradar.scorer import Usage

TODAY = date(2026, 10, 4)
NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


def _note(nid, job_pk, score=None, kind="new", baseline=False, closed=False):
    job = {
        "id": job_pk, "source": "acme", "ats": "ashby", "company": "Acme", "job_id": str(job_pk), "title": f"Job {job_pk}",
        "location": "Singapore", "url": f"https://x/{job_pk}", "posted_date": "2026-10-01", "description": "d", "salary": None,
        "employment_type": None, "via_agency": False, "applicants": None, "score": score,
        "score_detail": {"fit_points": ["a"], "gaps": ["b"], "cv_version": "CV_X", "red_flags": []} if score is not None else None,
        "closed_at": "2026-10-03T00:00:00+00:00" if closed else None,
    }
    return {"id": nid, "kind": kind, "baseline": baseline, "job": job}


class FakeStore:
    def __init__(self, notes, spent=0.0):
        self.notes, self.spent = notes, spent
        self.scores, self.usage, self.marked = {}, [], []

    def pending_notifications(self):
        return [n for n in self.notes if n["id"] not in {i for ids, _ in self.marked for i in ids}]

    def mark_notifications(self, ids, status):
        self.marked.append((list(ids), status))

    def save_score(self, job_pk, score, detail):
        self.scores[job_pk] = score
        for n in self.notes:
            if n["job"]["id"] == job_pk:
                n["job"]["score"], n["job"]["score_detail"] = score, detail

    def record_usage(self, row):
        self.usage.append(row)

    def cost_since(self, since):
        return self.spent


class FakeScorer:
    def __init__(self, score=75, cost_tokens=1000, fail_ids=()):
        self.calls, self.score_value, self.fail_ids = [], score, set(fail_ids)
        self.tokens = cost_tokens

    def score(self, job, kind, today):
        self.calls.append(job.job_id)
        if int(job.job_id) in self.fail_ids:
            raise RuntimeError("boom")
        result = SimpleNamespace(score=self.score_value, model_dump=lambda: {"fit_points": ["a"], "gaps": [], "cv_version": "CV", "red_flags": []})
        usage = Usage("gpt-6-luna", "default", input_tokens=self.tokens, cached_tokens=0, cache_write_tokens=0, output_tokens=0, reasoning_tokens=0)
        return SimpleNamespace(result=result, usage=usage)


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def test_scores_each_unscored_job_once_with_regular_notifications_first():
    store = FakeStore([_note(1, 10, baseline=True), _note(2, 20), _note(3, 20, kind="long_open"), _note(4, 30, score=60)])
    scorer, report = FakeScorer(), AlertReport()

    score_pending(store, scorer, ScoringSettings(), TODAY, report, NOW)

    assert scorer.calls == ["20", "10"]  # 30 已評過；20 只評一次；非初始盤點優先
    assert report.scored == 2 and len(store.usage) == 2


def test_budget_already_spent_stops_scoring():
    store = FakeStore([_note(1, 10)], spent=5.0)
    report = AlertReport()

    score_pending(store, FakeScorer(), ScoringSettings(monthly_budget_usd=5), TODAY, report, NOW)

    assert report.budget_hit and report.scored == 0 and report.unscored == 1


def test_budget_crossed_mid_run_stops_after_the_batch():
    store = FakeStore([_note(i, i) for i in range(1, 13)])
    # 每筆 1,000,000 input tokens × US$0.10 = US$0.10，預算 0.5 → 第一批 6 筆後停止
    report = AlertReport()

    score_pending(store, FakeScorer(cost_tokens=1_000_000), ScoringSettings(monthly_budget_usd=0.5), TODAY, report, NOW)

    assert report.scored == 6 and report.budget_hit and report.unscored == 6


def test_failures_are_counted_and_retried_later():
    store = FakeStore([_note(1, 10), _note(2, 20)])
    report = AlertReport()

    score_pending(store, FakeScorer(fail_ids=[10]), ScoringSettings(), TODAY, report, NOW)

    assert (report.scored, report.failed) == (1, 1)
    assert store.notes[0]["job"]["score"] is None


def test_dispatch_routes_by_score():
    notes = [
        _note(1, 10, score=82),  # 立即推播
        _note(2, 20, score=60),  # 每日摘要
        _note(3, 30, score=40),  # 不通知
        _note(4, 40, score=90, baseline=True),  # 初始盤點清單
        _note(5, 50, score=72, baseline=True),  # 初始盤點清單
        _note(8, 80, score=60, baseline=True),  # 初始盤點只列 70 分以上，不通知
        _note(6, 60),  # 還沒評分，留著
        _note(7, 70, score=95, closed=True),  # 已關閉，不通知
    ]
    store, notifier, report = FakeStore(notes), FakeNotifier(), AlertReport()

    dispatch(store, notifier, NotificationSettings(), TODAY, report, send_digest=True, status_line="狀態")

    assert len(notifier.sent) == 3
    assert "82 分" in notifier.sent[0] and "Job 10" in notifier.sent[0]
    assert "初始盤點" in notifier.sent[1] and notifier.sent[1].index("Job 40") < notifier.sent[1].index("Job 50")
    assert "Job 80" not in notifier.sent[1]
    assert "每日摘要" in notifier.sent[2] and "Job 20" in notifier.sent[2] and "狀態" in notifier.sent[2]
    sent = {i for ids, status in store.marked if status == "sent" for i in ids}
    skipped = {i for ids, status in store.marked if status == "skipped" for i in ids}
    assert sent == {1, 2, 4, 5} and skipped == {3, 7, 8}
    assert (report.instant_sent, report.baseline_listed, report.digest_listed, report.skipped) == (1, 2, 1, 3)


def test_digest_waits_until_digest_time():
    store, notifier = FakeStore([_note(1, 10, score=60)]), FakeNotifier()

    dispatch(store, notifier, NotificationSettings(), TODAY, AlertReport(), send_digest=False, status_line="")

    assert notifier.sent == [] and store.marked == []


def test_without_notifier_nothing_is_marked():
    store = FakeStore([_note(1, 10, score=82)])

    messages = dispatch(store, None, NotificationSettings(), TODAY, AlertReport(), send_digest=True, status_line="")

    assert len(messages) == 2 and store.marked == []
