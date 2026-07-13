from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_server
import web_api.domain_data as domain_data
from market_data.store import DomainStore


_REAL_STORE_FACTORY = domain_data._store


def _temporary_store_factory(tmp_path):
    stores = {}

    def factory(domain):
        stores.setdefault(domain, DomainStore(tmp_path / f"{domain}.sqlite"))
        return stores[domain]

    return factory


def test_domain_store_factory_uses_platform_path_authority(tmp_path, monkeypatch):
    paths = domain_data.runtime_paths().for_environment(
        project_root=tmp_path / "bundle",
        packaged=True,
        system="Windows",
        environ={"LOCALAPPDATA": str(tmp_path / "LocalAppData")},
    )
    monkeypatch.setattr(domain_data, "runtime_paths", lambda: paths)
    _REAL_STORE_FACTORY.cache_clear()
    try:
        assert _REAL_STORE_FACTORY("macro").db_path == paths.domain_store_path("macro")
    finally:
        _REAL_STORE_FACTORY.cache_clear()


import pytest


@pytest.fixture(autouse=True)
def isolate_domain_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(domain_data, "_store", _temporary_store_factory(tmp_path))
    with domain_data._SYNC_LOCK:
        domain_data._SYNC_JOBS.clear()
        domain_data._SYNC_CANCEL.clear()
        domain_data._ACTIVE_SYNC_DOMAINS.clear()
    yield
    with domain_data._SYNC_LOCK:
        domain_data._SYNC_JOBS.clear()
        domain_data._SYNC_CANCEL.clear()
        domain_data._ACTIVE_SYNC_DOMAINS.clear()


def test_domain_list_rejects_unbounded_limit():
    response = web_server.app.test_client().get("/api/futures/contracts?limit=999999")
    assert response.status_code == 400


def test_sync_start_requires_session_token():
    response = web_server.app.test_client().post("/api/sync/start", json={"domain": "macro"})
    assert response.status_code == 403


def test_sync_start_reports_missing_local_token_without_exposing_value(monkeypatch):
    monkeypatch.setattr(domain_data, "load_config_file", lambda: {})
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    response = web_server.app.test_client().post(
        "/api/sync/start",
        json={"domain": "macro"},
        headers={"X-Quant-Session": web_server.WEB_SESSION_TOKEN},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == "TOKEN_MISSING"
    assert "token_present" not in str(payload).lower()


def test_domain_status_exposes_empty_local_state_without_invented_zeroes():
    response = web_server.app.test_client().get("/api/domain-status/hong_kong")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["domain"] == "hong_kong"
    assert payload["state"] == "empty"
    assert "token" not in str(payload).lower()


def test_sync_job_pruning_is_bounded_and_preserves_active_jobs():
    with domain_data._SYNC_LOCK:
        domain_data._SYNC_JOBS.clear()
        domain_data._SYNC_CANCEL.clear()


def test_domain_sync_event_observer_is_best_effort_and_receives_context():
    observed = []
    previous = domain_data._SYNC_EVENT_OBSERVER
    try:
        domain_data.configure_sync_event_observer(
            lambda job_id, domain, event: observed.append((job_id, domain, event))
        )
        domain_data._observe_sync_event(
            "job-1", "macro", {"phase": "terminal", "status": "completed"}
        )
        assert observed == [
            ("job-1", "macro", {"phase": "terminal", "status": "completed"})
        ]
    finally:
        domain_data.configure_sync_event_observer(previous)


def test_sync_start_rejects_second_active_job_for_the_same_domain(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "local-test-token")
    with domain_data._SYNC_LOCK:
        domain_data._ACTIVE_SYNC_DOMAINS["macro"] = "existing-job"

    response = web_server.app.test_client().post(
        "/api/sync/start",
        json={"domain": "macro"},
        headers={"X-Quant-Session": web_server.WEB_SESSION_TOKEN},
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["code"] == "DOMAIN_SYNC_ACTIVE"
    assert payload["job_id"] == "existing-job"


def test_sync_start_enforces_global_domain_worker_budget(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "local-test-token")
    with domain_data._SYNC_LOCK:
        for index in range(domain_data._MAX_ACTIVE_SYNC_JOBS):
            domain_data._ACTIVE_SYNC_DOMAINS[f"domain-{index}"] = f"job-{index}"

    response = web_server.app.test_client().post(
        "/api/sync/start",
        json={"domain": "futures"},
        headers={"X-Quant-Session": web_server.WEB_SESSION_TOKEN},
    )

    assert response.status_code == 429
    assert response.get_json()["code"] == "SYNC_BUDGET_EXHAUSTED"
    assert domain_data._SYNC_EXECUTOR._max_workers == domain_data._MAX_ACTIVE_SYNC_JOBS


def test_sync_terminal_cleanup_releases_domain_even_after_unexpected_failure(monkeypatch):
    job_id = "job-failure"
    cancel = domain_data.Event()
    with domain_data._SYNC_LOCK:
        domain_data._SYNC_JOBS[job_id] = {
            "job_id": job_id,
            "domain": "macro",
            "status": "queued",
            "events": [],
            "result": None,
        }
        domain_data._SYNC_CANCEL[job_id] = cancel
        domain_data._ACTIVE_SYNC_DOMAINS["macro"] = job_id

    def fail_run(*_args, **_kwargs):
        raise RuntimeError("unexpected worker failure")

    monkeypatch.setattr(domain_data.SyncEngine, "run", fail_run)
    domain_data._run_sync_job(
        job_id,
        domain_data.SyncRequest(domain="macro"),
        object(),
        object(),
        object(),
        cancel,
    )

    with domain_data._SYNC_LOCK:
        assert domain_data._SYNC_JOBS[job_id]["status"] == "error"
        assert domain_data._SYNC_JOBS[job_id]["result"]["error"] == "unexpected worker failure"
        assert "macro" not in domain_data._ACTIVE_SYNC_DOMAINS
        assert job_id not in domain_data._SYNC_CANCEL
        for index in range(domain_data._MAX_TERMINAL_SYNC_JOBS + 7):
            job_id = f"terminal-{index:03d}"
            domain_data._SYNC_JOBS[job_id] = {"status": "completed"}
            domain_data._SYNC_CANCEL[job_id] = object()
        domain_data._SYNC_JOBS["active"] = {"status": "running"}
        domain_data._SYNC_CANCEL["active"] = object()

        domain_data._prune_sync_jobs_locked()

        terminal_ids = [job_id for job_id in domain_data._SYNC_JOBS if job_id != "active"]
        assert len(terminal_ids) == domain_data._MAX_TERMINAL_SYNC_JOBS
        assert terminal_ids[0] == "terminal-007"
        assert "active" in domain_data._SYNC_JOBS
        assert "active" in domain_data._SYNC_CANCEL

        domain_data._SYNC_JOBS.clear()
        domain_data._SYNC_CANCEL.clear()
