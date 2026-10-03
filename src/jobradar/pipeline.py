"""單一來源的處理流程。"""

from collections.abc import Iterable
from dataclasses import dataclass

from jobradar.adapters import Adapter
from jobradar.detect import detect_from_url
from jobradar.filters import Verdict, screen_detail, screen_listing
from jobradar.models import AtsType, Job, Source


@dataclass
class Screened:
    job: Job
    verdict: Verdict


def resolve_ats(source: Source) -> AtsType:
    return source.type or detect_from_url(str(source.url)) or "generic"


def screen_jobs(adapter: Adapter, source: Source, jobs: Iterable[Job]) -> list[Screened]:
    """先用清單資料篩地區與排除詞，通過的才抓完整內容比對關鍵字，減少對網站的請求。"""
    results = []
    for job in jobs:
        verdict = screen_listing(job, source, check_location=not adapter.handles_location)
        if verdict is None:
            if job.description is None:
                job = adapter.fetch_detail(job)
            verdict = screen_detail(job, source)
        results.append(Screened(job, verdict))
    return results
