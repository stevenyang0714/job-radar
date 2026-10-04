"""載入 .env、sources.yaml、profile.md。"""

import os
from dataclasses import dataclass
from pathlib import Path

from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


FILTER_FIELDS = ("locations", "keywords", "exclude", "exclude_description")
_ACCUMULATED = ("exclude", "exclude_description")  # 各層累加，不互相取代


def load_sources(path: Path = ROOT / "sources.yaml") -> list[Source]:
    try:
        data = yaml.safe_load(_read(path)) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name} 不是有效的 YAML：\n{e}") from e

    defaults = _filter_block(data.get("defaults"), "defaults", path)
    groups_data = data.get("groups") or {}
    if not isinstance(groups_data, dict):
        raise ConfigError(f"{path.name} 的 groups 格式錯誤：應為「分組名稱: 篩選條件」")
    groups = {name: _filter_block(block, f"groups.{name}", path) for name, block in groups_data.items()}

    sources = []
    for i, item in enumerate(data.get("sources") or [], start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"{path.name} 第 {i} 個來源格式錯誤：應為 name、company、url 等欄位")
        group = item.get("group")
        if group is not None and group not in groups:
            raise ConfigError(f"{path.name} 第 {i} 個來源的 group「{group}」不存在，可用：{', '.join(groups)}")
        try:
            sources.append(Source.model_validate(_merge([defaults, groups.get(group, {}), item])))
        except ValidationError as e:
            raise ConfigError(f"{path.name} 第 {i} 個來源格式錯誤：\n{e}") from e

    names = [s.name for s in sources]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ConfigError(f"{path.name} 的 name 重複：" + ", ".join(duplicates))
    return sources


def _filter_block(block: object, label: str, path: Path) -> dict:
    block = block or {}
    if not isinstance(block, dict) or set(block) - set(FILTER_FIELDS):
        raise ConfigError(f"{path.name} 的 {label} 只能包含：{', '.join(FILTER_FIELDS)}")
    return block


def _merge(layers: list[dict]) -> dict:
    """依 defaults → group → 來源 的順序合併。

    locations、keywords：後面的層有寫就取代前面的；exclude、exclude_description：各層累加。
    """
    merged: dict = {}
    accumulated: dict[str, list[str]] = {field: [] for field in _ACCUMULATED}
    for layer in layers:
        merged.update({k: v for k, v in layer.items() if k not in _ACCUMULATED})
        for field in _ACCUMULATED:
            accumulated[field] += layer.get(field) or []
    for field, terms in accumulated.items():
        if terms:
            merged[field] = list(dict.fromkeys(terms))
    return merged


class ScoringSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "gpt-6-luna"
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    service_tier: Literal["flex", "auto", "default"] = "flex"
    monthly_budget_usd: float = Field(5.0, ge=0)
    max_per_run: int = Field(1000, ge=0)
    concurrency: int = Field(3, ge=1, le=20)


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instant_min_score: int = Field(70, ge=0, le=100)
    digest_min_score: int = Field(50, ge=0, le=100)
    baseline_min_score: int = Field(70, ge=0, le=100)
    digest_after_hour: int = Field(0, ge=0, le=23)
    new_within_days: int = Field(7, ge=0)
    long_open_after_days: int = Field(120, ge=1)


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scoring: ScoringSettings = ScoringSettings()
    notifications: NotificationSettings = NotificationSettings()


def load_app_settings(path: Path = ROOT / "settings.yaml") -> AppSettings:
    """讀取 settings.yaml；檔案不存在時使用預設值。"""
    if not path.exists():
        return AppSettings()
    try:
        return AppSettings.model_validate(yaml.safe_load(_read(path)) or {})
    except (yaml.YAMLError, ValidationError) as e:
        raise ConfigError(f"{path.name} 格式錯誤：\n{e}") from e


def load_cvs(directory: Path = ROOT / "cv") -> dict[str, str]:
    """cv/ 資料夾裡的履歷文字，檔名（不含副檔名）→ 內容。"""
    return {p.stem: p.read_text(encoding="utf-8").strip() for p in sorted(directory.glob("*.txt"))}


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
