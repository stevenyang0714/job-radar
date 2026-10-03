"""命令列入口：uv run jobradar <指令>"""

import argparse

import httpx

from jobradar.config import ConfigError, Settings, load_profile, load_settings, load_sources
from jobradar.store import TABLES, Store


def cmd_check(args: argparse.Namespace) -> int:
    results: list[bool] = []

    def report(passed: bool, label: str, detail: str) -> None:
        results.append(passed)
        print(f"{'✅' if passed else '❌'} {label}：{detail}")

    try:
        settings = load_settings()
    except ConfigError as e:
        report(False, ".env", str(e))
        return 1
    report(True, ".env", "5 個環境變數都已設定")

    try:
        sources = load_sources()
        report(True, "sources.yaml", f"{len(sources)} 個來源（{', '.join(s.name for s in sources)}）")
    except ConfigError as e:
        report(False, "sources.yaml", str(e))

    try:
        profile = load_profile()
        report(True, "profile.md", f"{len(profile):,} 字元")
    except ConfigError as e:
        report(False, "profile.md", str(e))

    for label, probe in (
        ("Supabase", _check_supabase),
        ("Telegram", _check_telegram),
        ("OpenAI", _check_openai),
    ):
        try:
            passed, detail = probe(settings)
        except (httpx.HTTPError, ValueError) as e:
            # 只顯示錯誤類型：httpx 的錯誤訊息含網址，Telegram 網址裡有 bot token
            passed, detail = False, f"連線失敗（{type(e).__name__}）"
        report(passed, label, detail)

    failed = results.count(False)
    print("\n全部通過" if not failed else f"\n{failed} 項需要處理")
    return 1 if failed else 0


def _check_supabase(s: Settings) -> tuple[bool, str]:
    try:
        with Store(s.supabase_url, s.supabase_secret_key) as store:
            missing = store.missing_tables()
    except httpx.HTTPStatusError as e:
        try:
            message = e.response.json().get("message", "")
        except ValueError:
            message = ""
        return False, f"HTTP {e.response.status_code} {message}".strip()
    if missing:
        return False, (
            f"連線成功，但缺少資料表 {', '.join(missing)}"
            " → 請到 Supabase SQL Editor 執行 supabase/schema.sql"
        )
    return True, f"連線成功，資料表 {' / '.join(TABLES)} 都在"


def _check_telegram(s: Settings) -> tuple[bool, str]:
    # 只呼叫唯讀的 getMe / getChat，不會發送訊息
    base = f"https://api.telegram.org/bot{s.telegram_bot_token}"
    with httpx.Client(timeout=20) as client:
        me = client.get(f"{base}/getMe").json()
        if not me.get("ok"):
            return False, f"bot token 無效（{me.get('description')}）"
        chat = client.get(f"{base}/getChat", params={"chat_id": s.telegram_chat_id}).json()

    bot = "@" + me["result"]["username"]
    if not chat.get("ok"):
        return False, (
            f"{bot} 正常，但無法存取 chat_id（{chat.get('description')}）"
            " → 請確認 chat_id，並先在 Telegram 對 bot 按「開始」"
        )
    c = chat["result"]
    name = c.get("title") or c.get("first_name") or c.get("username") or "?"
    return True, f"{bot} 正常，可以傳訊息給 {name}（{c['type']}）"


def _check_openai(s: Settings) -> tuple[bool, str]:
    # 列出模型清單只驗證金鑰，不產生費用
    resp = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {s.openai_api_key}"},
        timeout=20,
    )
    if resp.status_code == 401:
        return False, "API key 無效（HTTP 401）"
    if resp.status_code == 403:
        return False, "HTTP 403：金鑰可能被限制了權限，請到 OpenAI 後台確認"
    if resp.is_error:
        return False, f"HTTP {resp.status_code}"
    return True, f"API key 有效（可用模型 {len(resp.json()['data'])} 個）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobradar", description="個人職缺監控")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="檢查設定檔、金鑰與各服務連線（不會發送任何訊息）").set_defaults(
        func=cmd_check
    )
    args = parser.parse_args(argv)
    return args.func(args)
