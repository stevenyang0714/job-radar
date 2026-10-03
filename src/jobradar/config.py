"""載入 .env、sources.yaml、profile.md。"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from jobradar.models import Source

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_ENV = (
    "SUPABASE_URL",
    "SUPABASE_SECRET_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "OPENAI_API_KEY",
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_secret_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    openai_api_key: str


def load_settings(env_file: Path = ROOT / ".env") -> Settings:
    # 本機從 .env 讀取；GitHub Actions 由 Secrets 直接放進環境變數，不會被 .env 覆蓋
    load_dotenv(env_file, override=False)
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
    if missing:
        raise ConfigError("缺少環境變數：" + ", ".join(missing))
    env = {name: os.environ[name].strip() for name in REQUIRED_ENV}
    return Settings(
        supabase_url=env["SUPABASE_URL"].rstrip("/"),
        supabase_secret_key=env["SUPABASE_SECRET_KEY"],
        telegram_bot_token=env["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=env["TELEGRAM_CHAT_ID"],
        openai_api_key=env["OPENAI_API_KEY"],
    )


DEFAULT_FIELDS = ("locations", "keywords", "exclude")


def load_sources(path: Path = ROOT / "sources.yaml") -> list[Source]:
    try:
        data = yaml.safe_load(_read(path)) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name} 不是有效的 YAML：\n{e}") from e

    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict) or set(defaults) - set(DEFAULT_FIELDS):
        raise ConfigError(f"{path.name} 的 defaults 只能包含：{', '.join(DEFAULT_FIELDS)}")

    sources = []
    for i, item in enumerate(data.get("sources") or [], start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"{path.name} 第 {i} 個來源格式錯誤：應為 name、company、url 等欄位")
        try:
            sources.append(Source.model_validate(_apply_defaults(item, defaults)))
        except ValidationError as e:
            raise ConfigError(f"{path.name} 第 {i} 個來源格式錯誤：\n{e}") from e

    names = [s.name for s in sources]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ConfigError(f"{path.name} 的 name 重複：" + ", ".join(duplicates))
    return sources


def _apply_defaults(item: dict, defaults: dict) -> dict:
    """locations、keywords：來源有寫就用來源的；exclude：來源的排除詞加在 defaults 之上。"""
    merged = {**{k: v for k, v in defaults.items() if k != "exclude"}, **item}
    exclude = [*(defaults.get("exclude") or []), *(item.get("exclude") or [])]
    if exclude:
        merged["exclude"] = list(dict.fromkeys(exclude))
    return merged


def load_profile(path: Path = ROOT / "profile.md") -> str:
    text = _read(path).strip()
    if not text:
        raise ConfigError(f"{path.name} 是空的")
    return text


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ConfigError(f"找不到 {path.name}") from e
