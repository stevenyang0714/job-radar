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
    status: str  # passed / rejected_location / rejected_exclude / rejected_description / rejected_keyword
    reason: str = ""  # 命中的詞或不符的地點

    @property
    def passed(self) -> bool:
        return self.status == "passed"


# 職缺地點常只寫城市或代碼，例如 Greenhouse 只寫「Taipei」、MSD 寫「TWN-Taipei」
LOCATION_ALIASES = {
    "taiwan": ["Taipei", "New Taipei", "Hsinchu", "Taichung", "Tainan", "Kaohsiung", "Taoyuan", "TWN", "台灣", "臺灣", "台北", "臺北", "新竹", "台中", "臺中", "高雄", "台南", "臺南", "桃園"],
    "singapore": ["SGP", "新加坡"],
    "hong kong": ["HKG", "HKSAR", "Kowloon", "香港"],
    "australia": ["Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Canberra", "AUS", "NSW", "澳洲"],
    "japan": ["Tokyo", "Osaka", "Yokohama", "Kyoto", "Fukuoka", "Nagoya", "JPN", "日本", "東京", "大阪"],
}


def expand_locations(locations: Iterable[str]) -> list[str]:
    """把地區加上別名，例如 Taiwan → Taiwan、Taipei、Hsinchu…"""
    expanded = []
    for location in locations:
        expanded += [location, *LOCATION_ALIASES.get(location.strip().lower(), [])]
    return list(dict.fromkeys(expanded))


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


# 日文假名（平假名、片假名）。中文沒有假名，所以只會擋到日文寫的職缺
_KANA = re.compile(r"[぀-ゟ゠-ヿ]")
JAPANESE_TEXT_MIN_KANA = 20  # 內容裡假名超過這個數量，視為日文職缺（需要日文）


def is_japanese_text(text: str | None, min_kana: int = 1) -> bool:
    return bool(text) and len(_KANA.findall(text)) >= min_kana


def screen_listing(job: Job, source: Source, *, check_location: bool = True) -> Verdict | None:
    """清單階段的篩選。回傳 None 代表通過，要抓完整內容後再比對關鍵字。"""
    if check_location and source.locations and not find_term(expand_locations(source.locations), job.location):
        return Verdict("rejected_location", job.location or "未標示地點")
    if term := find_term(source.exclude, job.title):
        return Verdict("rejected_exclude", term)
    if is_japanese_text(job.title):
        return Verdict("rejected_description", "日文職缺")
    return None


def screen_detail(job: Job, source: Source) -> Verdict:
    """先檢查內容排除詞與日文內容，再用關鍵字比對職稱與職缺內容；沒有設定關鍵字則全部通過。"""
    if term := find_term(source.exclude_description, job.description):
        return Verdict("rejected_description", term)
    if is_japanese_text(job.description, JAPANESE_TEXT_MIN_KANA):
        return Verdict("rejected_description", "日文職缺")
    if not source.keywords:
        return Verdict("passed")
    if term := find_term(source.keywords, f"{job.title}\n{job.description or ''}"):
        return Verdict("passed", term)
    return Verdict("rejected_keyword")
