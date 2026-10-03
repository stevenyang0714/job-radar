"""Adapter 介面：把各家招募系統的資料轉成統一的 Job 格式。"""

import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import httpx

from jobradar.models import AtsType, Job, Source

_RETRY_STATUS = {429, 500, 502, 503, 504}


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
                    return resp.json()
            time.sleep(2**attempt)
        raise AssertionError("unreachable")
