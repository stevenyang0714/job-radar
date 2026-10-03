# job-radar

個人職缺監控：每天台灣時間 08:00、20:00 檢查 `sources.yaml` 裡的職缺來源，找出新職缺，依 `profile.md` 評分，高分職缺推播到 Telegram。

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
| `uv run pytest` | 執行測試 |

## 新增公司

1. `uv run jobradar detect https://公司招募網址`，確認類型與建議網址
2. 在 `sources.yaml` 的 `sources:` 下加一筆 `name`、`company`、`url`（篩選條件沒寫就套用 `defaults`）
3. `uv run jobradar fetch --source 名稱` 看抓取與篩選結果
