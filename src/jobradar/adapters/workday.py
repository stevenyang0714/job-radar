"""Workday 招募網站（*.myworkdayjobs.com、*.myworkdaysite.com）。

使用 Workday 網站前端本身呼叫的公開 JSON API：
- 清單：POST {api}/jobs，每頁最多 20 筆，可用網站內建的篩選選項（facets）縮小範圍
- 單筆：GET {api}{externalPath}
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

from jobradar.adapters.base import Adapter, iso_date
from jobradar.filters import expand_locations, find_term
from jobradar.models import Job, Source
from jobradar.text import html_to_text

PAGE_SIZE = 20
MAX_JOBS = 2000

_LOCALE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
_MULTI_LOCATIONS = re.compile(r"^\d+ Locations$", re.IGNORECASE)
_JOB_ID = re.compile(r"_([^_/]+)$")
_POSTED_DAYS = re.compile(r"Posted (\d+) Days? Ago", re.IGNORECASE)


@dataclass(frozen=True)
class WorkdaySite:
    host: str
    tenant: str
    site: str
    public_base: str  # 職缺網址的前綴，後面接 externalPath

    @property
    def api_base(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"


def parse_site(url: str) -> WorkdaySite:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if segments and _LOCALE.match(segments[0]):
        segments = segments[1:]
    if host.endswith(".myworkdayjobs.com") and segments:
        tenant, site = host.split(".")[0], segments[0]
        return WorkdaySite(host, tenant, site, f"https://{host}/{site}")
    if host.endswith(".myworkdaysite.com") and len(segments) >= 3 and segments[0] == "recruiting":
        tenant, site = segments[1], segments[2]
        return WorkdaySite(host, tenant, site, f"https://{host}/recruiting/{tenant}/{site}")
    raise ValueError(f"無法解析 Workday 網址：{url}（格式應為 https://公司.wd5.myworkdayjobs.com/網站名稱）")


@dataclass(frozen=True)
class _FacetValue:
    parameter: str
    descriptor: str
    id: str
    count: int


@dataclass
class LocationSelection:
    applied: dict[str, list[str]] = field(default_factory=dict)  # facet 參數 → 選項 id
    matched: list[str] = field(default_factory=list)  # 給使用者看的說明，例如 Singapore (34)
    missing: list[str] = field(default_factory=list)  # 找不到對應選項的地區


def _iter_facet_values(facets: Iterable[dict[str, Any]], parameter: str = "") -> Iterator[_FacetValue]:
    for facet in facets:
        param = facet.get("facetParameter", parameter)
        for value in facet.get("values") or []:
            if "values" in value:  # 巢狀群組，例如 locationMainGroup
                yield from _iter_facet_values([value], param)
            elif "id" in value:
                yield _FacetValue(param, value.get("descriptor", ""), value["id"], value.get("count", 0))


def match_location_facets(facets: list[dict[str, Any]], locations: list[str]) -> LocationSelection | None:
    """把 sources.yaml 的地區對應到 Workday 的篩選選項；網站沒有地區選項時回傳 None。"""
    candidates = [v for v in _iter_facet_values(facets) if re.search("location|country", v.parameter, re.IGNORECASE)]
    if not candidates:
        return None
    selection = LocationSelection()
    for location in locations:
        terms = expand_locations([location])
        hits = [v for v in candidates if find_term(terms, v.descriptor)]
        if not hits:
            selection.missing.append(location)
            continue
        # 同一個地區同時出現在國家與城市選項時，優先用國家
        chosen = [v for v in hits if "country" in v.parameter.lower()] or hits
        param = chosen[0].parameter
        ids = selection.applied.setdefault(param, [])
        for value in chosen:
            if value.parameter == param and value.id not in ids:
                ids.append(value.id)
                selection.matched.append(f"{value.descriptor} ({value.count})")
    return selection


def _valid(postings: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """略過殘缺項目：Workday 偶爾會回傳只有編號、沒有職稱與網址的資料（例如下架到一半的職缺）。"""
    return [p for p in postings or [] if p.get("externalPath") and p.get("title")]


def _clean_location(text: str | None) -> str | None:
    return " ".join(text.split()) if text else None


def _posted_date(text: str | None, today: date | None = None) -> date | None:
    """把清單上的「Posted 3 Days Ago」換算成日期；30+ 天前的無法精確換算。"""
    if not text:
        return None
    today = today or date.today()
    lowered = text.lower()
    if "today" in lowered:
        return today
    if "yesterday" in lowered:
        return today - timedelta(days=1)
    if (match := _POSTED_DAYS.search(text)) and "+" not in text:
        return today - timedelta(days=int(match.group(1)))
    return None


class WorkdayAdapter(Adapter):
    ats = "workday"
    handles_location = True

    def list_jobs(self, source: Source) -> list[Job]:
        site = parse_site(str(source.url))
        first = self._search(site, {}, 0)
        if not source.locations:
            return self._to_jobs(source, site, self._collect(site, {}, first))

        selection = match_location_facets(first.get("facets") or [], source.locations)
        if selection is None:
            self.notes.append("這個 Workday 網站沒有地區篩選選項，改為抓全部職缺後比對地點")
            return self._filter_locations(source, self._to_jobs(source, site, self._collect(site, {}, first)))

        if selection.matched:
            self.notes.append("地區篩選：" + "、".join(selection.matched))
        if selection.missing:
            self.notes.append("⚠️ 找不到地區選項（可能目前沒有職缺，或名稱不同）：" + "、".join(selection.missing))

        # 不同 facet 參數之間是「且」的關係，所以分開查詢再合併
        postings: dict[str, dict[str, Any]] = {}
        for param, ids in selection.applied.items():
            facets = {param: ids}
            for posting in self._collect(site, facets, self._search(site, facets, 0)):
                postings.setdefault(posting["externalPath"], posting)
        return self._to_jobs(source, site, postings.values())

    def fetch_detail(self, job: Job) -> Job:
        site = parse_site(job.url)
        path = job.url.removeprefix(site.public_base)
        info = self.request_json("GET", site.api_base + path)["jobPostingInfo"]
        locations = [info.get("location"), *(info.get("additionalLocations") or [])]
        return job.model_copy(
            update={
                "description": html_to_text(info.get("jobDescription") or ""),
                "location": " / ".join(filter(None, map(_clean_location, locations))) or job.location,
                "posted_date": iso_date(info.get("startDate")) or job.posted_date,
            }
        )

    def _search(self, site: WorkdaySite, facets: dict[str, list[str]], offset: int) -> dict[str, Any]:
        body = {"appliedFacets": facets, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
        return self.request_json("POST", f"{site.api_base}/jobs", json=body)

    def _collect(self, site: WorkdaySite, facets: dict[str, list[str]], first: dict[str, Any]) -> list[dict[str, Any]]:
        """從第一頁開始翻頁。Workday 只有第一頁的 total 可靠，翻過頭時會重複回傳舊資料。"""
        postings = _valid(first.get("jobPostings"))
        seen = {p["externalPath"] for p in postings}
        total = first.get("total") or 0
        if total > MAX_JOBS:
            self.notes.append(f"⚠️ 共 {total} 筆，只抓前 {MAX_JOBS} 筆")
        while len(postings) < min(total, MAX_JOBS):
            page = _valid(self._search(site, facets, len(postings)).get("jobPostings"))
            new = [p for p in page if p["externalPath"] not in seen]
            if not new:
                break
            postings += new
            seen.update(p["externalPath"] for p in new)
        return postings

    def _to_jobs(self, source: Source, site: WorkdaySite, postings: Iterable[dict[str, Any]]) -> list[Job]:
        jobs = []
        for posting in postings:
            path = posting["externalPath"]
            match = _JOB_ID.search(path)
            jobs.append(
                Job(
                    source=source.name,
                    ats=self.ats,
                    company=source.company,
                    job_id=match.group(1) if match else path,
                    title=posting["title"].strip(),
                    location=_clean_location(posting.get("locationsText")),
                    url=site.public_base + path,
                    posted_date=_posted_date(posting.get("postedOn")),
                )
            )
        return jobs

    def _filter_locations(self, source: Source, jobs: list[Job]) -> list[Job]:
        terms = expand_locations(source.locations)
        kept = []
        for job in jobs:
            if job.location and _MULTI_LOCATIONS.match(job.location):
                job = self.fetch_detail(job)  # 清單只寫「N Locations」，要看詳細資料才知道地點
            if find_term(terms, job.location):
                kept.append(job)
        return kept
