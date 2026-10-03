"""MyCareersFuture（新加坡政府求職網站）。

新加坡公司為外籍人士申請 EP 前，大多數職缺須先在這裡刊登（Fair Consideration Framework），
而且每個職缺都標示月薪範圍，適合用來找「可能辦 EP、薪資達標」的職缺。

這是搜尋型來源：依 sources.yaml 的 searches 逐一搜尋，職缺的公司各不相同。
- 搜尋：POST https://api.mycareersfuture.gov.sg/v2/search?limit=100&page=N
- 單筆：GET  https://api.mycareersfuture.gov.sg/v2/jobs/{uuid}
"""

import re
from typing import Any

from jobradar.adapters.base import Adapter, iso_date
from jobradar.models import Job, Source
from jobradar.text import html_to_text

API = "https://api.mycareersfuture.gov.sg/v2"
PAGE_SIZE = 100
MAX_PER_SEARCH = 500

# 用自己名義刊登的大型仲介，不會勾選「代雇主刊登」，只能比對名稱
_AGENCY_NAME = re.compile(
    r"RECRUIT|STAFFING|TALENT|SEARCH|PERSONNEL|MANPOWER|HEADHUNT|CAREER|RESOURCES|"
    r"HAYS|MICHAEL PAGE|ROBERT WALTERS|MORGAN MCKINLEY|RANDSTAD|ADECCO|PERSOLKELLY|KELLY SERVICES|"
    r"EAMES|HUDSON|SPENCER STUART|ARGYLL SCOTT|ALLEGIS|IKAS|TANGSPAC",
    re.IGNORECASE,
)


class MyCareersFutureAdapter(Adapter):
    ats = "mycareersfuture"
    handles_location = True  # 全部都是新加坡的職缺

    def list_jobs(self, source: Source) -> list[Job]:
        if not source.searches:
            raise ValueError(f"{source.name} 是 MyCareersFuture 來源，需要在 sources.yaml 設定 searches")
        results: dict[str, dict[str, Any]] = {}
        for term in source.searches:
            for item in self._search(term, source.min_salary):
                results.setdefault(item["uuid"], item)

        jobs = []
        for item in results.values():
            salary = item.get("salary") or {}
            if source.min_salary and (salary.get("maximum") or 0) < source.min_salary:
                continue
            jobs.append(_to_job(source, item))
        return jobs

    def fetch_detail(self, job: Job) -> Job:
        info = self.request_json("GET", f"{API}/jobs/{job.job_id}")
        metadata = info.get("metadata") or {}
        return job.model_copy(
            update={
                "description": html_to_text(info.get("description") or ""),
                # 職缺 30 天到期後重新刊登，搜尋結果只有最近一次的日期；原始刊登日才看得出開了多久
                "posted_date": iso_date(metadata.get("originalPostingDate")) or job.posted_date,
                "applicants": metadata.get("totalNumberJobApplication"),
            }
        )

    def _search(self, term: str, min_salary: int | None) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"search": term, "sortBy": ["new_posting_date"]}
        if min_salary:
            body["salary"] = min_salary  # 網站的篩選依月薪上限
        items: list[dict[str, Any]] = []
        page = 0
        while len(items) < MAX_PER_SEARCH:
            data = self.request_json("POST", f"{API}/search", params={"limit": PAGE_SIZE, "page": page}, json=body)
            batch = data.get("results") or []
            items += batch
            if len(batch) < PAGE_SIZE or len(items) >= (data.get("total") or 0):
                break
            page += 1
        return items


def _to_job(source: Source, item: dict[str, Any]) -> Job:
    metadata = item.get("metadata") or {}
    posted = (item.get("postedCompany") or {}).get("name") or "?"
    hiring = (item.get("hiringCompany") or {}).get("name")
    on_behalf = bool(metadata.get("isPostedOnBehalf"))
    salary = item.get("salary") or {}
    types = [t.get("employmentType") for t in item.get("employmentTypes") or [] if t.get("employmentType")]
    return Job(
        source=source.name,
        ats="mycareersfuture",
        # 仲介代刊登且公開雇主名稱時，以實際雇主為公司
        company=hiring if on_behalf and hiring and not metadata.get("isHideHiringEmployerName") else posted,
        job_id=item["uuid"],
        title=item["title"].strip(),
        location="Singapore",
        url=metadata.get("jobDetailsUrl") or f"https://www.mycareersfuture.gov.sg/job/{item['uuid']}",
        posted_date=iso_date(metadata.get("newPostingDate")),
        salary=_format_salary(salary) if not metadata.get("isHideSalary") else None,
        employment_type=", ".join(types) or None,
        via_agency=on_behalf or bool(_AGENCY_NAME.search(posted)),
    )


def _format_salary(salary: dict[str, Any]) -> str | None:
    low, high = salary.get("minimum"), salary.get("maximum")
    if not high:
        return None
    period = {"Monthly": "月", "Annual": "年"}.get((salary.get("type") or {}).get("salaryType"), "月")
    return f"SGD {low:,}–{high:,} / {period}" if low else f"SGD {high:,} / {period}"
