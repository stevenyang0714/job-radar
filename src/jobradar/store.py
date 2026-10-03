"""Supabase 存取（透過 PostgREST REST API）。"""

import httpx

TABLES = ("jobs", "runs", "source_runs")

# PostgREST 找不到資料表時回傳的錯誤代碼
_MISSING_TABLE_CODES = {"PGRST205", "42P01"}


class Store:
    def __init__(self, url: str, secret_key: str, timeout: float = 20.0) -> None:
        self._client = httpx.Client(
            base_url=f"{url}/rest/v1",
            headers={"apikey": secret_key, "Authorization": f"Bearer {secret_key}"},
            timeout=timeout,
        )

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def missing_tables(self) -> list[str]:
        missing = []
        for table in TABLES:
            resp = self._client.get(f"/{table}", params={"select": "id", "limit": 1})
            if resp.status_code in (400, 404) and _error_code(resp) in _MISSING_TABLE_CODES:
                missing.append(table)
            else:
                resp.raise_for_status()
        return missing


def _error_code(resp: httpx.Response) -> str | None:
    try:
        return resp.json().get("code")
    except ValueError:
        return None
