"""Frequency-aware local dataset and database health snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping


_MAX_AGE_DAYS = {
    "intraday": 1,
    "daily": 3,
    "weekly": 10,
    "monthly": 45,
    "quarterly": 140,
    "annual": 430,
    "irregular": 365,
}


class HealthService:
    def __init__(
        self,
        *,
        stores: Mapping[str, object],
        datasets: Mapping[str, Mapping[str, object]],
        checks: Mapping[str, Callable[[], Mapping[str, object]]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.stores = dict(stores)
        self.datasets = {str(key): dict(value) for key, value in datasets.items()}
        self.checks = dict(checks or {})
        reserved = {"status", "stores", "datasets", "storage_bytes", "deep"}
        collisions = reserved.intersection(self.checks)
        if collisions:
            raise ValueError(
                f"health check names are reserved: {', '.join(sorted(collisions))}"
            )
        self.now = now or (lambda: datetime.now(timezone.utc))

    def snapshot(self, *, deep: bool = False) -> dict:
        stores, storage_bytes = {}, 0
        for name, store in self.stores.items():
            try:
                raw = dict(store.health(deep=deep))
                raw.pop("db_path", None)
                storage_bytes += int(raw.get("db_size_bytes") or 0)
                stores[name] = raw
            except Exception as exc:
                stores[name] = self._safe_error(exc)

        datasets = {}
        for dataset, metadata in self.datasets.items():
            store_name = str(metadata.get("store") or "")
            frequency = str(metadata.get("frequency") or "irregular").lower()
            scope = str(metadata.get("scope") or "default")
            state = None
            error = None
            try:
                state = self.stores[store_name].get_sync_state(dataset, scope)
            except Exception as exc:
                error = self._safe_error(exc)["error"]
            datasets[dataset] = self._dataset_health(
                frequency=frequency,
                state=state,
                error=error,
            )
        checks = {}
        for name, callback in self.checks.items():
            try:
                payload = dict(callback())
                payload.pop("path", None)
                payload.pop("db_path", None)
                checks[name] = payload
            except Exception as exc:
                checks[name] = self._safe_error(exc)
        warning_states = {"warning", "error", "critical", "open"}
        has_warning = any(item.get("status") == "error" for item in stores.values())
        has_warning = has_warning or any(
            str(item.get("status") or "").lower() in warning_states
            for item in checks.values()
        )
        return {
            "status": "warning" if has_warning else "ready",
            "stores": stores,
            "datasets": datasets,
            "storage_bytes": storage_bytes,
            "deep": bool(deep),
            **checks,
        }

    @staticmethod
    def _safe_error(exc: Exception) -> dict:
        return {
            "status": "error",
            "error": "本地健康检查失败；请查看仅限本机的系统日志。",
            "error_code": exc.__class__.__name__,
        }

    def _dataset_health(self, *, frequency, state, error):
        max_age = _MAX_AGE_DAYS.get(frequency, _MAX_AGE_DAYS["irregular"])
        if error:
            return {"frequency": frequency, "freshness": "unknown", "status": "error", "error": error}
        state = state or {}
        updated_at = state.get("updated_at")
        freshness, age_days = "unknown", None
        if updated_at:
            try:
                updated = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                age_days = max(0.0, (self.now() - updated).total_seconds() / 86400)
                freshness = "fresh" if age_days <= max_age else "stale"
            except (TypeError, ValueError):
                pass
        return {
            "frequency": frequency,
            "freshness": freshness,
            "max_age_days": max_age,
            "age_days": round(age_days, 2) if age_days is not None else None,
            "status": state.get("status") or "empty",
            "cursor": state.get("cursor"),
            "updated_at": updated_at,
            "warning": state.get("warning"),
            "error": state.get("error"),
        }
