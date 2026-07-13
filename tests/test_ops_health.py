from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ops.health import HealthService
from ops.performance import PerformanceRecorder
from ops.tasks import TaskRegistry


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class FakeDomainStore:
    def __init__(self, states):
        self.states = states

    def get_sync_state(self, dataset, scope="default"):
        return self.states.get((dataset, scope))

    def health(self, *, deep=False):
        return {
            "integrity": "ok" if deep else "not_checked",
            "integrity_checked": deep,
            "schema_version": 1,
            "journal_mode": "wal",
            "dataset_count": 2 if deep else None,
            "row_count": 10 if deep else None,
            "db_size_bytes": 4096,
            "db_path": "/Users/private/data.sqlite",
        }


def test_task_registry_unifies_bounded_sanitized_snapshots():
    registry = TaskRegistry(max_items=2)
    registry.register(
        "update",
        lambda: {
            "one": {"job_id": "one", "status": "running", "token": "secret"},
            "two": {"job_id": "two", "status": "completed", "progress_pct": 100},
            "three": {"job_id": "three", "status": "failed", "error": "boom"},
        },
    )

    payload = registry.snapshot(limit=2)

    assert payload["total"] == 3
    assert len(payload["items"]) == 2
    assert payload["active"] == 1
    assert all(item["task_type"] == "update" for item in payload["items"])
    assert "secret" not in str(payload)


def test_health_reports_frequency_aware_freshness_without_paths():
    old = (NOW - timedelta(days=60)).isoformat()
    store = FakeDomainStore(
        {
            ("cn_gdp", "default"): {"status": "completed", "updated_at": old},
            ("hk_daily", "default"): {"status": "completed", "updated_at": old},
        }
    )
    service = HealthService(
        stores={"economy": store, "hong_kong": store},
        datasets={
            "cn_gdp": {"store": "economy", "frequency": "quarterly"},
            "hk_daily": {"store": "hong_kong", "frequency": "daily"},
        },
        now=lambda: NOW,
    )

    payload = service.snapshot()

    assert payload["datasets"]["cn_gdp"]["frequency"] == "quarterly"
    assert payload["datasets"]["cn_gdp"]["freshness"] == "fresh"
    assert payload["datasets"]["hk_daily"]["freshness"] == "stale"
    assert "/Users/private" not in str(payload)
    assert payload["stores"]["economy"]["integrity"] == "not_checked"


def test_health_includes_safe_operational_checks_for_disk_tasks_cache_and_circuits():
    assert "checks" in inspect.signature(HealthService).parameters
    service = HealthService(
        stores={},
        datasets={},
        checks={
            "disk": lambda: {"status": "ready", "free_bytes": 1024},
            "tasks": lambda: {
                "status": "warning",
                "active_count": 2,
                "queue_depth": 1,
                "conflict_detected": True,
            },
            "a_share_cache": lambda: {
                "status": "warning",
                "refresh_pending": True,
                "snapshot_stock_count": 5200,
            },
            "api_circuits": lambda: {
                "status": "warning",
                "providers": {"tushare": {"state": "open", "evidence": "provider_state"}},
            },
        },
        now=lambda: NOW,
    )

    payload = service.snapshot()

    assert payload["disk"]["free_bytes"] == 1024
    assert payload["tasks"]["queue_depth"] == 1
    assert payload["a_share_cache"]["refresh_pending"] is True
    assert payload["api_circuits"]["providers"]["tushare"]["state"] == "open"
    assert payload["status"] == "warning"
    assert "path" not in str(payload).lower()


def test_performance_recorder_keeps_bounded_numeric_summaries():
    recorder = PerformanceRecorder(max_samples=3)
    for value in (10, 20, 30, 40):
        recorder.record("api_latency_ms", value, labels={"endpoint": "hk_daily"})

    payload = recorder.summary()

    assert payload["retained_samples"] == 3
    metric = payload["metrics"]["api_latency_ms"]
    assert metric == {"count": 3, "min": 20.0, "max": 40.0, "avg": 30.0, "latest": 40.0}


def test_performance_contract_reports_available_gauges_and_honest_unavailable_signals():
    recorder = PerformanceRecorder(max_samples=5)
    assert hasattr(recorder, "register_gauge")
    assert hasattr(recorder, "mark_unavailable")
    recorder.record("api_call_count", 1, labels={"route": "/api/stocks", "status": "2xx"})
    recorder.record("api_latency_ms", 12.5, labels={"route": "/api/stocks", "status": "2xx"})
    recorder.record("response_payload_bytes", 256, labels={"route": "/api/stocks", "status": "2xx"})
    recorder.record("db_duration_ms", 2.5, labels={"operation": "query_rows"})
    recorder.register_gauge("retained_task_count", lambda: 7)
    recorder.register_gauge("retained_event_count", lambda: 42)
    recorder.mark_unavailable("cache_hit_rate", "no generic cache observer")

    payload = recorder.summary()

    assert payload["signals"]["api_call_count"]["status"] == "available"
    assert payload["signals"]["api_call_count"]["value"] == 1.0
    assert payload["signals"]["api_latency_ms"]["latest"] == 12.5
    assert payload["signals"]["db_duration_ms"]["status"] == "available"
    assert payload["signals"]["response_payload_bytes"]["status"] == "available"
    assert payload["signals"]["retained_task_count"] == {"status": "available", "value": 7.0}
    assert payload["signals"]["retained_event_count"] == {"status": "available", "value": 42.0}
    assert payload["signals"]["cache_hit_rate"] == {
        "status": "unavailable",
        "reason": "no generic cache observer",
    }


def test_performance_recorder_rejects_unbounded_or_secret_labels():
    recorder = PerformanceRecorder()

    with pytest.raises(ValueError, match="labels"):
        recorder.record("api_latency_ms", 1, labels={f"label_{index}": index for index in range(20)})
    with pytest.raises(ValueError, match="sensitive"):
        recorder.record("api_latency_ms", 1, labels={"token": "secret-value"})
