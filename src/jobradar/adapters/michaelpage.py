"""Michael Page Japan 英文職缺（https://www.michaelpage.co.jp/en/jobs?language[0]=en）。

這是仲介的搜尋型來源：每頁 30 筆，看不到實際雇主。網站的「英文」篩選很鬆，
很多職缺其實用日文寫，交給共用的日文職缺規則排除。職缺頁有 JobPosting 結構化資料（完整說明與刊登日）。
"""

import re
from urllib.parse import unquote

import httpx

from jobradar.adapters.base import Adapter, find_job_posting, iso_date
from jobradar.models import Job, Source
from jobradar.text import html_to_text

BASE = "https://www.michaelpage.co.jp"
MAX_PAGES = 20
_TILE = re.compile(r'<div about="(/en/job-detail/[^"]+)" class="job-tile[^"]*">(.*?)</li>', re.S)
_JOB_REF = re.compile(r"/ref/([A-Za-z0-9-]+)")


def _text(tile: str, css_class: str) -> str | None:
    match = re.search(rf'class="{css_class}[^"]*"[^>]*>(.*?)</div>', tile, re.S)
    return html_to_text(match.group(1)) or None if match else None


def parse_tiles(page: str) -> list[dict[str, str | None]]:
    tiles = []
    for href, tile in _TILE.findall(page):
        ref = _JOB_REF.search(href)
        tiles.append(
            {
                "href": href,
                "job_id": ref.group(1) if ref else unquote(href),
                "title": _text(tile, "job-title"),
                "location": _text(tile, "job-location"),
                "contract": _text(tile, "job-contract-type"),
                "salary": _text(tile, "job-salary"),
                "company_type": _text(tile, "job-company-type"),
            }
        )
    return [t for t in tiles if t["title"]]


class MichaelPageAdapter(Adapter):
    ats = "michaelpage"

    def list_jobs(self, source: Source) -> list[Job]:
        tiles: dict[str, dict[str, str | None]] = {}
        for page in range(MAX_PAGES):
            url = str(source.url) + (f"&page={page}" if page else "")
            try:
                batch = parse_tiles(self.request("GET", url, headers={"Accept": "text/html"}).text)
            except httpx.HTTPStatusError as e:
                if page and e.response.status_code == 404:  # 翻過最後一頁網站會回 404
                    break
                raise
            new = [t for t in batch if t["job_id"] not in tiles]
            if not new:
                break
            tiles.update((t["job_id"], t) for t in new)
        return [_to_job(source, t) for t in tiles.values()]

    def fetch_detail(self, job: Job) -> Job:
        posting = find_job_posting(self.request("GET", job.url, headers={"Accept": "text/html"}).text) or {}
        return job.model_copy(
            update={
                "description": html_to_text(posting.get("description") or "") or job.title,
                "posted_date": iso_date(posting.get("datePosted")) or job.posted_date,
            }
        )


def _to_job(source: Source, tile: dict[str, str | None]) -> Job:
    return Job(
        source=source.name,
        ats="michaelpage",
        # 仲介刊登看不到雇主；公司類型（例如 Japanese Corporate）放在名稱後面供參考
        company=f"{source.company}（{tile['company_type']}）" if tile["company_type"] else source.company,
        job_id=tile["job_id"] or "",
        title=tile["title"] or "",
        location=tile["location"] or "Japan",
        url=BASE + (tile["href"] or ""),
        salary=tile["salary"],
        employment_type=tile["contract"],
        via_agency=True,
    )
