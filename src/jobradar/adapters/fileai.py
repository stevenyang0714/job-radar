"""file.ai 官網職缺（https://www.file.ai/careers）。

職缺直接寫在 Webflow 頁面上，每張卡片有職稱、部門、聘僱類型、工作型態、地點，
完整說明是 Google Drive 上的 PDF，申請連到 Google 表單。官網改版時要跟著調整這裡的解析方式。
"""

import html
import io
import re

from pypdf import PdfReader

from jobradar.adapters.base import Adapter, UnexpectedResponse
from jobradar.models import Job, Source

_CARD = re.compile(r'role="listitem"[^>]*class="career-item[^"]*"(.*?)(?=role="listitem"|w-pagination-wrapper)', re.S)
_DRIVE_ID = re.compile(r"/file/d/([A-Za-z0-9_-]+)")


def _field(card: str, pattern: str) -> str | None:
    match = re.search(pattern + r"\s*>([^<]+)<", card)
    return html.unescape(match.group(1)).strip() if match else None


def _link_before(card: str, label: str) -> str | None:
    """按鈕的連結；有些職缺的連結是 # 之類的空連結，視為沒有。"""
    match = re.search(r'href="([^"]+)"[^>]*>\s*<div>\s*' + label, card)
    link = html.unescape(match.group(1)) if match else None
    return link if link and link.startswith("http") else None


def parse_cards(page: str) -> list[dict[str, str | None]]:
    cards = []
    for card in _CARD.findall(page):
        cards.append(
            {
                "title": _field(card, r'class="career-heading"'),
                "department": _field(card, r'class="career-text-grey"'),
                "type": _field(card, r'fs-list-field="type"'),
                "arrangement": _field(card, r'fs-list-field="arrange"'),
                "location": _field(card, r'fs-list-field="make"'),
                "description_url": _link_before(card, "Job Description"),
                "apply_url": _link_before(card, "Apply"),
            }
        )
    return [c for c in cards if c["title"]]


class FileAiAdapter(Adapter):
    ats = "fileai"

    def list_jobs(self, source: Source) -> list[Job]:
        page = self.request("GET", str(source.url), headers={"Accept": "text/html"}).text
        cards = parse_cards(page)
        if not cards and "career-item" not in page:
            raise UnexpectedResponse("file.ai 職缺頁格式改變，找不到職缺卡片")
        jobs = []
        for card in cards:
            link = card["description_url"] or card["apply_url"] or str(source.url)
            drive = _DRIVE_ID.search(link)
            jobs.append(
                Job(
                    source=source.name,
                    ats=self.ats,
                    company=source.company,
                    job_id=drive.group(1) if drive else f"{card['title']}|{card['location']}",
                    title=card["title"],
                    location=card["location"],
                    url=link,
                    employment_type=", ".join(filter(None, [card["type"], card["arrangement"]])) or None,
                )
            )
        return jobs

    def fetch_detail(self, job: Job) -> Job:
        """下載 Google Drive 上的職缺說明 PDF 並轉成文字。"""
        drive = _DRIVE_ID.search(job.url)
        if not drive:
            return job.model_copy(update={"description": job.title})
        content = self.request("GET", "https://drive.google.com/uc", params={"export": "download", "id": drive.group(1)}).content
        if not content.startswith(b"%PDF"):
            raise UnexpectedResponse(f"file.ai 職缺說明不是 PDF（{job.url}）")
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
        return job.model_copy(update={"description": " ".join(text.split())})
