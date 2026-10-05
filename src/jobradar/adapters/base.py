"""Adapter 介面：把各家招募系統的資料轉成統一的 Job 格式。"""

import json
import re
import time
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from jobradar.models import AtsType, Job, Source

_RETRY_STATUS = {429, 500, 502, 503, 504}


class SourceUnavailable(Exception):
    """網站暫時無法使用（例如例行維護），通常過幾小時就會恢復。"""


class UnexpectedResponse(Exception):
    """網站回傳的不是預期的格式，可能是改版或被導到其他頁面。"""


class Adapter(ABC):
    ats: ClassVar[AtsType]
    # True：list_jobs 已依 source.locations 篩過地區，不需要再比對地點文字
    handles_location: ClassVar[bool] = False

    def __init__(self, client: httpx.Client, delay: float = 0.3, retries: int = 3) -> None:
        self.client = client
        self.delay = delay  # 每個請求之間的間隔，避免對網站造成負擔
        self.retries = retries
        self.notes: list[str] = []  # 執行過程中值得讓使用者知道的資訊

    @abstractmethod
    def list_jobs(self, source: Source) -> list[Job]:
        """抓職缺清單。清單通常沒有完整內容，description 可以是 None。"""

    def fetch_detail(self, job: Job) -> Job:
        """補上 description 等清單沒有的欄位；清單已含完整內容的系統不需覆寫。"""
        return job

    def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        """發送請求並解析 JSON；連線錯誤、429、5xx 會等待後重試。"""
        return _parse_json(self.request(method, url, **kwargs))

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """發送請求；連線錯誤、429、5xx 會等待後重試，其他錯誤狀態直接拋出。"""
        for attempt in range(1, self.retries + 1):
            time.sleep(self.delay)
            try:
                resp = self.client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt == self.retries:
                    raise
            else:
                if resp.status_code not in _RETRY_STATUS or attempt == self.retries:
                    resp.raise_for_status()
                    return resp
            time.sleep(2**attempt)
        raise AssertionError("unreachable")


def iso_date(text: str | None) -> date | None:
    """解析 2026-09-30 或 2026-09-07T21:25:04-04:00 這類 ISO 格式的日期。"""
    try:
        return datetime.fromisoformat(text).date() if text else None
    except ValueError:
        return None


_LD_JSON = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def find_job_posting(page: str) -> dict[str, Any] | None:
    """網頁裡的 schema.org JobPosting 結構化資料（很多招募頁為了搜尋引擎都會附上）。"""
    for block in _LD_JSON.findall(page):
        try:
            data = json.loads(block.strip(), strict=False)  # 有些網站的字串裡夾著未跳脫的換行
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def first_path_segment(url: str, example: str) -> str:
    """招募網址的第一段路徑通常就是公司代號，例如 jobs.lever.co/acme 的 acme。"""
    segments = [s for s in urlsplit(url).path.split("/") if s]
    if not segments:
        raise ValueError(f"無法從網址取得公司代號：{url}（格式應為 {example}）")
    return segments[0]


def _parse_json(resp: httpx.Response) -> Any:
    if resp.history and "maintenance" in str(resp.url).lower():
        raise SourceUnavailable(f"網站維護中（被導向 {resp.url}）")
    content_type = resp.headers.get("content-type", "")
    if "json" not in content_type:
        raise UnexpectedResponse(f"預期 JSON，收到 {content_type or '未知格式'}（{resp.url}）")
    return resp.json()
