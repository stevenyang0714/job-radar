"""Lever 招募網站（jobs.lever.co）。

公開 Postings API：GET https://api.lever.co/v0/postings/{company}?mode=json
一次回傳全部職缺與完整內容，不需要逐筆抓。
"""

from datetime import datetime, timezone
from urllib.parse import urlsplit

from jobradar.adapters.base import Adapter, first_path_segment
from jobradar.models import Job, Source
from jobradar.text import html_to_text, join_unique


class LeverAdapter(Adapter):
    ats = "lever"

    def list_jobs(self, source: Source) -> list[Job]:
        url = str(source.url)
        company = first_path_segment(url, "https://jobs.lever.co/公司")
        api_host = "api.eu.lever.co" if ".eu." in (urlsplit(url).hostname or "") else "api.lever.co"
        data = self.request_json("GET", f"https://{api_host}/v0/postings/{company}", params={"mode": "json"})

        jobs = []
        for item in data:
            categories = item.get("categories") or {}
            places = [categories.get("location"), *(categories.get("allLocations") or [])]
            created = item.get("createdAt")
            jobs.append(
                Job(
                    source=source.name,
                    ats=self.ats,
                    company=source.company,
                    job_id=item["id"],
                    title=item["text"].strip(),
                    location=join_unique(places),
                    url=item["hostedUrl"],
                    posted_date=datetime.fromtimestamp(created / 1000, tz=timezone.utc).date() if created else None,
                    description=_description(item),
                )
            )
        return jobs


def _description(item: dict) -> str:
    """Lever 把內容拆成開頭說明、數個條列區塊（lists）與結尾說明。"""
    parts = [item.get("descriptionPlain")]
    for section in item.get("lists") or []:
        parts += [section.get("text"), html_to_text(f"<ul>{section.get('content') or ''}</ul>")]
    parts.append(item.get("additionalPlain"))
    return "\n\n".join(p.strip() for p in parts if p and p.strip())
