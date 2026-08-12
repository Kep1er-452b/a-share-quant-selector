from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ops.events import OpsEvent, RetentionPolicy
from ops.logging import EventLogger
from ops.store import OpsStore
import ops.store as ops_store_module


NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)


def test_ops_event_redacts_tokens_before_storage(tmp_path):
    logger = EventLogger(OpsStore(tmp_path / "ops.sqlite"), tmp_path / "events.jsonl")
    logger.emit(message="request failed", details={"token": "secret-value", "status": 403})

    stored = logger.store.query(limit=1)["items"][0]
    assert "secret-value" not in json.dumps(stored)
    assert stored["details"]["token"] == "[REDACTED]"
    assert "secret-value" not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")


def test_ops_event_redacts_credentials_embedded_in_message_text(tmp_path):
    logger = EventLogger(OpsStore(tmp_path / "ops.sqlite"), tmp_path / "events.jsonl")
    logger.emit(
        message=(
            "request Authorization: Bearer top-secret-token "
            "TUSHARE_TOKEN=another-secret api_key=third-secret"
        )
    )

    stored = logger.store.query(limit=1)["items"][0]
    serialized = json.dumps(stored)
    assert "top-secret-token" not in serialized
    assert "another-secret" not in serialized
    assert "third-secret" not in serialized
    assert "[REDACTED]" in stored["message"]


def test_ops_store_filters_and_pages_structured_fields(tmp_path):
    store = OpsStore(tmp_path / "ops.sqlite")
    store.append(OpsEvent.create(message="hk warning", severity="warning", market="hong_kong", job_id="job-1"))
    store.append(OpsEvent.create(message="a info", severity="info", market="a_share", job_id="job-2"))

    payload = store.query(market="hong_kong", severity="warning", limit=50, offset=0)
    assert payload["total"] == 1
    assert payload["items"][0]["job_id"] == "job-1"


def test_ops_store_filters_an_inclusive_iso_time_range(tmp_path):
    store = OpsStore(tmp_path / "ops.sqlite")
    store.append(OpsEvent.create(
        message="before", timestamp="2026-06-30T23:59:59+00:00",
    ))
    store.append(OpsEvent.create(
        message="inside", timestamp="2026-07-05T12:00:00+00:00",
    ))
    store.append(OpsEvent.create(
        message="after", timestamp="2026-07-14T00:00:00+00:00",
    ))

    payload = store.query(
        since="2026-07-01T00:00:00Z",
        until="2026-07-13T23:59:59Z",
        limit=10,
    )

    assert [item["message"] for item in payload["items"]] == ["inside"]


def test_active_task_events_survive_pruning(tmp_path):
    store = OpsStore(tmp_path / "ops.sqlite")
    old = (NOW - timedelta(days=90)).isoformat()
    store.append(OpsEvent.create(message="active", job_id="active-1", timestamp=old))
    store.append(OpsEvent.create(message="old", job_id="old-1", timestamp=old))

    deleted = store.prune(
        now=NOW,
        policy=RetentionPolicy(task_days=30, performance_days=7),
        active_job_ids={"active-1"},
    )

    assert deleted == 1
    assert store.query(job_id="active-1", limit=10)["total"] == 1


def test_retention_maintenance_is_periodic_and_preserves_active_jobs(tmp_path):
    assert hasattr(ops_store_module, "OpsRetentionMaintenance")
    maintenance_type = ops_store_module.OpsRetentionMaintenance
    store = OpsStore(tmp_path / "ops.sqlite")
    old = (NOW - timedelta(days=90)).isoformat()
    store.append(OpsEvent.create(message="active", job_id="active-1", timestamp=old))
    store.append(OpsEvent.create(message="expired", job_id="expired-1", timestamp=old))
    ticks = iter((10.0, 20.0, 80.0))
    maintenance = maintenance_type(
        store,
        active_job_ids=lambda: {"active-1"},
        policy=RetentionPolicy(task_days=30, performance_days=7),
        interval_seconds=60,
        monotonic=lambda: next(ticks),
        now=lambda: NOW,
    )

    first = maintenance.run_if_due()
    second = maintenance.run_if_due()
    third = maintenance.run_if_due()

    assert first == {"ran": True, "deleted": 1, "active_jobs": 1}
    assert second == {"ran": False, "deleted": 0, "active_jobs": 0}
    assert third == {"ran": True, "deleted": 0, "active_jobs": 1}
    assert store.query(job_id="active-1", limit=10)["total"] == 1


def test_ops_store_serializes_runtime_detail_scalars(tmp_path):
    store = OpsStore(tmp_path / "ops.sqlite")
    store.append(OpsEvent.create(message="runtime", details={"at": NOW, "path": tmp_path}))

    details = store.query(limit=1)["items"][0]["details"]
    assert details["at"] == NOW.isoformat()
    assert details["path"] == str(tmp_path)


def test_redaction_preserves_mapping_keys_with_different_types():
    from ops.events import redact

    cleaned = redact({1: "integer", "1": "string"})

    assert cleaned == {"int:1": "integer", "str:1": "string"}
