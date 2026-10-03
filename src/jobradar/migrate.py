"""在本機直接執行 supabase/schema.sql 更新資料庫結構（需要 .env 的 SUPABASE_DB_URL）。"""

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import load_dotenv

from jobradar.config import ROOT, ConfigError

SCHEMA = ROOT / "supabase" / "schema.sql"


def database_url(env_file: Path = ROOT / ".env") -> str:
    load_dotenv(env_file, override=False)
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        raise ConfigError(
            ".env 的 SUPABASE_DB_URL 是空的：到 Supabase 專案頁面上方的 Connect → Session pooler "
            "複製連線字串，把 [YOUR-PASSWORD] 換成資料庫密碼後填入"
        )
    if "[YOUR-PASSWORD]" in url:
        raise ConfigError("SUPABASE_DB_URL 裡的 [YOUR-PASSWORD] 還沒換成資料庫密碼")
    if (password := _password(url)) and password.startswith("[") and password.endswith("]"):
        raise ConfigError("SUPABASE_DB_URL 的密碼前後還有方括號 [ ]，要連同方括號一起換掉")
    return url


def _password(url: str) -> str | None:
    try:
        return urlsplit(url).password
    except ValueError:  # 網址格式有誤（例如密碼含方括號）時改用比對
        match = re.search(r"://[^:/@]+:([^@]+)@", url)
        return match.group(1) if match else None


def redact(text: str, url: str) -> str:
    """錯誤訊息可能帶出連線資訊，把密碼遮掉。"""
    password = _password(url)
    for secret in sorted(filter(None, {password, unquote(password or "")}), key=len, reverse=True):
        text = text.replace(secret, "****")
    return text


def apply_schema(url: str, schema: Path = SCHEMA) -> None:
    import psycopg  # 只有本機開發時才需要，GitHub Actions 不會安裝

    with psycopg.connect(url, connect_timeout=15) as conn:
        conn.execute(schema.read_text(encoding="utf-8"))
        conn.execute("notify pgrst, 'reload schema'")  # 讓 Supabase 的 REST API 立刻看到新欄位
