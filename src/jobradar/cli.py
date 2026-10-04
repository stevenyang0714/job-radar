"""命令列入口：uv run jobradar <指令>"""

import argparse
import subprocess
from collections import Counter

from dotenv import dotenv_values

import httpx

from datetime import date, datetime, timedelta

from jobradar.adapters import ADAPTERS, Adapter, get_adapter
from jobradar.alerts import TAIPEI, AlertReport, usage_row, dispatch, score_pending, to_job
from jobradar.config import (
    REQUIRED_ENV,
    ROOT,
    ConfigError,
    Settings,
    load_app_settings,
    load_cvs,
    load_profile,
    load_settings,
    load_sources,
)
from jobradar.scorer import Scorer
from jobradar.detect import detect
from jobradar.messages import SourceWarning, format_job, format_warnings
from jobradar.models import Source
from jobradar.notify import Telegram, TelegramError
from jobradar.pipeline import RunReport, Screened, describe_error, resolve_ats, run, screen_jobs
from jobradar.store import TABLES, Store
from jobradar.web import make_client

_REJECT_LABELS = {
    "rejected_location": "地區不符",
    "rejected_exclude": "排除詞",
    "rejected_description": "內容排除詞",
    "rejected_keyword": "關鍵字不符",
    "detail_error": "內容抓取失敗",
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
            missing_columns = [] if missing else store.missing_columns()
    except httpx.HTTPStatusError as e:
        try:
            message = e.response.json().get("message", "")
        except ValueError:
            message = ""
        return False, f"HTTP {e.response.status_code} {message}".strip()
    if missing or missing_columns:
        what = f"資料表 {', '.join(missing)}" if missing else f"新欄位 {', '.join(missing_columns)}"
        return False, f"連線成功，但缺少{what} → 請到 Supabase SQL Editor 執行 supabase/schema.sql"
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


def _select_sources(names: list[str] | None) -> list[Source] | None:
    """讀取 sources.yaml，可只取指定的來源；設定有誤時印出原因並回傳 None。"""
    try:
        sources = load_sources()
    except ConfigError as e:
        print(f"❌ {e}")
        return None
    if names:
        unknown = set(names) - {s.name for s in sources}
        if unknown:
            print(f"❌ sources.yaml 裡沒有：{', '.join(sorted(unknown))}")
            return None
        sources = [s for s in sources if s.name in names]
    return sources


def cmd_fetch(args: argparse.Namespace) -> int:
    sources = _select_sources(args.source)
    if sources is None:
        return 1

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
                print(f"❌ 抓取失敗：{describe_error(e)}")
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
            print(f"❌ 無法讀取網頁：{describe_error(e)}")
            return 1
    print(f"類型：{result.ats}（依據：{result.how}）")
    if result.url != args.url:
        print(f"請在 sources.yaml 使用這個網址：{result.url}")
    if result.ats in ADAPTERS:
        print("✅ 已支援，可以加進 sources.yaml")
    else:
        print("⚠️ 這種類型還沒有 adapter")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """正式的每日流程：抓取、去重、寫入資料庫、建立推播、發送警告與初始盤點。"""
    sources = _select_sources(args.source)
    if sources is None:
        return 1
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"❌ {e}")
        return 1

    try:
        app = load_app_settings()
        scorer = None if args.no_score else Scorer(settings.openai_api_key, app.scoring, load_profile(), load_cvs())
    except (ConfigError, ValueError) as e:
        print(f"❌ {e}")
        return 1

    notifier = None if args.no_notify else Telegram(settings.telegram_bot_token, settings.telegram_chat_id)
    now = datetime.now(TAIPEI)
    send_digest = args.digest or (args.trigger == "schedule" and now.hour >= app.notifications.digest_after_hour)
    with Store(settings.supabase_url, settings.supabase_secret_key) as store, make_client() as client:
        missing = store.missing_tables() or store.missing_columns()
        if missing:
            print(f"❌ 資料庫缺少 {', '.join(missing)} → 執行 uv run jobradar db-migrate")
            return 1
        try:
            report = run(sources, store, notifier, client, trigger=args.trigger, today=now.date(), rules=app.notifications)
            alerts = AlertReport()
            if scorer:
                score_pending(store, scorer, app.scoring, now.date(), alerts)
                dispatch(store, notifier, app.notifications, now.date(), alerts, send_digest, _status_line(report, alerts))
        except TelegramError as e:
            print(f"❌ Telegram 發送失敗：{e}")
            return 1
        pending = store.count_pending_notifications()

    _print_report(report, pending, sent=notifier is not None)
    if scorer:
        _print_alerts(alerts)
    # 警告已成功送到 Telegram 就算正常結束；沒有推播時，以結束代碼提醒有來源失敗
    return 1 if args.no_notify and report.status != "ok" else 0


def _status_line(report: RunReport, alerts: AlertReport) -> str:
    """每日摘要最後的執行狀態；每天都會收到摘要，沒收到就代表排程停了。"""
    ok = sum(r.status == "ok" for r in report.results)
    down = [r.source.company for r in report.results if r.status != "ok"]
    text = f"⚙️ 這次執行：{ok}/{len(report.results)} 個來源正常"
    if down:
        text += f"（{', '.join(down)} 暫時無法使用）"
    text += f"｜本月 AI 評分 US${alerts.month_cost_usd:.2f} / {alerts.budget_usd:.0f}"
    if alerts.budget_hit:
        text += "（已達上限，暫停評分）"
    return text


def _print_alerts(alerts: AlertReport) -> None:
    print(f"\nAI 評分：{alerts.scored} 筆，失敗 {alerts.failed} 筆，這次 US${alerts.cost_usd:.4f}，本月累計 US${alerts.month_cost_usd:.4f}")
    if alerts.budget_hit or alerts.unscored:
        print(f"  ⚠️ 尚未評分 {alerts.unscored} 筆" + ("（已達每月預算上限）" if alerts.budget_hit else ""))
    for error in alerts.errors[:3]:
        print(f"  ❌ {error}")
    print(
        f"推播：立即 {alerts.instant_sent} 則、初始盤點清單 {alerts.baseline_listed} 筆、"
        f"每日摘要 {'已發送 ' + str(alerts.digest_listed) + ' 筆' if alerts.digest_sent else '未發送'}、未達門檻 {alerts.skipped} 筆"
    )


def cmd_score(args: argparse.Namespace) -> int:
    """試評：從待評分職缺挑幾筆評分並顯示結果，不寫回職缺、不推播（費用會記入本月用量）。"""
    try:
        settings = load_settings()
        app = load_app_settings()
        overrides = {k: v for k, v in (("model", args.model), ("reasoning_effort", args.effort)) if v}
        scoring = app.scoring.model_copy(update=overrides)
        scorer = Scorer(settings.openai_api_key, scoring, load_profile(), load_cvs())
    except (ConfigError, ValueError) as e:
        print(f"❌ {e}")
        return 1

    today = datetime.now(TAIPEI).date()
    with Store(settings.supabase_url, settings.supabase_secret_key) as store:
        sample = _spread_sample(store.pending_notifications(), args.sample)
        print(f"試評 {len(sample)} 筆｜{scoring.model}｜reasoning {scoring.reasoning_effort}｜{scoring.service_tier}\n")
        total = 0.0
        for n in sample:
            outcome = scorer.score(to_job(n["job"]), n["kind"], today)
            store.record_usage(usage_row(n["job"]["id"], outcome))
            r, u = outcome.result, outcome.usage
            total += u.cost_usd
            print(f"{r.score:>3} 分｜{n['job']['company'][:20]}｜{n['job']['title'][:60]}")
            print(f"      Track {r.track}｜{r.cv_version}｜輸出 {u.output_tokens} tokens（推理 {u.reasoning_tokens}）｜US${u.cost_usd:.5f}")
            print(f"      ✅ {' / '.join(r.fit_points)[:220]}")
            print(f"      ⚠️ {' / '.join(r.gaps)[:160]}")
    print(f"\n合計 US${total:.4f}")
    return 0


def _spread_sample(pending: list[dict], size: int) -> list[dict]:
    """從不同來源輪流挑選，讓試評涵蓋各種公司。"""
    by_source: dict[str, list[dict]] = {}
    for n in pending:
        by_source.setdefault(n["job"]["source"], []).append(n)
    sample: list[dict] = []
    while len(sample) < size and any(by_source.values()):
        for items in by_source.values():
            if items and len(sample) < size:
                sample.append(items.pop(0))
    return sample


def _print_report(report: RunReport, pending: dict[str, int], sent: bool) -> None:
    print(f"執行 #{report.run_id}：{report.status}\n")
    for r in report.results:
        tag = "（初始盤點）" if r.baseline else ""
        if r.status == "ok":
            notes = "、".join(f"{label} {r.notifications[kind]}" for kind, label in (("new", "🆕"), ("long_open", "⏳")) if r.notifications[kind])
            print(f"✅ {r.source.name}{tag}：{r.job_count} 筆，新 {r.new_count}，通過硬篩 {r.passed_new}" + (f"，新增推播 {notes}" if notes else ""))
        else:
            print(f"{'⏸️' if r.status == 'unavailable' else '❌'} {r.source.name}：{r.error}")
        if r.warning:
            print(f"   ⚠️ {r.warning}")
    print(f"\n待評分的推播：🆕 {pending.get('new', 0)} 筆、⏳ {pending.get('long_open', 0)} 筆（步驟 4 評分後發送）")
    print(f"Telegram：{'已發送 ' + str(len(report.messages)) + ' 則' if sent and report.messages else '沒有要發送的訊息' if sent else '未發送（--no-notify）'}")


def cmd_copy_secrets(args: argparse.Namespace) -> int:
    """把 .env 裡 GitHub Actions 需要的值逐一複製到剪貼簿，方便貼到 GitHub Secrets（不會顯示內容，macOS 限定）。"""
    values = dotenv_values(ROOT / ".env")
    missing = [name for name in REQUIRED_ENV if not (values.get(name) or "").strip()]
    if missing:
        print(f"❌ .env 缺少：{', '.join(missing)}")
        return 1
    print(f"打開 https://github.com/stevenyang0714/job-radar/settings/secrets/actions/new ，共 {len(REQUIRED_ENV)} 個：")
    try:
        for i, name in enumerate(REQUIRED_ENV, start=1):
            value = values[name].strip()
            subprocess.run(["pbcopy"], input=value.encode(), check=True)
            input(
                f"\n[{i}/{len(REQUIRED_ENV)}] 📋 已複製 {name} 的值（{len(value)} 字元）\n"
                f"      Name 填 {name}，Secret 貼上（Cmd+V），按 Add secret；完成後回到這裡按 Enter… "
            )
    finally:
        subprocess.run(["pbcopy"], input=b"", check=True)  # 清空剪貼簿，避免金鑰留在裡面
    print("\n✅ 全部完成，剪貼簿已清空")
    return 0


def cmd_db_migrate(args: argparse.Namespace) -> int:
    """執行 supabase/schema.sql，更新資料庫結構（可重複執行）。"""
    from jobradar.migrate import apply_schema, database_url, redact

    try:
        url = database_url()
    except ConfigError as e:
        print(f"❌ {e}")
        return 1
    try:
        apply_schema(url)
    except Exception as e:  # 連線或 SQL 錯誤；訊息先遮掉密碼再顯示
        print(f"❌ 更新失敗：{redact(f'{type(e).__name__}: {e}', url)}")
        return 1
    print("✅ 資料庫結構已更新（supabase/schema.sql）")
    return 0


def cmd_test_notify(args: argparse.Namespace) -> int:
    """發送示範訊息，讓使用者看警告與職缺通知的格式。"""
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"❌ {e}")
        return 1
    today = date.today()
    samples = [
        "🧪 <b>job-radar 測試訊息</b>\n以下是示範格式，不是真的職缺或錯誤。",
        format_warnings(
            [
                SourceWarning("Abbott", "抓取失敗：HTTP 500（abbott.wd5.myworkdayjobs.com）"),
                SourceWarning("Citi", "上次抓到 480 筆，這次 0 筆，網站可能改版"),
            ]
        ),
        format_job(
            kind="new", title="Director, Strategic Partnerships", company="Airwallex", location="Singapore",
            url="https://jobs.ashbyhq.com/airwallex", posted_date=today - timedelta(days=3), today=today, score=82,
            fit_points=["iKala 企業 AI 0→1 與 US$2M pipeline", "APAC 跨國合作夥伴經驗", "金融科技產業相關"],
            gaps=["缺少支付產業直接經驗"],
        ),
        format_job(
            kind="long_open", title="Technology Risk Manager", company="LGT", location="Singapore",
            url="https://www.mycareersfuture.gov.sg/", posted_date=today - timedelta(days=150), today=today, score=76,
            salary="SGD 10,000–15,000 / 月", via_agency=True, applicants=3,
        ),
    ]
    try:
        telegram = Telegram(settings.telegram_bot_token, settings.telegram_chat_id)
        for text in samples:
            telegram.send(text)
    except TelegramError as e:
        print(f"❌ Telegram 發送失敗：{e}")
        return 1
    print(f"✅ 已發送 {len(samples)} 則示範訊息到 Telegram")
    return 0


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

    run_cmd = sub.add_parser("run", help="正式執行：抓取、去重、AI 評分、推播（GitHub Actions 每天跑的就是這個）")
    run_cmd.add_argument("--source", action="append", help="只處理指定來源（sources.yaml 的 name），可重複")
    run_cmd.add_argument("--no-notify", action="store_true", help="不發送 Telegram，只寫入資料庫")
    run_cmd.add_argument("--no-score", action="store_true", help="不做 AI 評分（也不發職缺推播）")
    run_cmd.add_argument("--digest", action="store_true", help="這次執行也發每日摘要（排程執行會自動發）")
    run_cmd.add_argument("--trigger", default="local", choices=["local", "schedule", "manual"], help="執行來源，記錄用")
    run_cmd.set_defaults(func=cmd_run)

    score_cmd = sub.add_parser("score", help="試評幾筆待評分職缺並顯示結果（不寫回、不推播），改 profile.md 後可先試試")
    score_cmd.add_argument("--sample", type=int, default=5, help="試評幾筆（預設 5）")
    score_cmd.add_argument("--model", help="暫時改用其他模型，例如 gpt-6.1-sol")
    score_cmd.add_argument("--effort", choices=["none", "low", "medium", "high"], help="暫時改用其他推理強度")
    score_cmd.set_defaults(func=cmd_score)

    sub.add_parser("test-notify", help="發送示範訊息到 Telegram，看警告與職缺通知的格式").set_defaults(
        func=cmd_test_notify
    )
    sub.add_parser("db-migrate", help="用 .env 的 SUPABASE_DB_URL 執行 supabase/schema.sql，更新資料庫結構").set_defaults(
        func=cmd_db_migrate
    )
    sub.add_parser("copy-secrets", help="把 .env 的金鑰逐一複製到剪貼簿，貼到 GitHub Secrets（不顯示內容）").set_defaults(
        func=cmd_copy_secrets
    )

    args = parser.parse_args(argv)
    return args.func(args)
