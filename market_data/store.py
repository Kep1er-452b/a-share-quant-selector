"""Schema-versioned SQLite storage shared by non-legacy market domains."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
MAX_QUERY_LIMIT = 2_000
_PERFORMANCE_OBSERVER = None


def configure_store_performance_observer(observer=None):
    """Install one best-effort low-cardinality database timing observer."""
    global _PERFORMANCE_OBSERVER
    if observer is not None and not callable(observer):
        raise TypeError("store performance observer must be callable")
    previous = _PERFORMANCE_OBSERVER
    _PERFORMANCE_OBSERVER = observer
    return previous


def _observe_db_duration(operation: str, started_at: float) -> None:
    observer = _PERFORMANCE_OBSERVER
    if observer is None:
        return
    try:
        observer(
            "db_duration_ms",
            max(0.0, (time.perf_counter() - started_at) * 1000),
            {"operation": operation},
        )
    except Exception:
        pass


class DomainStore:
    """Transactional JSON row store with indexed domain query columns."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._ensure_schema()

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.connection = conn
        return conn

    @contextmanager
    def connect(self):
        conn = self._connection()
        try:
            yield conn
            if conn.in_transaction:
                conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise

    def close(self) -> None:
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            conn.close()
            self._local.connection = None

    def _ensure_schema(self) -> None:
        # Compatibility must be checked before changing journaling mode or
        # creating tables. Opening a newer database is a read-only rejection.
        with self.connect() as conn:
            has_schema_meta = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
            ).fetchone()
            if has_schema_meta:
                row = conn.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()
                if row is not None:
                    try:
                        existing_version = int(row["value"])
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError("invalid domain store schema version") from exc
                    if existing_version > SCHEMA_VERSION:
                        raise RuntimeError(
                            "domain store schema is newer than this application: "
                            f"{existing_version} > {SCHEMA_VERSION}"
                        )
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dataset_rows (
                    dataset TEXT NOT NULL,
                    row_key TEXT NOT NULL,
                    symbol TEXT,
                    data_date TEXT,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (dataset, row_key)
                );
                CREATE INDEX IF NOT EXISTS idx_dataset_rows_symbol_date
                    ON dataset_rows(dataset, symbol, data_date);
                CREATE INDEX IF NOT EXISTS idx_dataset_rows_date
                    ON dataset_rows(dataset, data_date);

                CREATE TABLE IF NOT EXISTS sync_state (
                    dataset TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cursor TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    warning TEXT,
                    error TEXT,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (dataset, scope)
                );
                """
            )
            conn.execute(
                """
                INSERT INTO schema_meta(key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field} is required")
        return text

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _row_key(cls, row: Mapping[str, object], key_fields: Sequence[str]) -> str:
        values = []
        for field in key_fields:
            field_name = cls._required_text(field, "key field")
            values.append(cls._required_text(row.get(field_name), field_name))
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"), default=str)

    def upsert_rows(
        self,
        dataset: str,
        rows: Iterable[Mapping[str, object]],
        key_fields: Sequence[str],
        symbol_field: str | None = None,
        date_field: str | None = None,
    ) -> int:
        """Upsert one batch atomically and return its prepared row count."""

        dataset_id = self._required_text(dataset, "dataset")
        fields = tuple(key_fields or ())
        if not fields:
            raise ValueError("key_fields is required")

        updated_at = self._now()
        prepared: list[tuple[object, ...]] = []
        for source_row in rows or ():
            if not isinstance(source_row, Mapping):
                raise TypeError("each row must be a mapping")
            payload = dict(source_row)
            prepared.append(
                (
                    dataset_id,
                    self._row_key(payload, fields),
                    self._optional_text(payload.get(symbol_field))
                    if symbol_field
                    else None,
                    self._optional_text(payload.get(date_field)) if date_field else None,
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                    updated_at,
                )
            )

        if not prepared:
            return 0

        started_at = time.perf_counter()
        try:
            with self.connect() as conn:
                conn.executemany(
                    """
                    INSERT INTO dataset_rows
                        (dataset, row_key, symbol, data_date, payload_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dataset, row_key) DO UPDATE SET
                        symbol = excluded.symbol,
                        data_date = excluded.data_date,
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    prepared,
                )
        finally:
            _observe_db_duration("upsert_rows", started_at)
        return len(prepared)

    def query_rows(
        self,
        dataset: str,
        *,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 200,
        offset: int = 0,
        descending: bool = True,
    ) -> list[dict]:
        dataset_id = self._required_text(dataset, "dataset")
        try:
            page_limit = int(limit)
            page_offset = int(offset)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit and offset must be integers") from exc
        if page_limit < 0 or page_limit > MAX_QUERY_LIMIT:
            raise ValueError(f"limit must be between 0 and {MAX_QUERY_LIMIT}")
        if page_offset < 0:
            raise ValueError("offset must be greater than or equal to 0")

        clauses = ["dataset = ?"]
        params: list[object] = [dataset_id]
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(str(symbol))
        if start_date is not None:
            clauses.append("data_date >= ?")
            params.append(str(start_date))
        if end_date is not None:
            clauses.append("data_date <= ?")
            params.append(str(end_date))
        order = "DESC" if bool(descending) else "ASC"
        sql = (
            "SELECT payload_json FROM dataset_rows "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY data_date {order}, row_key {order} LIMIT ? OFFSET ?"
        )
        params.extend((page_limit, page_offset))
        started_at = time.perf_counter()
        try:
            with self.connect() as conn:
                rows = conn.execute(sql, params).fetchall()
        finally:
            _observe_db_duration("query_rows", started_at)
        return [json.loads(row["payload_json"]) for row in rows]

    def query_all_rows(
        self,
        dataset: str,
        *,
        symbol: str | None = None,
        max_rows: int = 100_000,
        descending: bool = True,
    ) -> list[dict]:
        """Read a deliberately bounded dataset through one shared pager."""

        maximum = int(max_rows)
        if maximum < 1:
            raise ValueError("max_rows must be positive")
        rows: list[dict] = []
        offset = 0
        while offset < maximum:
            page_limit = min(MAX_QUERY_LIMIT, maximum - offset)
            page = self.query_rows(
                dataset,
                symbol=symbol,
                limit=page_limit,
                offset=offset,
                descending=descending,
            )
            rows.extend(page)
            if len(page) < page_limit:
                return rows
            offset += len(page)
        if self.count_rows(dataset, symbol=symbol) > len(rows):
            raise RuntimeError(f"{dataset} exceeds the bounded row limit {maximum}")
        return rows

    def count_rows(
        self,
        dataset: str,
        *,
        symbol: str | None = None,
    ) -> int:
        dataset_id = self._required_text(dataset, "dataset")
        clauses = ["dataset = ?"]
        params: list[object] = [dataset_id]
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(str(symbol))
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM dataset_rows WHERE {' AND '.join(clauses)}",
                params,
            ).fetchone()
        return int(row[0] or 0)

    def max_data_date(self, dataset: str, *, symbol: str | None = None) -> str | None:
        dataset_id = self._required_text(dataset, "dataset")
        clauses = ["dataset = ?", "data_date IS NOT NULL"]
        params: list[object] = [dataset_id]
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(str(symbol))
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT MAX(data_date) FROM dataset_rows WHERE {' AND '.join(clauses)}",
                params,
            ).fetchone()
        return self._optional_text(row[0]) if row else None

    def storage_signature(self) -> tuple[tuple[str, int, int], ...]:
        """Return a cheap signature that changes when the SQLite store changes."""

        paths = (self.db_path, Path(f"{self.db_path}-wal"))
        result = []
        for path in paths:
            try:
                stat = path.stat()
                result.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
            except FileNotFoundError:
                result.append((path.name, 0, 0))
        return tuple(result)

    def query_latest_rows(
        self,
        dataset: str,
        *,
        rows_per_symbol: int = 1,
        end_date: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        """Return at most the newest two rows for each symbol in a bounded page.

        ``limit`` and ``offset`` paginate symbols rather than history rows, so
        callers never need to transfer an entire time series into Python just
        to derive a latest-market snapshot.
        """

        dataset_id = self._required_text(dataset, "dataset")
        end_text = self._optional_text(end_date)
        try:
            symbol_limit = int(limit)
            symbol_offset = int(offset)
            history_limit = int(rows_per_symbol)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "limit, offset, and rows_per_symbol must be integers"
            ) from exc
        if symbol_limit < 1 or symbol_limit > MAX_QUERY_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_QUERY_LIMIT}")
        if symbol_offset < 0:
            raise ValueError("offset must be greater than or equal to 0")
        if history_limit < 1 or history_limit > 2:
            raise ValueError("rows_per_symbol must be between 1 and 2")

        date_filter = " AND data_date <= ?" if end_text else ""
        sql = f"""
            WITH selected_symbols AS (
                SELECT symbol
                FROM dataset_rows
                WHERE dataset = ? AND symbol IS NOT NULL{date_filter}
                GROUP BY symbol
                ORDER BY symbol ASC
                LIMIT ? OFFSET ?
            ), latest AS (
                SELECT rows.payload_json, rows.symbol, rows.data_date, rows.row_key
                FROM selected_symbols AS selected
                INNER JOIN dataset_rows AS rows
                    ON rows.rowid IN (
                        SELECT candidate.rowid
                        FROM dataset_rows AS candidate
                        WHERE candidate.dataset = ?
                          AND candidate.symbol = selected.symbol
                          {"AND candidate.data_date <= ?" if end_text else ""}
                        ORDER BY candidate.data_date DESC, candidate.row_key DESC
                        LIMIT ?
                    )
            )
            SELECT payload_json
            FROM latest
            ORDER BY symbol ASC, data_date DESC, row_key DESC
        """
        selected_args = [dataset_id]
        if end_text:
            selected_args.append(end_text)
        selected_args.extend((symbol_limit, symbol_offset, dataset_id))
        if end_text:
            selected_args.append(end_text)
        selected_args.append(history_limit)
        started_at = time.perf_counter()
        try:
            with self.connect() as conn:
                rows = conn.execute(
                    sql,
                    tuple(selected_args),
                ).fetchall()
        finally:
            _observe_db_duration("query_latest_rows", started_at)
        return [json.loads(row["payload_json"]) for row in rows]

    def set_sync_state(
        self,
        dataset: str,
        *,
        status: str,
        scope: str = "default",
        cursor: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        warning: str | None = None,
        error: str | None = None,
        row_count: int = 0,
        details: Mapping[str, object] | None = None,
    ) -> None:
        dataset_id = self._required_text(dataset, "dataset")
        scope_id = self._required_text(scope, "scope")
        state_status = self._required_text(status, "status")
        details_json = (
            json.dumps(
                dict(details),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            if details is not None
            else None
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state
                    (dataset, scope, status, cursor, start_date, end_date,
                     warning, error, row_count, details_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset, scope) DO UPDATE SET
                    status = excluded.status,
                    cursor = excluded.cursor,
                    start_date = excluded.start_date,
                    end_date = excluded.end_date,
                    warning = excluded.warning,
                    error = excluded.error,
                    row_count = excluded.row_count,
                    details_json = excluded.details_json,
                    updated_at = excluded.updated_at
                """,
                (
                    dataset_id,
                    scope_id,
                    state_status,
                    self._optional_text(cursor),
                    self._optional_text(start_date),
                    self._optional_text(end_date),
                    self._optional_text(warning),
                    self._optional_text(error),
                    int(row_count or 0),
                    details_json,
                    self._now(),
                ),
            )

    def get_sync_state(self, dataset: str, scope: str = "default") -> dict | None:
        dataset_id = self._required_text(dataset, "dataset")
        scope_id = self._required_text(scope, "scope")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT dataset, scope, status, cursor, start_date, end_date,
                       warning, error, row_count, details_json, updated_at
                FROM sync_state
                WHERE dataset = ? AND scope = ?
                """,
                (dataset_id, scope_id),
            ).fetchone()
        if row is None:
            return None
        state = dict(row)
        state["details"] = (
            json.loads(state.pop("details_json"))
            if state["details_json"] is not None
            else None
        )
        return state

    def health(self, *, deep: bool = False) -> dict:
        with self.connect() as conn:
            integrity = (
                conn.execute("PRAGMA integrity_check").fetchone()[0]
                if deep
                else "not_checked"
            )
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            version_row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if deep:
                row_count = conn.execute("SELECT COUNT(*) FROM dataset_rows").fetchone()[0]
                dataset_count = conn.execute(
                    "SELECT COUNT(DISTINCT dataset) FROM dataset_rows"
                ).fetchone()[0]
            else:
                row_count = None
                dataset_count = None
        sidecar_size = sum(
            path.stat().st_size
            for path in (
                Path(f"{self.db_path}-wal"),
                Path(f"{self.db_path}-shm"),
            )
            if path.exists()
        )
        main_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "integrity": str(integrity),
            "integrity_checked": bool(deep),
            "schema_version": int(version_row[0]) if version_row else 0,
            "journal_mode": str(journal_mode).lower(),
            "dataset_count": int(dataset_count) if dataset_count is not None else None,
            "row_count": int(row_count) if row_count is not None else None,
            "db_size_bytes": main_size + sidecar_size,
            "db_main_size_bytes": main_size,
            "db_sidecar_size_bytes": sidecar_size,
            "db_path": str(self.db_path),
        }
