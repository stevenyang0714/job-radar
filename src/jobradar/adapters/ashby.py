"""Ashby 招募網站（jobs.ashbyhq.com）。

公開 Posting API：GET https://api.ashbyhq.com/posting-api/job-board/{name}
一次回傳全部職缺與完整內容，不需要逐筆抓。
"""

from jobradar.adapters.base import Adapter, first_path_segment, iso_date
from jobradar.models import Job, Source
from jobradar.text import html_to_text, join_unique


class AshbyAdapter(Adapter):
    ats = "ashby"

    def list_jobs(self, source: Source) -> list[Job]:
        name = first_path_segment(str(source.url), "https://jobs.ashbyhq.com/公司")
        data = self.request_json("GET", f"https://api.ashbyhq.com/posting-api/job-board/{name}")

        jobs = []
        for item in data.get("jobs") or []:
            if item.get("isListed") is False:
                continue
            country = ((item.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
            places = [item.get("location"), *(s.get("location") for s in item.get("secondaryLocations") or []), country]
            jobs.append(
                Job(
                    source=source.name,
                    ats=self.ats,
                    company=source.company,
                    job_id=item["id"],
                    title=item["title"].strip(),
                    location=join_unique(places),
                    url=item["jobUrl"],
                    posted_date=iso_date(item.get("publishedAt")),
                    description=(item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml") or "")).strip(),
                )
            )
        return jobs
