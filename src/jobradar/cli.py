"""命令列入口：uv run jobradar <指令>"""

import argparse
from collections import Counter

import httpx

from jobradar.adapters import ADAPTERS, Adapter, get_adapter
from jobradar.config import ConfigError, Settings, load_profile, load_settings, load_sources
from jobradar.detect import detect
from jobradar.pipeline import Screened, resolve_ats, screen_jobs
from jobradar.store import TABLES, Store
from jobradar.web import make_client

_REJECT_LABELS = {
    "rejected_location": "地區不符",
    "rejected_exclude": "排除詞",
    "rejected_description": "內容排除詞",
    "rejected_keyword": "關鍵字不符",
}


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


def cmd_fetch(args: argparse.Namespace) -> int:
    try:
        sources = load_sources()
    except ConfigError as e:
        print(f"❌ {e}")
        return 1
    if args.source:
        unknown = set(args.source) - {s.name for s in sources}
        if unknown:
            print(f"❌ sources.yaml 裡沒有：{', '.join(sorted(unknown))}")
            return 1
        sources = [s for s in sources if s.name in args.source]

    failed = 0
    with make_client() as client:
        for source in sources:
            ats = resolve_ats(source)
            labels = " · ".join(filter(None, [source.company, source.group, ats]))
            print(f"\n== {source.name}（{labels}）==")
            try:
                adapter = get_adapter(ats, client)
                screened = screen_jobs(adapter, source, adapter.list_jobs(source))
            except Exception as e:  # 一個來源失敗不影響其他來源
                failed += 1
                print(f"❌ 抓取失敗：{_describe_error(e)}")
                continue
            _print_screened(adapter, screened, verbose=args.verbose)
    return 1 if failed else 0


def _print_screened(adapter: Adapter, screened: list[Screened], verbose: bool) -> None:
    for note in adapter.notes:
        print(f"  {note}")
    passed = [s for s in screened if s.verdict.passed]
    rejected = [s for s in screened if not s.verdict.passed]
    counts = Counter(s.verdict.status for s in rejected)
    summary = "、".join(f"{_REJECT_LABELS[k]} {n}" for k, n in counts.items())
    print(f"  抓到 {len(screened)} 筆 → 通過硬篩 {len(passed)} 筆" + (f"（篩掉：{summary}）" if summary else ""))

    if passed:
        print("\n  ✅ 通過硬篩（之後會送 AI 評分）")
    for s in passed:
        job = s.job
        extras = [
            job.company if job.ats == "mycareersfuture" else None,  # 搜尋型來源的公司各不相同
            job.salary,
            "仲介" if job.via_agency else None,
            f"命中「{s.verdict.reason}」" if s.verdict.reason else None,
        ]
        tail = "".join(f"｜{e}" for e in extras if e)
        print(f"  • {job.title}｜{job.location}｜{job.posted_date or '日期不明'}{tail}")
        print(f"    {job.url}")
        if verbose and job.description:
            print(f"    {job.description[:300].replace(chr(10), ' ')}…")

    # 地區不符的通常是大量海外職缺，只顯示數量
    listed = [s for s in rejected if s.verdict.status != "rejected_location"]
    if listed:
        print("\n  ✗ 篩掉（地區不符的不列出）")
    for s in listed:
        detail = f"「{s.verdict.reason}」" if s.verdict.status in ("rejected_exclude", "rejected_description") else ""
        print(f"  • {s.job.title}｜{_REJECT_LABELS[s.verdict.status]}{detail}")


def cmd_detect(args: argparse.Namespace) -> int:
    with make_client() as client:
        try:
            result = detect(args.url, client)
        except httpx.HTTPError as e:
            print(f"❌ 無法讀取網頁：{_describe_error(e)}")
            return 1
    print(f"類型：{result.ats}（依據：{result.how}）")
    if result.url != args.url:
        print(f"請在 sources.yaml 使用這個網址：{result.url}")
    if result.ats in ADAPTERS:
        print("✅ 已支援，可以加進 sources.yaml")
    else:
        print("⚠️ 這種類型還沒有 adapter")
    return 0


def _describe_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}（{e.request.url}）"
    return f"{type(e).__name__}: {e}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobradar", description="個人職缺監控")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="檢查設定檔、金鑰與各服務連線（不會發送任何訊息）").set_defaults(
        func=cmd_check
    )

    fetch = sub.add_parser("fetch", help="抓取職缺並套用硬篩，只顯示結果（不寫資料庫、不推播）")
    fetch.add_argument("--source", action="append", help="只抓指定來源（sources.yaml 的 name），可重複")
    fetch.add_argument("-v", "--verbose", action="store_true", help="顯示職缺內容開頭")
    fetch.set_defaults(func=cmd_fetch)

    detect_cmd = sub.add_parser("detect", help="判斷招募網址屬於哪種招募系統")
    detect_cmd.add_argument("url")
    detect_cmd.set_defaults(func=cmd_detect)

    args = parser.parse_args(argv)
    return args.func(args)
