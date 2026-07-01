"""SQLite storage for Tushare extension datasets."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


class TushareExtStore:
    """Small repository around the local Tushare extension SQLite database."""

    def __init__(self, base_path: str | Path):
        path = Path(base_path)
        self.db_path = path if path.suffix else path / "tushare_ext.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS ext_dataset_rows (
                    dataset TEXT NOT NULL,
                    row_key TEXT NOT NULL,
                    ts_code TEXT,
                    trade_date TEXT,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (dataset, row_key)
                );
                CREATE INDEX IF NOT EXISTS idx_ext_rows_dataset_code_date
                    ON ext_dataset_rows(dataset, ts_code, trade_date);

                CREATE TABLE IF NOT EXISTS ext_sync_state (
                    dataset TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    start_date TEXT,
                    end_date TEXT,
                    status TEXT NOT NULL,
                    warning TEXT,
                    error TEXT,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (dataset, scope)
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _normalize_value(value) -> str:
        if value is None:
            return ""
        return str(value)

    def upsert_rows(
        self,
        dataset: str,
        rows: Iterable[dict],
        *,
        key_fields: Sequence[str],
        ts_code_field: str = "ts_code",
        trade_date_field: str = "trade_date",
    ) -> int:
        dataset = str(dataset or "").strip()
        if not dataset:
            raise ValueError("dataset is required")
        if not key_fields:
            raise ValueError("key_fields is required")

        prepared = []
        updated_at = self._now()
        for row in rows or []:
            payload = dict(row)
            row_key = "|".join(self._normalize_value(payload.get(field)) for field in key_fields)
            if not row_key.strip("|"):
                continue
            prepared.append(
                (
                    dataset,
                    row_key,
                    self._normalize_value(payload.get(ts_code_field)) or None,
                    self._normalize_value(payload.get(trade_date_field)) or None,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                    updated_at,
                )
            )

        if not prepared:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO ext_dataset_rows
                    (dataset, row_key, ts_code, trade_date, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset, row_key) DO UPDATE SET
                    ts_code = excluded.ts_code,
                    trade_date = excluded.trade_date,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                prepared,
            )
        return len(prepared)

    def query_rows(
        self,
        dataset: str,
        *,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int | None = None,
        descending: bool = True,
    ) -> list[dict]:
        clauses = ["dataset = ?"]
        params: list[object] = [dataset]
        if ts_code:
            clauses.append("ts_code = ?")
            params.append(ts_code)
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(end_date)
        order = "DESC" if descending else "ASC"
        sql = (
            "SELECT payload_json FROM ext_dataset_rows "
            f"WHERE {' AND '.join(clauses)} ORDER BY trade_date {order}, row_key {order}"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(int(limit), 0))
        with self.connect() as conn:
            return [json.loads(row["payload_json"]) for row in conn.execute(sql, params)]

    def latest_trade_date(self, dataset: str, *, ts_code: str | None = None) -> str | None:
        clauses = ["dataset = ?", "trade_date IS NOT NULL", "trade_date != ''"]
        params: list[object] = [dataset]
        if ts_code:
            clauses.append("ts_code = ?")
            params.append(ts_code)
        sql = f"SELECT MAX(trade_date) AS latest FROM ext_dataset_rows WHERE {' AND '.join(clauses)}"
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return row["latest"] if row and row["latest"] else None

    def set_sync_state(
        self,
        dataset: str,
        *,
        scope: str,
        status: str,
        start_date: str | None = None,
        end_date: str | None = None,
        warning: str | None = None,
        error: str | None = None,
        row_count: int = 0,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ext_sync_state
                    (dataset, scope, start_date, end_date, status, warning, error, row_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset, scope) DO UPDATE SET
                    start_date = excluded.start_date,
                    end_date = excluded.end_date,
                    status = excluded.status,
                    warning = excluded.warning,
                    error = excluded.error,
                    row_count = excluded.row_count,
                    updated_at = excluded.updated_at
                """,
                (
                    dataset,
                    scope,
                    start_date,
                    end_date,
                    status,
                    warning,
                    error,
                    int(row_count or 0),
                    self._now(),
                ),
            )

    def list_warnings(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT dataset, scope, status, start_date, end_date, warning
                FROM ext_sync_state
                WHERE warning IS NOT NULL AND warning != ''
                ORDER BY dataset, scope
                """
            ).fetchall()
        return [dict(row) for row in rows]
