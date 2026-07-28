"""Read-only bridge over heterogeneous in-memory job registries."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from threading import RLock

from ops.events import redact


_FIELDS = (
    "job_id", "status", "created_at", "updated_at", "started_at", "finished_at",
    "elapsed_seconds", "progress_pct", "current_step", "current_stock", "domain",
    "market", "provider", "dataset", "symbol", "warning", "error", "error_code",
    "processed_count", "total_count", "success_count", "failed_count",
)
_ACTIVE = {"queued", "running", "cancelling"}


class TaskRegistry:
    def __init__(self, sources: Mapping[str, Callable[[], object]] | None = None, *, max_items=500):
        self.max_items = min(500, max(1, int(max_items)))
        self._sources: dict[str, Callable[[], object]] = dict(sources or {})
        self._lock = RLock()

    def register(self, task_type: str, provider: Callable[[], object]) -> None:
        task_type = str(task_type or "").strip()
        if not task_type or not callable(provider):
            raise ValueError("task source requires a name and callable provider")
        with self._lock:
            self._sources[task_type] = provider

    def snapshot(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        status: str | None = None,
        task_type: str | None = None,
        job_ids: Iterable[str] | None = None,
    ) -> dict:
        limit, offset = int(limit), int(offset)
        if not 1 <= limit <= min(500, self.max_items) or offset < 0:
            raise ValueError("invalid task page")
        selected_ids = {str(item) for item in (job_ids or ())}
        with self._lock:
            sources = list(self._sources.items())
        items, source_errors = [], []
        for kind, provider in sources:
            if task_type and kind != task_type:
                continue
            try:
                raw = provider()
            except Exception as exc:
                source_errors.append(redact({"task_type": kind, "error": str(exc)}))
                continue
            jobs = raw.values() if isinstance(raw, Mapping) else (raw or ())
            for job in jobs:
                if not isinstance(job, Mapping):
                    continue
                job_id = str(job.get("job_id") or job.get("id") or "").strip()
                if not job_id or (selected_ids and job_id not in selected_ids):
                    continue
                job_status = str(job.get("status") or "unknown")
                if status and job_status != status:
                    continue
                item = {field: job.get(field) for field in _FIELDS if field in job}
                item["job_id"] = job_id
                item["status"] = job_status
                item["task_type"] = kind
                items.append(redact(item))
        items.sort(
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or item["job_id"]),
            reverse=True,
        )
        total = len(items)
        return {
            "items": items[offset : offset + limit],
            "total": total,
            "active": sum(item["status"] in _ACTIVE for item in items),
            "limit": limit,
            "offset": offset,
            "source_errors": source_errors,
        }
