"""硬篩：地區、排除詞、關鍵字。

比對不分大小寫；英文詞以完整單字比對（AI 不會比對到 maintain），中文詞以字串比對。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from jobradar.models import Job, Source


@dataclass(frozen=True)
class Verdict:
    status: str  # passed / rejected_location / rejected_exclude / rejected_keyword
    reason: str = ""  # 命中的詞或不符的地點

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@lru_cache(maxsize=2048)
def term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term.strip())
    if term.isascii():
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def find_term(terms: Iterable[str], text: str | None) -> str | None:
    if not text:
        return None
    return next((t for t in terms if term_pattern(t).search(text)), None)


def screen_listing(job: Job, source: Source, *, check_location: bool = True) -> Verdict | None:
    """清單階段的篩選。回傳 None 代表通過，要抓完整內容後再比對關鍵字。"""
    if check_location and source.locations and not find_term(source.locations, job.location):
        return Verdict("rejected_location", job.location or "未標示地點")
    if term := find_term(source.exclude, job.title):
        return Verdict("rejected_exclude", term)
    return None


def screen_detail(job: Job, source: Source) -> Verdict:
    """關鍵字比對職稱與職缺內容；沒有設定關鍵字則全部通過。"""
    if not source.keywords:
        return Verdict("passed")
    if term := find_term(source.keywords, f"{job.title}\n{job.description or ''}"):
        return Verdict("passed", term)
    return Verdict("rejected_keyword")
