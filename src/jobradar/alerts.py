"""AI 評分與推播發送。

1. 評分：只評「有待發推播、還沒評過分」的職缺，每個職缺只評一次；受每月預算限制
2. 推播：依分數決定立即推播、放進每日摘要、或不通知
"""

from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from jobradar.config import NotificationSettings, ScoringSettings
from jobradar.messages import format_baseline_jobs, format_budget_stop, format_digest, format_job, format_job_line
from jobradar.models import Job
from jobradar.pipeline import NotifierLike, describe_error
from jobradar.scorer import ScoreOutcome

TAIPEI = timezone(timedelta(hours=8))


class AlertStore(Protocol):
    def pending_notifications(self) -> list[dict[str, Any]]: ...
    def mark_notifications(self, ids: Sequence[int], status: str) -> None: ...
    def save_score(self, job_pk: int, score: int, detail: dict[str, Any]) -> None: ...
    def record_usage(self, row: dict[str, Any]) -> None: ...
    def cost_since(self, since: datetime) -> float: ...


class ScorerLike(Protocol):
    def score(self, job: Job, kind: str | None, today: date) -> ScoreOutcome: ...


@dataclass
class AlertReport:
    scored: int = 0
    failed: int = 0
    cost_usd: float = 0.0
    month_cost_usd: float = 0.0
    budget_usd: float = 0.0
    budget_hit: bool = False
    unscored: int = 0  # 因預算或筆數上限沒評到的
    instant_sent: int = 0
    baseline_listed: int = 0
    digest_sent: bool = False
    digest_listed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def to_job(row: dict[str, Any]) -> Job:
    return Job.model_validate({k: v for k, v in row.items() if k in Job.model_fields})


def month_start(now: datetime) -> datetime:
    return now.astimezone(TAIPEI).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def score_pending(store: AlertStore, scorer: ScorerLike, settings: ScoringSettings, today: date, report: AlertReport, now: datetime | None = None) -> None:
    # 一般推播優先，初始盤點其次；同一個職缺只評一次
    queue: dict[int, tuple[Job, str]] = {}
    for n in sorted(store.pending_notifications(), key=lambda n: n["baseline"]):
        row = n["job"]
        if row["score"] is None and row["id"] not in queue:
            queue[row["id"]] = (to_job(row), n["kind"])
    items = list(queue.items())
    report.unscored = max(0, len(items) - settings.max_per_run)
    items = items[: settings.max_per_run]

    report.budget_usd = settings.monthly_budget_usd
    spent = store.cost_since(month_start(now or datetime.now(timezone.utc)))
    # Flex 每筆要十幾秒，平行處理才不會太久；筆數受帳號每分鐘的 token 上限限制
    with ThreadPoolExecutor(settings.concurrency) as pool:
        for batch in _batches(items, settings.concurrency):
            if spent >= settings.monthly_budget_usd:
                report.budget_hit = True
                report.unscored += len(items) - report.scored - report.failed
                break
            futures = [(pk, pool.submit(scorer.score, job, kind, today)) for pk, (job, kind) in batch]
            batch_failures = 0
            for pk, future in futures:
                try:
                    outcome = future.result()
                except Exception as e:  # 單筆失敗不影響其他職缺，下次執行會重試
                    report.failed += 1
                    batch_failures += 1
                    report.errors.append(describe_error(e))
                    continue
                store.save_score(pk, outcome.result.score, outcome.result.model_dump())
                store.record_usage(usage_row(pk, outcome))
                report.scored += 1
                report.cost_usd += outcome.usage.cost_usd
                spent += outcome.usage.cost_usd
            if batch_failures == len(batch):  # 整批都失敗，多半是 API 或金鑰出問題，先停下來
                report.unscored += len(items) - report.scored - report.failed
                break
    report.month_cost_usd = spent


def dispatch(
    store: AlertStore,
    notifier: NotifierLike | None,
    rules: NotificationSettings,
    today: date,
    report: AlertReport,
    send_digest: bool,
    status_line: str,
) -> list[str]:
    """依分數發送推播，回傳發出的訊息。沒有 notifier 時只計算，不改變推播狀態。"""
    instant, digest_items, baseline_items, skipped = [], [], [], []
    for n in store.pending_notifications():
        score = n["job"]["score"]
        if score is None:
            continue  # 還沒評分（預算或上限），留到下次
        if n["job"]["closed_at"] or score < rules.digest_min_score:
            skipped.append(n)
        elif n["baseline"]:
            (baseline_items if score >= rules.baseline_min_score else skipped).append(n)
        elif score >= rules.instant_min_score:
            instant.append(n)
        else:
            digest_items.append(n)

    messages: list[str] = []

    def send(text: str, ids: list[int]) -> None:
        messages.append(text)
        if notifier:
            notifier.send(text)
            if ids:
                store.mark_notifications(ids, "sent")

    if report.budget_hit and report.scored:  # 這次執行中途用完預算才通知，避免每次執行都重複提醒
        send(format_budget_stop(report.month_cost_usd, report.budget_usd, report.unscored), [])
    for n in sorted(instant, key=lambda n: -n["job"]["score"]):
        send(_job_message(n, today), [n["id"]])
        report.instant_sent += 1
    if baseline_items:
        ordered = sorted(baseline_items, key=lambda n: -n["job"]["score"])
        high = sum(n["job"]["score"] >= rules.instant_min_score for n in ordered)
        send(format_baseline_jobs([_job_line(n, today) for n in ordered], high, rules.instant_min_score), [n["id"] for n in ordered])
        report.baseline_listed = len(ordered)
    if send_digest:
        ordered = sorted(digest_items, key=lambda n: -n["job"]["score"])
        lines = [_job_line(n, today) for n in ordered]
        send(format_digest(lines, today, rules.digest_min_score, rules.instant_min_score, status_line), [n["id"] for n in ordered])
        report.digest_sent = True
        report.digest_listed = len(ordered)
    if notifier and skipped:
        store.mark_notifications([n["id"] for n in skipped], "skipped")
    report.skipped = len(skipped)
    return messages


def _job_message(n: dict[str, Any], today: date) -> str:
    job, detail = n["job"], n["job"]["score_detail"] or {}
    return format_job(
        kind=n["kind"],
        title=job["title"],
        company=job["company"],
        location=job["location"],
        url=job["url"],
        posted_date=date.fromisoformat(job["posted_date"]) if job["posted_date"] else None,
        today=today,
        score=job["score"],
        salary=job["salary"],
        via_agency=job["via_agency"],
        applicants=job["applicants"],
        fit_points=detail.get("fit_points"),
        gaps=detail.get("gaps"),
        cv_version=detail.get("cv_version"),
        red_flags=detail.get("red_flags"),
    )


def _job_line(n: dict[str, Any], today: date) -> str:
    job = n["job"]
    return format_job_line(
        kind=n["kind"],
        score=job["score"],
        title=job["title"],
        company=job["company"],
        url=job["url"],
        posted_date=date.fromisoformat(job["posted_date"]) if job["posted_date"] else None,
        today=today,
        salary=job["salary"],
        via_agency=job["via_agency"],
    )


def usage_row(job_pk: int, outcome: ScoreOutcome) -> dict[str, Any]:
    u = outcome.usage
    return {
        "job": job_pk,
        "model": u.model,
        "service_tier": u.service_tier,
        "input_tokens": u.input_tokens,
        "cached_tokens": u.cached_tokens,
        "cache_write_tokens": u.cache_write_tokens,
        "output_tokens": u.output_tokens,
        "reasoning_tokens": u.reasoning_tokens,
        "cost_usd": round(u.cost_usd, 6),
    }


def _batches(items: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
