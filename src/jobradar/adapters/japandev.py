"""Japan Dev「不需日文」職缺（https://japan-dev.com/jobs-in-japan-for-english-speakers）。

這是搜尋型來源：列出在日本、不需要日文的最新職缺（約 60 筆，以工程職為主），公司各不相同。
網站用 Nuxt，資料以壓縮格式放在 __NUXT_DATA__ 裡：物件的欄位值是陣列索引，要還原才讀得到。
"""

import json
import re
from collections.abc import Callable
from typing import Any

from jobradar.adapters.base import Adapter, UnexpectedResponse, find_job_posting, iso_date
from jobradar.models import Job, Source
from jobradar.text import html_to_text

BASE = "https://japan-dev.com"
_NUXT_DATA = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef"}


def nuxt_resolver(data: list[Any]) -> Callable[[int], Any]:
    """還原 Nuxt 的壓縮資料（devalue 格式）：回傳「索引 → 還原後的值」的函式。"""
    memo: dict[int, Any] = {}

    def get(index: Any) -> Any:
        if not isinstance(index, int) or index < 0:
            return None
        if index in memo:
            return memo[index]
        value = data[index]
        if isinstance(value, list):
            if value and isinstance(value[0], str) and value[0] in _WRAPPERS:
                memo[index] = get(value[1]) if len(value) > 1 else None
                return memo[index]
            items: list[Any] = []
            memo[index] = items
            items.extend(get(i) for i in value)
            return items
        if isinstance(value, dict):
            obj: dict[str, Any] = {}
            memo[index] = obj
            obj.update({k: get(i) for k, i in value.items()})
            return obj
        memo[index] = value
        return value

    return get


def extract_jobs(page: str) -> list[dict[str, Any]]:
    if not (match := _NUXT_DATA.search(page)):
        raise UnexpectedResponse("Japan Dev 頁面格式改變，找不到 __NUXT_DATA__")
    data = json.loads(match.group(1))
    get = nuxt_resolver(data)
    jobs: dict[str, dict[str, Any]] = {}
    for i, value in enumerate(data):
        # 同一個職缺會出現兩種包裝；只取欄位直接指向純值的那一種
        if isinstance(value, dict) and "japanese_level_enum" in value and "slug" in value and isinstance(data[value["id"]], (str, int)):
            job = get(i)
            jobs.setdefault(str(job["id"]), job)
    return list(jobs.values())


class JapanDevAdapter(Adapter):
    ats = "japandev"

    def list_jobs(self, source: Source) -> list[Job]:
        page = self.request("GET", str(source.url), headers={"Accept": "text/html"}).text
        return [_to_job(source, item) for item in extract_jobs(page) if item.get("japanese_level_enum") == "japanese_level_not_required"]

    def fetch_detail(self, job: Job) -> Job:
        """職缺頁的 JobPosting 結構化資料有完整說明；公司的語言環境說明也一併附上。"""
        page = self.request("GET", job.url, headers={"Accept": "text/html"}).text
        posting = find_job_posting(page) or {}
        policy = None
        if match := _NUXT_DATA.search(page):
            data = json.loads(match.group(1))
            get = nuxt_resolver(data)
            company = next((get(i) for i, v in enumerate(data) if isinstance(v, dict) and "japanese_policy" in v), None)
            policy = (company or {}).get("japanese_policy")
        parts = ["日文要求：不需要", f"公司語言環境：{html_to_text(policy)}" if policy else None, html_to_text(posting.get("description") or "")]
        return job.model_copy(update={"description": "\n\n".join(p for p in parts if p)})


def _to_job(source: Source, item: dict[str, Any]) -> Job:
    company = item.get("company") or {}
    low, high = item.get("salary_min"), item.get("salary_max")
    salary = f"JPY {int(low):,}–{int(high):,} / 年" if low and high else None
    return Job(
        source=source.name,
        ats="japandev",
        company=item.get("company_name") or company.get("name") or "?",
        job_id=str(item["id"]),
        title=item["title"].strip(),
        location=item.get("location") or "Japan",
        url=f"{BASE}/jobs/{company.get('slug')}/{item['slug']}",
        posted_date=iso_date(item.get("published_at") or item.get("created_at")),
        salary=salary,
        employment_type=(item.get("employment_type") or "").removeprefix("employment_type_").replace("_", " ") or None,
    )
