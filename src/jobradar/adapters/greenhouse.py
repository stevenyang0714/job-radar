"""Greenhouse 招募網站（boards.greenhouse.io、job-boards.greenhouse.io）。

公開 Job Board API：GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
一次回傳全部職缺與完整內容，不需要逐筆抓。
"""

import html
from urllib.parse import urlsplit

from jobradar.adapters.base import Adapter, first_path_segment, iso_date
from jobradar.models import Job, Source
from jobradar.text import html_to_text, join_unique


class GreenhouseAdapter(Adapter):
    ats = "greenhouse"

    def list_jobs(self, source: Source) -> list[Job]:
        url = str(source.url)
        token = first_path_segment(url, "https://boards.greenhouse.io/公司")
        api_host = "boards-api.eu.greenhouse.io" if ".eu." in (urlsplit(url).hostname or "") else "boards-api.greenhouse.io"
        data = self.request_json("GET", f"https://{api_host}/v1/boards/{token}/jobs", params={"content": "true"})

        jobs = []
        for item in data.get("jobs") or []:
            # location 常只寫 Hybrid / Remote，實際城市在 offices 裡
            offices = item.get("offices") or []
            places = [(item.get("location") or {}).get("name")]
            places += [part for office in offices for part in (office.get("name"), office.get("location"))]
            jobs.append(
                Job(
                    source=source.name,
                    ats=self.ats,
                    company=source.company,
                    job_id=str(item["id"]),
                    title=item["title"].strip(),
                    location=join_unique(places),
                    url=item["absolute_url"],
                    posted_date=iso_date(item.get("first_published")),
                    # content 是跳脫過的 HTML（&lt;p&gt;），要先還原
                    description=html_to_text(html.unescape(item.get("content") or "")),
                )
            )
        return jobs
