from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

AtsType = Literal["workday", "greenhouse", "lever", "ashby", "mycareersfuture", "generic"]


class Source(BaseModel):
    """sources.yaml 裡的一個來源。"""

    # 拼錯欄位名稱（例如 keyword 少了 s）直接報錯，避免設定悄悄失效
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    company: str = Field(min_length=1)
    url: HttpUrl
    group: str | None = None  # sources.yaml groups 裡的分組，例如 healthcare
    locations: list[str] = []
    keywords: list[str] = []
    exclude: list[str] = []
    exclude_description: list[str] = []  # 職缺內容含這些詞就略過，例如「限新加坡公民」
    type: AtsType | None = None  # 不填則依網址自動辨識
    # 搜尋型來源（例如 MyCareersFuture）才用得到
    searches: list[str] = []
    min_salary: int | None = None  # 月薪上限至少要達到這個數字


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
    salary: str | None = None  # 有公開薪資才有，例如「SGD 13,000–16,000 / 月」
    employment_type: str | None = None  # 例如「Permanent, Full Time」
    via_agency: bool = False  # 由仲介刊登
    applicants: int | None = None  # 應徵人數（MyCareersFuture 才有）
