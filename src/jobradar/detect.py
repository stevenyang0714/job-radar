"""判斷招募網址屬於哪一種招募系統。"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from jobradar.models import AtsType

_HOSTS: list[tuple[re.Pattern[str], AtsType]] = [
    (re.compile(r"\.myworkdayjobs\.com$|\.myworkdaysite\.com$"), "workday"),
    (re.compile(r"(^|\.)greenhouse\.io$"), "greenhouse"),
    (re.compile(r"^jobs\.(eu\.)?lever\.co$"), "lever"),
    (re.compile(r"^jobs\.ashbyhq\.com$"), "ashby"),
    (re.compile(r"(^|\.)mycareersfuture\.gov\.sg$"), "mycareersfuture"),
    (re.compile(r"(^|\.)ycombinator\.com$"), "ycombinator"),
    (re.compile(r"(^|\.)file\.ai$"), "fileai"),
    (re.compile(r"(^|\.)japan-dev\.com$"), "japandev"),
    (re.compile(r"(^|\.)michaelpage\.co\.jp$"), "michaelpage"),
]

# 公司自有網域的招募頁，常會嵌入招募系統的連結；只擷取到網站名稱，去掉登入頁、追蹤參數等
_EMBEDDED: list[tuple[re.Pattern[str], AtsType]] = [
    (
        re.compile(r"https://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?!wday/)[A-Za-z0-9_-]+"),
        "workday",
    ),
    (re.compile(r"https://(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?!embed)[A-Za-z0-9_-]+"), "greenhouse"),
    (re.compile(r"https://jobs\.(?:eu\.)?lever\.co/[A-Za-z0-9_.-]+"), "lever"),
    (re.compile(r"https://jobs\.ashbyhq\.com/[A-Za-z0-9_.%-]+"), "ashby"),
]


@dataclass(frozen=True)
class Detection:
    ats: AtsType
    url: str  # 建議填進 sources.yaml 的網址
    how: str  # 判斷依據


def detect_from_url(url: str) -> AtsType | None:
    """只看網址就能判斷的情況（不連網）。"""
    host = (urlsplit(url).hostname or "").lower()
    return next((ats for pattern, ats in _HOSTS if pattern.search(host)), None)


def detect(url: str, client: httpx.Client) -> Detection:
    """先看網址；看不出來就讀網頁，找轉址目標或頁面裡嵌入的招募系統連結。"""
    if ats := detect_from_url(url):
        return Detection(ats, url, "網址")
    resp = client.get(url, headers={"Accept": "text/html"})
    resp.raise_for_status()
    if ats := detect_from_url(str(resp.url)):
        return Detection(ats, str(resp.url), "轉址")
    for pattern, ats in _EMBEDDED:
        if match := pattern.search(resp.text):
            return Detection(ats, match.group(0), "頁面內容")
    return Detection("generic", url, "無法辨識")
