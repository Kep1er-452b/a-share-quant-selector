"""Allowlist-based, path-free diagnostic evidence exports."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
from typing import Mapping
import uuid


_SECRET_KEYS = ("token", "api_key", "secret", "password", "webhook", "credential")
_PATH_KEYS = ("path", "root", "directory", "folder", "cwd", "home")
_EVENT_FILTERS = {
    "severity", "domain", "market", "module", "job_id", "dataset", "symbol",
    "error_code", "event_type",
}


def _scrub(value, key=""):
    normalized = str(key).lower().replace("-", "_")
    if any(part in normalized for part in (*_SECRET_KEYS, *_PATH_KEYS)):
        return None
    if isinstance(value, Mapping):
        cleaned = {}
        for item_key, item in value.items():
            name = str(item_key)
            normalized_name = name.lower().replace("-", "_")
            if any(part in normalized_name for part in (*_SECRET_KEYS, *_PATH_KEYS)):
                continue
            cleaned[name] = _scrub(item, name)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    if isinstance(value, Path):
        return "[PATH REDACTED]"
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in ("tushare_token", "deepseek_api_key", "bearer ", "sk-")):
            return "[REDACTED]"
        if (
            value.startswith(("/", "file://"))
            or "/Users/" in value
            or "/home/" in value
            or re.search(r"[A-Za-z]:[\\/]", value)
        ):
            return "[PATH REDACTED]"
    return value


class DiagnosticExporter:
    def __init__(
        self,
        *,
        output_dir,
        tasks,
        events,
        health,
        performance,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.tasks = tasks
        self.events = events
        self.health = health
        self.performance = performance
        self.environment = dict(environment) if environment is not None else {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "python_version": platform.python_version(),
        }

    def export(
        self,
        filters: Mapping[str, object] | None = None,
        *,
        job_ids=None,
        event_filters=None,
        limit=200,
    ) -> Path:
        filters = dict(filters or {})
        job_ids = list(job_ids if job_ids is not None else filters.get("job_ids") or [])
        event_filters = dict(event_filters if event_filters is not None else filters.get("event_filters") or {})
        limit = int(filters.get("limit", limit))
        if not 1 <= limit <= 500:
            raise ValueError("diagnostic limit must be between 1 and 500")
        unknown = set(event_filters) - _EVENT_FILTERS
        if unknown:
            raise ValueError(f"unknown diagnostic event filters: {sorted(unknown)}")
        if len(job_ids) > 100 or any(not isinstance(item, str) for item in job_ids):
            raise ValueError("job_ids must contain at most 100 strings")

        tasks = self.tasks.snapshot(limit=500, job_ids=job_ids or None)
        if job_ids:
            event_items = []
            for job_id in job_ids:
                page = self.events.query(job_id=job_id, limit=limit, offset=0, **event_filters)
                event_items.extend(page["items"])
                if len(event_items) >= limit:
                    break
            events = {"items": event_items[:limit], "total": len(event_items[:limit]), "limit": limit, "offset": 0}
        else:
            events = self.events.query(limit=limit, offset=0, **event_filters)
        payload = _scrub({
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tasks": tasks,
            "events": events,
            "health": self.health.snapshot(),
            "performance": self.performance.summary(),
            "environment": self.environment,
        })
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / f"diagnostic-{uuid.uuid4().hex}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temporary, target)
        return target
