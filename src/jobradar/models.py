from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

AtsType = Literal["workday", "greenhouse", "lever", "ashby", "generic"]


class Source(BaseModel):
    """sources.yaml 裡的一個來源。"""

    # 拼錯欄位名稱（例如 keyword 少了 s）直接報錯，避免設定悄悄失效
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    company: str = Field(min_length=1)
    url: HttpUrl
    locations: list[str] = []
    keywords: list[str] = []
    exclude: list[str] = []
    type: AtsType | None = None  # 不填則依網址自動辨識


class Job(BaseModel):
    """各 adapter 統一輸出的職缺格式。"""

    source: str
    ats: AtsType
    company: str
    job_id: str
    title: str
    location: str | None = None
    url: str
    posted_date: date | None = None
    description: str | None = None  # 清單階段通常沒有，fetch_detail 後才補上
