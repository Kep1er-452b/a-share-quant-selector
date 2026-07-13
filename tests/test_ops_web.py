from __future__ import annotations

from pathlib import Path
import sys

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web_api.ops import OpsServiceContainer, create_ops_blueprint


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Tasks:
    def snapshot(self, **kwargs):
        return {"items": [{"job_id": "job-1"}], "total": 1, **kwargs}


class Events:
    def __init__(self):
        self.filters = None

    def query(self, **kwargs):
        self.filters = kwargs
        return {"items": [], "total": 0, "limit": kwargs["limit"], "offset": kwargs["offset"]}


class Snapshot:
    def snapshot(self):
        return {"status": "ready"}


class Performance:
    def summary(self):
        return {"retained_samples": 0, "metrics": {}}


class Diagnostics:
    def __init__(self, root):
        self.root = root

    def export(self, **_kwargs):
        path = self.root / "diagnostic-safe.json"
        path.write_text("{}", encoding="utf-8")
        return path


class Canceller:
    def __init__(self):
        self.calls = []

    def __call__(self, job_id):
        self.calls.append(job_id)
        return {"job_id": job_id, "status": "cancelling"}


def client_and_events(tmp_path):
    events = Events()
    cancellers = {
        task_type: Canceller()
        for task_type in ("selection", "update", "diagnostic", "wyckoff", "domain_sync")
    }
    services = OpsServiceContainer(
        tasks=Tasks(),
        events=events,
        health=Snapshot(),
        performance=Performance(),
        diagnostics=Diagnostics(tmp_path),
        task_cancellers=cancellers,
    )
    app = Flask(__name__)
    app.register_blueprint(create_ops_blueprint(services, session_token="session-secret"))
    return app.test_client(), events, cancellers


def test_ops_read_routes_use_bounded_structured_filters(tmp_path):
    client, events, _cancellers = client_and_events(tmp_path)

    response = client.get("/api/ops/events?market=hong_kong&severity=warning&limit=50")

    assert response.status_code == 200
    assert response.get_json()["limit"] == 50
    assert events.filters == {
        "market": "hong_kong",
        "severity": "warning",
        "limit": 50,
        "offset": 0,
    }
    assert client.get("/api/ops/tasks?limit=500").status_code == 200
    assert client.get("/api/ops/health").get_json()["status"] == "ready"
    assert client.get("/api/ops/performance").status_code == 200


def test_ops_routes_reject_unknown_filters_and_unbounded_limits(tmp_path):
    client, _events, _cancellers = client_and_events(tmp_path)

    assert client.get("/api/ops/events?unknown=x").status_code == 400
    assert client.get("/api/ops/events?limit=501").status_code == 400
    assert client.get("/api/ops/tasks?limit=0").status_code == 400


def test_diagnostic_export_requires_session_token_and_hides_local_path(tmp_path):
    client, _events, _cancellers = client_and_events(tmp_path)

    assert client.post("/api/ops/diagnostics/export", json={}).status_code == 403
    assert client.post(
        "/api/ops/diagnostics/export",
        json=[],
        headers={"X-Quant-Session": "session-secret"},
    ).status_code == 400
    response = client.post(
        "/api/ops/diagnostics/export",
        json={"job_ids": ["job-1"], "limit": 50},
        headers={"X-Quant-Session": "session-secret"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["filename"] == "diagnostic-safe.json"
    assert "path" not in payload
    assert str(tmp_path) not in str(payload)


def test_ops_events_accept_bounded_time_and_structured_filters(tmp_path):
    client, events, _cancellers = client_and_events(tmp_path)

    response = client.get(
        "/api/ops/events?since=2026-07-01T00:00:00Z&until=2026-07-13T23:59:59Z"
        "&domain=macro&module=sync&dataset=cn_gdp&symbol=000001.SZ"
        "&job_id=job-1&error_code=RATE_LIMIT"
    )

    assert response.status_code == 200
    assert events.filters["since"] == "2026-07-01T00:00:00Z"
    assert events.filters["until"] == "2026-07-13T23:59:59Z"
    assert events.filters["error_code"] == "RATE_LIMIT"


def test_ops_task_cancel_requires_token_and_validates_action_boundary(tmp_path):
    client, _events, cancellers = client_and_events(tmp_path)
    url = "/api/ops/tasks/domain_sync/job-1/cancel"

    assert client.post(url).status_code == 403
    assert client.post(
        "/api/ops/tasks/unknown/job-1/cancel",
        headers={"X-Quant-Session": "session-secret"},
    ).status_code == 400
    assert client.post(
        "/api/ops/tasks/domain_sync/not%20valid/cancel",
        headers={"X-Quant-Session": "session-secret"},
    ).status_code == 400
    response = client.post(url, headers={"X-Quant-Session": "session-secret"})

    assert response.status_code == 200
    assert response.get_json() == {"job_id": "job-1", "status": "cancelling"}
    assert cancellers["domain_sync"].calls == ["job-1"]


def test_ops_task_cancel_dispatches_every_registered_task_type(tmp_path):
    client, _events, cancellers = client_and_events(tmp_path)

    for task_type in ("selection", "update", "diagnostic", "wyckoff", "domain_sync"):
        response = client.post(
            f"/api/ops/tasks/{task_type}/job-1/cancel",
            headers={"X-Quant-Session": "session-secret"},
        )
        assert response.status_code == 200
        assert response.get_json()["status"] == "cancelling"
        assert cancellers[task_type].calls == ["job-1"]


def test_web_server_routes_ops_sqlite_through_approved_data_directory():
    source = (PROJECT_ROOT / "web_server.py").read_text(encoding="utf-8")

    assert "platform_runtime_paths().ops_store_path()" in source
    assert 'OpsStore(platform_runtime_paths().logs_root / "ops.sqlite")' not in source
