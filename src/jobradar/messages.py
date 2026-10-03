"""Telegram 訊息內容（HTML 格式）。"""

from dataclasses import dataclass
from datetime import date
from html import escape

EVERGREEN_AFTER_DAYS = 365  # 開超過一年的職缺可能是常年招募


@dataclass(frozen=True)
class SourceWarning:
    company: str
    message: str


@dataclass(frozen=True)
class BaselineSummary:
    company: str
    total: int
    passed: int
    new: int
    long_open: int


def format_warnings(warnings: list[SourceWarning]) -> str:
    lines = ["⚠️ <b>job-radar 抓取警告</b>"]
    lines += [f"• <b>{escape(w.company)}</b>：{escape(w.message)}" for w in warnings]
    return "\n".join(lines)


def format_baseline(summaries: list[BaselineSummary]) -> str:
    lines = ["📋 <b>初始盤點</b>（第一次執行的來源）"]
    for s in summaries:
        lines.append(
            f"• <b>{escape(s.company)}</b>：{s.total} 筆，通過硬篩 {s.passed} 筆"
            f"｜🆕 一週內 {s.new}｜⏳ 4 個月以上 {s.long_open}"
        )
    lines.append("\n符合兩種條件的職缺會在 AI 評分後，推播高分的給你。")
    return "\n".join(lines)


def describe_age(days: int) -> str:
    if days <= 0:
        return "今天"
    if days < 30:
        return f"{days} 天前"
    return f"{days // 30} 個月前"


def format_job(
    *,
    kind: str,
    title: str,
    company: str,
    url: str,
    posted_date: date | None,
    today: date,
    location: str | None = None,
    score: int | None = None,
    salary: str | None = None,
    via_agency: bool = False,
    applicants: int | None = None,
    fit_points: list[str] | None = None,
    gaps: list[str] | None = None,
) -> str:
    days = (today - posted_date).days if posted_date else None
    if kind == "long_open":
        label = f"⏳ <b>已開 {days // 30 if days else '?'} 個月未關</b>"
        if days and days >= EVERGREEN_AFTER_DAYS:
            label += "（可能是常年招募）"
    else:
        label = "🆕 <b>新刊登</b>" + (f"（{describe_age(days)}）" if days is not None else "")
    if score is not None:
        label += f"｜<b>{score} 分</b>"

    lines = [label, f"<b>{escape(title)}</b>", escape(" · ".join(filter(None, [company, location])))]
    extras = [
        f"💰 {salary}" if salary else None,
        "仲介刊登" if via_agency else None,
        f"應徵 {applicants} 人" if applicants is not None else None,
    ]
    if any(extras):
        lines.append("｜".join(escape(e) for e in extras if e))
    if fit_points:
        lines.append("✅ 符合：" + escape(" / ".join(fit_points)))
    if gaps:
        lines.append("⚠️ 缺口：" + escape(" / ".join(gaps)))
    lines.append(f'🔗 <a href="{escape(url, quote=True)}">查看職缺</a>')
    return "\n".join(lines)
