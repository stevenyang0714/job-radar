"""Y Combinator 官方職缺頁（例如 https://www.ycombinator.com/jobs/location/singapore）。

這是搜尋型來源：頁面列出 YC 新創在該地點最新的職缺（約 50 筆），公司各不相同。
YC 網站用 Inertia.js，資料放在 HTML 的 data-page 屬性裡（JSON）。robots.txt 允許這些頁面。
"""

import html
import json
import re
from datetime import date, timedelta
from typing import Any

from jobradar.adapters.base import Adapter, UnexpectedResponse
from jobradar.models import Job, Source
from jobradar.text import join_unique

BASE = "https://www.ycombinator.com"
_DATA_PAGE = re.compile(r'data-page="([^"]+)"')
_RELATIVE = re.compile(r"(\d+)\s*(minute|hour|day|week|month|year)", re.IGNORECASE)
_UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}


def relative_date(text: str | None, today: date | None = None) -> date | None:
    """把「3 days」「2 weeks」這類相對時間換算成日期（月、年為概略值）。"""
    if not text:
        return None
    today = today or date.today()
    if match := _RELATIVE.search(text):
        return today - timedelta(days=int(match.group(1)) * _UNIT_DAYS[match.group(2).lower()])
    return today if re.search(r"today|just now|hour|minute", text, re.IGNORECASE) else None


class YCombinatorAdapter(Adapter):
    ats = "ycombinator"

    def list_jobs(self, source: Source) -> list[Job]:
        return [_to_job(source, item) for item in self._props(str(source.url)).get("jobPostings") or []]

    def fetch_detail(self, job: Job) -> Job:
        info = self._props(job.url).get("job") or {}
        return job.model_copy(update={"description": "\n\n".join(filter(None, [_header(info), info.get("description")]))})

    def _props(self, url: str) -> dict[str, Any]:
        page = self.request("GET", url, headers={"Accept": "text/html"}).text
        if not (match := _DATA_PAGE.search(page)):
            raise UnexpectedResponse(f"YC 頁面格式改變，找不到 data-page（{url}）")
        return json.loads(html.unescape(match.group(1))).get("props") or {}


def _header(item: dict[str, Any]) -> str:
    """公司簡介、簽證條件、經驗要求放在內容開頭，評分時一併參考。"""
    lines = [
        f"公司：{item.get('companyName')}（YC {item.get('companyBatchName')}）— {item.get('companyOneLiner') or ''}",
        f"簽證：{item['visa']}" if item.get("visa") else None,
        f"經驗要求：{item['minExperience']}" if item.get("minExperience") else None,
        f"職能：{item.get('prettyRole') or ''} {item.get('roleSpecificType') or ''}".strip(),
    ]
    return "\n".join(line for line in lines if line)


def _to_job(source: Source, item: dict[str, Any]) -> Job:
    return Job(
        source=source.name,
        ats="ycombinator",
        company=item.get("companyName") or "YC startup",
        job_id=str(item["id"]),
        title=item["title"].strip(),
        location=join_unique([item.get("location")]),
        url=BASE + item["url"],
        posted_date=relative_date(item.get("createdAt")),
        salary=f"{item['salaryRange']}（年薪）" if item.get("salaryRange") else None,
        employment_type=item.get("type"),
    )
