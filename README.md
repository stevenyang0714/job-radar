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
| `uv run pytest` | 執行測試 |
