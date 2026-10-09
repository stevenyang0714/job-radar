# job-radar

個人職缺監控：每兩天台灣凌晨 02:07 檢查 `sources.yaml` 裡的職缺來源，找出新職缺，依 `profile.md` 與 `cv/` 裡的履歷用 AI 評分，高分職缺推播到 Telegram。

- `sources.yaml`：要監控的公司與篩選條件
- `profile.md`、`cv/`：評分依據（背景、求職方向、各版履歷）
- `settings.yaml`：AI 模型、每月預算、推播門檻與天數

## 本機設定

1. 安裝 [uv](https://docs.astral.sh/uv/)（會自動下載 Python 3.11）
2. `cp .env.example .env`，填入各項金鑰
3. 在 Supabase Dashboard → SQL Editor 執行 `supabase/schema.sql`
4. `uv run jobradar check` 確認設定與連線

## 指令

| 指令 | 用途 |
|---|---|
| `uv run jobradar check` | 檢查設定檔、金鑰與各服務連線（不會發送任何訊息） |
| `uv run jobradar detect <網址>` | 判斷招募網址屬於哪種招募系統，新增公司前先跑這個 |
| `uv run jobradar fetch [--source 名稱] [-v]` | 抓取職缺並套用硬篩，只顯示結果（不寫資料庫、不推播） |
| `uv run jobradar run` | 正式執行：抓取、去重、AI 評分、推播 |
| `uv run jobradar score --sample 5` | 試評幾筆待評分職缺（改 profile.md 後先看看分數） |
| `uv run jobradar test-notify` | 發示範訊息到 Telegram |
| `uv run jobradar db-migrate` | 更新資料庫結構（需要 `.env` 的 `SUPABASE_DB_URL`） |
| `uv run pytest` | 執行測試 |

## 新增公司

1. `uv run jobradar detect https://公司招募網址`，確認類型與建議網址
2. 在 `sources.yaml` 的 `sources:` 下加一筆 `name`、`company`、`url`（篩選條件沒寫就套用 `defaults`）
3. `uv run jobradar fetch --source 名稱` 看抓取與篩選結果

## 自動執行（GitHub Actions）

`.github/workflows/daily.yml` 每兩天在台灣凌晨 02:07 觸發 `jobradar run`。刻意避開整點：GitHub 排程在 UTC 午夜最擁擠，曾延遲將近 6 小時。

1. 到 GitHub repo → Settings → Secrets and variables → Actions，新增 5 個 secret：`SUPABASE_URL`、`SUPABASE_SECRET_KEY`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`OPENAI_API_KEY`。可以用 `uv run jobradar copy-secrets` 把 `.env` 的值逐一複製到剪貼簿
2. 要立即執行：Actions → Job radar → Run workflow
3. 整個執行失敗時會發 Telegram 通知；每次執行都會發摘要，該收到卻沒收到就代表排程沒跑
4. 執行頻率：`.github/workflows/daily.yml` 每天觸發，由 `RUN_EVERY_DAYS`（目前 2）決定每幾天真正執行一次
