"""Supabase 存取（透過 PostgREST REST API）。"""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx

TABLES = ("jobs", "notifications", "runs", "source_runs")

# 步驟 3 新增的欄位；檢查這些欄位可以知道 schema.sql 是否已更新
REQUIRED_COLUMNS = {"jobs": "salary,via_agency,applicants,closed_at,score_detail"}

# PostgREST 找不到資料表或欄位時回傳的錯誤代碼
_MISSING_TABLE_CODES = {"PGRST205", "42P01"}
_MISSING_COLUMN_CODES = {"PGRST204", "42703"}

PAGE_SIZE = 1000  # Supabase 每次查詢最多回傳 1000 筆
CHUNK = 200  # 一次寫入或用 id 篩選的筆數，避免請求過大


@dataclass(frozen=True)
class KnownJob:
    id: int
    company: str
    job_id: str
    filter_status: str | None
    posted_date: date | None
    first_seen_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class SourceHistory:
    has_success: bool  # 這個來源曾經成功執行過；沒有的話這次是初始盤點
    last_status: str | None  # 上一次執行的狀態：ok / error / unavailable
    last_ok_count: int | None  # 上一次成功時抓到的筆數


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, url: str, secret_key: str, timeout: float = 30.0) -> None:
        self._client = httpx.Client(
            base_url=f"{url}/rest/v1",
            headers={"apikey": secret_key, "Authorization": f"Bearer {secret_key}"},
            timeout=timeout,
        )

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    # ── 檢查 ──

    def missing_tables(self) -> list[str]:
        missing = []
        for table in TABLES:
            resp = self._client.get(f"/{table}", params={"select": "id", "limit": 1})
            if resp.status_code in (400, 404) and _error_code(resp) in _MISSING_TABLE_CODES:
                missing.append(table)
            else:
                resp.raise_for_status()
        return missing

    def missing_columns(self) -> list[str]:
        missing = []
        for table, columns in REQUIRED_COLUMNS.items():
            resp = self._client.get(f"/{table}", params={"select": columns, "limit": 1})
            if resp.status_code == 400 and _error_code(resp) in _MISSING_COLUMN_CODES:
                missing.append(f"{table}（{resp.json().get('message', '')}）")
            else:
                resp.raise_for_status()
        return missing

    # ── 執行紀錄 ──

    def start_run(self, trigger: str) -> int:
        [row] = self._insert("runs", [{"trigger": trigger}], returning=True)
        return row["id"]

    def finish_run(self, run_id: int, status: str, summary: dict[str, Any]) -> None:
        self._patch("runs", {"id": f"eq.{run_id}"}, {"status": status, "summary": summary, "finished_at": now_iso()})

    def record_source_run(
        self, run_id: int, source: str, status: str, job_count: int | None = None, new_count: int | None = None, error: str | None = None
    ) -> None:
        row = {"run_id": run_id, "source": source, "status": status, "job_count": job_count, "new_count": new_count, "error": error}
        self._insert("source_runs", [row])

    def source_history(self, source: str) -> SourceHistory:
        rows = self._get("source_runs", {"source": f"eq.{source}", "select": "status,job_count", "order": "created_at.desc", "limit": 20})
        ok_rows = [r for r in rows if r["status"] == "ok"]
        return SourceHistory(
            has_success=bool(ok_rows),
            last_status=rows[0]["status"] if rows else None,
            last_ok_count=ok_rows[0]["job_count"] if ok_rows else None,
        )

    # ── 職缺 ──

    def known_jobs(self, source: str) -> list[KnownJob]:
        select = "id,company,job_id,filter_status,posted_date,first_seen_at,closed_at"
        return [
            KnownJob(
                id=r["id"],
                company=r["company"],
                job_id=r["job_id"],
                filter_status=r["filter_status"],
                posted_date=date.fromisoformat(r["posted_date"]) if r["posted_date"] else None,
                first_seen_at=datetime.fromisoformat(r["first_seen_at"]),
                closed_at=datetime.fromisoformat(r["closed_at"]) if r["closed_at"] else None,
            )
            for r in self._get_all("jobs", {"source": f"eq.{source}", "select": select, "order": "id"})
        ]

    def insert_jobs(self, rows: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
        """寫入新職缺，回傳 (company, job_id) → 資料庫 id。"""
        ids = {}
        for chunk in _chunks(rows, CHUNK):
            for r in self._insert("jobs", chunk, returning=True, on_conflict="company,job_id"):
                ids[(r["company"], r["job_id"])] = r["id"]
        return ids

    def mark_seen(self, ids: Sequence[int]) -> None:
        """這次又看到的職缺：更新最後看到時間；之前被標記關閉的重新開啟。"""
        for chunk in _chunks(ids, CHUNK):
            self._patch("jobs", {"id": _in(chunk)}, {"last_seen_at": now_iso(), "closed_at": None})

    def mark_closed(self, ids: Sequence[int]) -> None:
        for chunk in _chunks(ids, CHUNK):
            self._patch("jobs", {"id": _in(chunk)}, {"closed_at": now_iso()})

    # ── 推播 ──

    def add_notifications(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """同一個職缺同一種通知已存在就略過；回傳真正新增的通知。"""
        added = []
        for chunk in _chunks(rows, CHUNK):
            added += self._insert("notifications", chunk, returning=True, on_conflict="job,kind", ignore_duplicates=True)
        return added

    def count_pending_notifications(self) -> dict[str, int]:
        rows = self._get_all("notifications", {"status": "eq.pending", "select": "kind", "order": "id"})
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        return counts

    # ── 底層 ──

    def _get(self, table: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        resp = self._client.get(f"/{table}", params=params)
        resp.raise_for_status()
        return resp.json()

    def _get_all(self, table: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        while True:
            page = self._get(table, {**params, "limit": PAGE_SIZE, "offset": len(rows)})
            rows += page
            if len(page) < PAGE_SIZE:
                return rows

    def _insert(
        self, table: str, rows: list[dict[str, Any]], *, returning: bool = False, on_conflict: str | None = None, ignore_duplicates: bool = False
    ) -> list[dict[str, Any]]:
        prefer = ["return=representation" if returning else "return=minimal"]
        params = {}
        if on_conflict:
            params["on_conflict"] = on_conflict
            prefer.append("resolution=ignore-duplicates" if ignore_duplicates else "resolution=merge-duplicates")
        resp = self._client.post(f"/{table}", params=params, json=rows, headers={"Prefer": ",".join(prefer)})
        resp.raise_for_status()
        return resp.json() if returning else []

    def _patch(self, table: str, filters: dict[str, str], values: dict[str, Any]) -> None:
        resp = self._client.patch(f"/{table}", params=filters, json=values, headers={"Prefer": "return=minimal"})
        resp.raise_for_status()


def _in(ids: Iterable[int]) -> str:
    return f"in.({','.join(map(str, ids))})"


def _chunks(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _error_code(resp: httpx.Response) -> str | None:
    try:
        return resp.json().get("code")
    except ValueError:
        return None
