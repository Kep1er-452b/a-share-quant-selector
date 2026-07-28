from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from threading import Lock
import time

from ops.events import OpsEvent, RetentionPolicy


class OpsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS ops_events (
                    event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, severity TEXT NOT NULL,
                    message TEXT NOT NULL, domain TEXT, market TEXT, module TEXT, job_id TEXT,
                    dataset TEXT, symbol TEXT, error_code TEXT, event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ops_events_time ON ops_events(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_ops_events_filters
                    ON ops_events(market, severity, domain, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_ops_events_job ON ops_events(job_id, timestamp DESC);
            """)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 30000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def append(self, event: OpsEvent) -> None:
        payload = event.to_dict()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ops_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    payload["event_id"], payload["timestamp"], payload["severity"], payload["message"],
                    payload["domain"], payload["market"], payload["module"], payload["job_id"],
                    payload["dataset"], payload["symbol"], payload["error_code"], payload["event_type"],
                    json.dumps(payload["details"], ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def query(self, *, limit: int = 100, offset: int = 0, **filters):
        limit, offset = int(limit), int(offset)
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("invalid event page")
        allowed = {
            "severity", "domain", "market", "module", "job_id", "dataset",
            "symbol", "error_code", "event_type", "since", "until",
        }
        unknown = set(filters) - allowed
        if unknown:
            raise ValueError(f"unknown event filters: {sorted(unknown)}")
        clauses, params = [], []
        since = self._normalize_time_filter(filters.pop("since", None), "since")
        until = self._normalize_time_filter(filters.pop("until", None), "until")
        if since and until and since > until:
            raise ValueError("since must not be later than until")
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)
        for key, value in filters.items():
            if value is not None:
                clauses.append(f"{key} = ?")
                params.append(str(value))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM ops_events{where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM ops_events{where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def _normalize_time_filter(value, name):
        if value in (None, ""):
            return None
        text = str(value).strip()
        if not text or len(text) > 64:
            raise ValueError(f"{name} must be a bounded ISO timestamp")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO timestamp") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()

    def prune(self, *, now: datetime, policy: RetentionPolicy, active_job_ids=()) -> int:
        cutoff = (now - timedelta(days=policy.task_days)).astimezone(timezone.utc).isoformat()
        active = tuple(str(item) for item in active_job_ids)
        sql = "DELETE FROM ops_events WHERE timestamp < ?"
        params: list[object] = [cutoff]
        if active:
            sql += f" AND (job_id IS NULL OR job_id NOT IN ({','.join('?' for _ in active)}))"
            params.extend(active)
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            return cursor.rowcount


class OpsRetentionMaintenance:
    """Run bounded event retention at most once per configured interval."""

    def __init__(
        self,
        store: OpsStore,
        *,
        active_job_ids: Callable[[], Iterable[str]],
        policy: RetentionPolicy | None = None,
        interval_seconds: float = 3600,
        max_active_jobs: int = 500,
        monotonic: Callable[[], float] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.active_job_ids = active_job_ids
        self.policy = policy or RetentionPolicy()
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.max_active_jobs = min(500, max(1, int(max_active_jobs)))
        self.monotonic = monotonic or time.monotonic
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._last_run = None
        self._lock = Lock()

    def run_if_due(self) -> dict:
        tick = float(self.monotonic())
        if self._last_run is not None and tick - self._last_run < self.interval_seconds:
            return {"ran": False, "deleted": 0, "active_jobs": 0}
        with self._lock:
            if self._last_run is not None and tick - self._last_run < self.interval_seconds:
                return {"ran": False, "deleted": 0, "active_jobs": 0}
            # Advance the gate before touching external providers so repeated
            # requests cannot hammer retention when a best-effort check fails.
            self._last_run = tick
            active = tuple(dict.fromkeys(
                str(job_id) for job_id in self.active_job_ids() if str(job_id)
            ))[: self.max_active_jobs]
            deleted = self.store.prune(
                now=self.now(),
                policy=self.policy,
                active_job_ids=active,
            )
            return {"ran": True, "deleted": int(deleted), "active_jobs": len(active)}
