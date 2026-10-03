"""每次執行的處理流程：抓取 → 去重 → 硬篩 → 寫入資料庫 → 推播條件 → 警告。"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

import httpx

from jobradar.adapters import Adapter, UnsupportedSource, get_adapter
from jobradar.adapters.base import SourceUnavailable
from jobradar.config import NotificationSettings
from jobradar.detect import detect_from_url
from jobradar.filters import Verdict, find_term, screen_detail, screen_listing
from jobradar.messages import BaselineSummary, SourceWarning, format_baseline, format_warnings
from jobradar.models import AtsType, Job, Source
from jobradar.store import KnownJob, SourceHistory

DEFAULT_RULES = NotificationSettings()  # 天數門檻的預設值，實際以 settings.yaml 為準


class StoreLike(Protocol):
    def start_run(self, trigger: str) -> int: ...
    def finish_run(self, run_id: int, status: str, summary: dict[str, Any]) -> None: ...
    def record_source_run(self, run_id: int, source: str, status: str, job_count: int | None = ..., new_count: int | None = ..., error: str | None = ...) -> None: ...
    def source_history(self, source: str) -> SourceHistory: ...
    def known_jobs(self, source: str) -> list[KnownJob]: ...
    def insert_jobs(self, rows: list[dict[str, Any]]) -> dict[tuple[str, str], int]: ...
    def mark_seen(self, ids: list[int]) -> None: ...
    def mark_closed(self, ids: list[int]) -> None: ...
    def add_notifications(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


class NotifierLike(Protocol):
    def send(self, text: str) -> None: ...


@dataclass
class Screened:
    job: Job
    verdict: Verdict


@dataclass
class SourceResult:
    source: Source
    status: str  # ok / error / unavailable
    job_count: int = 0
    new_count: int = 0
    passed_new: int = 0
    baseline: bool = False
    notifications: Counter = field(default_factory=Counter)  # kind → 筆數
    detail_errors: int = 0
    error: str | None = None
    warning: str | None = None  # 要推播給使用者的警告


@dataclass
class RunReport:
    run_id: int
    results: list[SourceResult]
    messages: list[str]

    @property
    def status(self) -> str:
        failed = [r for r in self.results if r.status == "error"]
        if not failed:
            return "ok"
        return "failed" if len(failed) == len(self.results) else "partial"


def resolve_ats(source: Source) -> AtsType:
    return source.type or detect_from_url(str(source.url)) or "generic"


def screen_jobs(adapter: Adapter, source: Source, jobs: Iterable[Job]) -> list[Screened]:
    """先用清單資料篩地區與排除詞，通過的才抓完整內容比對關鍵字，減少對網站的請求。"""
    results = []
    for job in jobs:
        verdict = screen_listing(job, source, check_location=not adapter.handles_location)
        if verdict is None:
            if job.description is None:
                try:
                    job = adapter.fetch_detail(job)
                except Exception as e:  # 單筆失敗不影響其他職缺；不寫入資料庫，下次會重試
                    results.append(Screened(job, Verdict("detail_error", describe_error(e))))
                    continue
            verdict = screen_detail(job, source)
        results.append(Screened(job, verdict))
    return results


def job_age_days(posted_date: date | None, first_seen: date, today: date) -> int:
    """以刊登日計算；網站沒提供刊登日時，以第一次看到的日期代替。"""
    return (today - (posted_date or first_seen)).days


def notification_kind(age_days: int, rules: NotificationSettings = DEFAULT_RULES) -> str | None:
    """🆕 刊登一週內；⏳ 刊登 4 個月以上仍未關閉（可能找不到人）。"""
    if age_days <= rules.new_within_days:
        return "new"
    if age_days >= rules.long_open_after_days:
        return "long_open"
    return None


def job_row(job: Job, filter_status: str) -> dict[str, Any]:
    row = job.model_dump(mode="json")
    row["filter_status"] = filter_status
    if filter_status != "passed":
        row["description"] = None  # 只有通過硬篩的需要內容（評分用），其餘只留基本資料供去重，節省資料庫空間
    return row


def process_source(
    source: Source,
    adapter: Adapter,
    store: StoreLike,
    run_id: int,
    other_companies: list[str],
    today: date,
    rules: NotificationSettings = DEFAULT_RULES,
) -> SourceResult:
    history = store.source_history(source.name)
    baseline = not history.has_success

    try:
        listed = adapter.list_jobs(source)
    except SourceUnavailable as e:
        # 例行維護常見，連續兩次才警告
        store.record_source_run(run_id, source.name, "unavailable", error=str(e))
        repeated = history.last_status in ("unavailable", "error")
        return SourceResult(source, "unavailable", error=str(e), warning=f"連續兩次無法使用：{e}" if repeated else None)

    known = {(k.company, k.job_id): k for k in store.known_jobs(source.name)}
    seen: dict[tuple[str, str], KnownJob] = {}
    new_jobs: dict[tuple[str, str], Job] = {}
    for job in listed:
        key = (job.company, job.job_id)
        if key in known:
            seen[key] = known[key]
        else:
            new_jobs.setdefault(key, job)

    # 搜尋型來源（MyCareersFuture）裡已有專屬來源的公司：略過，以公司官網的資料為準
    candidates, duplicates = [], []
    for job in new_jobs.values():
        (duplicates if find_term(other_companies, job.company) else candidates).append(job)

    screened = screen_jobs(adapter, source, candidates)
    stored = [s for s in screened if s.verdict.status != "detail_error"]
    rows = [job_row(s.job, s.verdict.status) for s in stored] + [job_row(j, "duplicate") for j in duplicates]
    ids = store.insert_jobs(rows) if rows else {}
    store.mark_seen([k.id for k in seen.values()])
    if listed:  # 抓到 0 筆時不標記關閉，避免網站異常時誤判全部關閉
        store.mark_closed([k.id for key, k in known.items() if key not in seen and k.closed_at is None])

    notifications = []
    for s in stored:
        kind = notification_kind(job_age_days(s.job.posted_date, today, today), rules) if s.verdict.passed else None
        if kind:
            notifications.append({"job": ids[(s.job.company, s.job.job_id)], "kind": kind, "baseline": baseline})
    for k in seen.values():
        # 之前看過、這次仍在的職缺：滿 4 個月時觸發（已通知過的會被資料庫略過）
        if k.filter_status == "passed" and job_age_days(k.posted_date, k.first_seen_at.date(), today) >= rules.long_open_after_days:
            notifications.append({"job": k.id, "kind": "long_open", "baseline": False})
    added = store.add_notifications(notifications) if notifications else []

    store.record_source_run(run_id, source.name, "ok", job_count=len(listed), new_count=len(new_jobs))
    result = SourceResult(
        source,
        "ok",
        job_count=len(listed),
        new_count=len(new_jobs),
        passed_new=sum(s.verdict.passed for s in stored),
        baseline=baseline,
        notifications=Counter(n["kind"] for n in added),
        detail_errors=len(screened) - len(stored),
    )
    if not listed and (history.last_ok_count or 0) > 0:
        result.warning = f"上次抓到 {history.last_ok_count} 筆，這次 0 筆，網站可能改版"
    elif result.detail_errors and result.detail_errors >= max(3, len(candidates) // 2):
        result.warning = f"{result.detail_errors} 筆職缺內容抓取失敗，下次會重試"
    return result


def run(
    sources: list[Source],
    store: StoreLike,
    notifier: NotifierLike | None,
    client: httpx.Client,
    trigger: str,
    today: date | None = None,
    rules: NotificationSettings = DEFAULT_RULES,
) -> RunReport:
    today = today or date.today()
    run_id = store.start_run(trigger)
    company_names = [name for s in sources if resolve_ats(s) != "mycareersfuture" for name in (s.company, *s.aliases)]

    results = []
    for source in sources:
        ats = resolve_ats(source)
        others = company_names if ats == "mycareersfuture" else []
        try:
            result = process_source(source, get_adapter(ats, client), store, run_id, others, today, rules)
        except Exception as e:  # 一個來源失敗不影響其他來源
            message = describe_error(e)
            try:
                store.record_source_run(run_id, source.name, "error", error=message)
            except Exception:
                pass  # 資料庫本身有問題時，警告仍要送出
            result = SourceResult(source, "error", error=message, warning=f"抓取失敗：{message}")
        results.append(result)

    messages = []
    warnings = [SourceWarning(r.source.company, r.warning) for r in results if r.warning]
    if warnings:
        messages.append(format_warnings(warnings))
    baselines = [
        BaselineSummary(r.source.company, r.job_count, r.passed_new, r.notifications["new"], r.notifications["long_open"])
        for r in results
        if r.status == "ok" and r.baseline
    ]
    if baselines:
        messages.append(format_baseline(baselines))

    report = RunReport(run_id, results, messages)
    store.finish_run(run_id, report.status, _summary(results))
    if notifier:
        for message in messages:
            notifier.send(message)
    return report


def _summary(results: list[SourceResult]) -> dict[str, Any]:
    return {
        r.source.name: {
            "status": r.status,
            "jobs": r.job_count,
            "new": r.new_count,
            "passed_new": r.passed_new,
            "notifications": dict(r.notifications),
            **({"error": r.error} if r.error else {}),
        }
        for r in results
    }


def describe_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}（{e.request.url.host}）"
    if isinstance(e, UnsupportedSource):
        return str(e)
    return f"{type(e).__name__}: {e}"[:300]
