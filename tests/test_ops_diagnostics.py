from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ops.diagnostics import DiagnosticExporter
from ops.events import OpsEvent
from ops.store import OpsStore
from ops.tasks import TaskRegistry


class StaticService:
    def __init__(self, payload):
        self.payload = payload

    def snapshot(self):
        return self.payload

    def summary(self):
        return self.payload


def test_diagnostic_export_contains_only_selected_sanitized_evidence(tmp_path):
    events = OpsStore(tmp_path / "ops.sqlite")
    events.append(
        OpsEvent.create(
            message="selected; failed at /Users/me/report.json with TUSHARE_TOKEN=plain-secret",
            job_id="job-1",
            details={"token": "event-secret", "report_path": "/Users/me/report.json"},
        )
    )
    events.append(OpsEvent.create(message="not-selected", job_id="job-2"))
    tasks = TaskRegistry()
    tasks.register(
        "diagnostic",
        lambda: {
            "job-1": {
                "job_id": "job-1",
                "status": "failed",
                "DEEPSEEK_API_KEY": "deep-secret",
                "file_path": "/Users/me/private.json",
            },
            "job-2": {"job_id": "job-2", "status": "completed"},
        },
    )
    exporter = DiagnosticExporter(
        output_dir=tmp_path / "exports",
        tasks=tasks,
        events=events,
        health=StaticService({"runtime_root": "/Users/me/app"}),
        performance=StaticService({"token": "perf-secret"}),
        environment={
            "TUSHARE_TOKEN": "tushare-secret",
            "DEEPSEEK_API_KEY": "deep-secret",
            "SAFE_FLAG": "1",
        },
    )

    archive = exporter.export(job_ids=["job-1"], limit=20)
    raw = archive.read_bytes()
    payload = json.loads(raw)

    assert payload["tasks"]["items"][0]["job_id"] == "job-1"
    assert payload["events"]["items"][0]["job_id"] == "job-1"
    assert b"job-2" not in raw
    for forbidden in (
        b"event-secret",
        b"deep-secret",
        b"tushare-secret",
        b"TUSHARE_TOKEN",
        b"DEEPSEEK_API_KEY",
        b"/Users/",
        b"plain-secret",
    ):
        assert forbidden not in raw
    assert payload["environment"] == {"SAFE_FLAG": "1"}


def test_diagnostic_export_rejects_unbounded_or_unknown_filters(tmp_path):
    exporter = DiagnosticExporter(
        output_dir=tmp_path,
        tasks=TaskRegistry(),
        events=OpsStore(tmp_path / "ops.sqlite"),
        health=StaticService({}),
        performance=StaticService({}),
    )

    for kwargs in ({"limit": 501}, {"event_filters": {"unknown": "value"}}):
        try:
            exporter.export(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe diagnostic filter was accepted")
