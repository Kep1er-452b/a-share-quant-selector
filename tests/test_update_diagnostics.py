import json
import sys
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pandas as pd
import pytest

import utils.update_diagnostics as diagnostics
import web_server
from utils.data_provider import BaseDataProvider
from utils.error_logging import sanitize_for_log
from utils.tushare_fetcher import TushareFetcher, TushareProviderError, classify_tushare_error


def _report(error_dir, error_id="deadbeef1234"):
    path = error_dir / f"20260619-120000-update-{error_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "error_id": error_id,
        "module": "update",
        "error_message": "invalid token",
        "context": {"stage": "preflight", "provider": "tushare"},
        "diagnostics": {"schema_version": 1, "auto_snapshot": {}, "runs": []},
    }), encoding="utf-8")
    return path


def _headers():
    return {"X-Quant-Session": web_server.WEB_SESSION_TOKEN}


def test_error_classification_covers_fatal_and_transient_errors():
    assert classify_tushare_error(RuntimeError("invalid token"))["code"] == "TOKEN_INVALID"
    assert classify_tushare_error(RuntimeError("没有访问该接口的权限"))["fatal"] is True
    assert classify_tushare_error(TimeoutError("timed out"))["code"] == "NETWORK_UNREACHABLE"
    assert classify_tushare_error(RuntimeError("每分钟最多访问该接口"))["code"] == "RATE_LIMITED"


def test_network_circuit_opens_after_eight_consecutive_failures():
    fetcher = TushareFetcher.__new__(TushareFetcher)
    fetcher._diagnostic_lock = Lock()
    fetcher._api_stats = Counter()
    fetcher._error_samples = []
    fetcher._network_outcomes = deque(maxlen=20)
    fetcher._network_consecutive_failures = 0
    fetcher._network_circuit_threshold = 8
    fetcher._network_circuit_ratio = 0.8
    fetcher._network_circuit_open = False

    for _ in range(7):
        fetcher._record_api_error("pro_bar", TimeoutError("timed out"), "000001")
        assert fetcher._network_circuit_open is False
    fetcher._record_api_error("pro_bar", TimeoutError("timed out"), "000001")

    assert fetcher._network_circuit_open is True
    assert len(fetcher._error_samples) == 8


def test_empty_response_is_not_counted_as_network_failure():
    fetcher = TushareFetcher.__new__(TushareFetcher)
    fetcher._diagnostic_lock = Lock()
    fetcher._api_stats = Counter()
    fetcher._network_outcomes = deque(maxlen=20)
    fetcher._network_consecutive_failures = 0

    fetcher._record_api_success("pro_bar", pd.DataFrame())

    assert fetcher._network_consecutive_failures == 0
    assert fetcher._api_stats["pro_bar.empty"] == 1


def test_preflight_auth_failure_writes_no_provider_csv(monkeypatch, tmp_path):
    fetcher = TushareFetcher.__new__(TushareFetcher)
    fetcher.token = "present"
    fetcher.token_source = "temporary"
    fetcher.ts = type("SDK", (), {"__version__": "test"})()
    fetcher._preflight_result = None
    fetcher._preflight_stock_basic_df = pd.DataFrame()
    fetcher._preflight_warnings = []
    fetcher.full_data_dir = tmp_path
    monkeypatch.setattr(
        fetcher,
        "_fetch_stock_basic_frame",
        lambda: (_ for _ in ()).throw(TushareProviderError("invalid token", code="TOKEN_INVALID", endpoint="stock_basic")),
    )

    with pytest.raises(TushareProviderError) as exc_info:
        fetcher.run_preflight()

    assert exc_info.value.code == "TOKEN_INVALID"
    assert not list(tmp_path.rglob("*.csv"))


def test_temporary_token_is_not_persisted_through_tushare_set_token(monkeypatch, tmp_path):
    class FakeTushare:
        __version__ = "test"

        @staticmethod
        def set_token(value):
            raise AssertionError("set_token must never be called")

        @staticmethod
        def pro_api(value):
            return object()

    monkeypatch.setitem(sys.modules, "tushare", FakeTushare)
    fetcher = TushareFetcher(str(tmp_path), token="temporary-secret", config={})

    assert fetcher.token == "temporary-secret"


def test_interrupted_sync_persists_actual_progress(tmp_path):
    provider_dir = tmp_path / "providers" / "tushare"
    provider = BaseDataProvider(str(provider_dir)).configure_storage(tmp_path, "tushare")
    provider._active_sync_context = {"latest_trade_date": "2026-06-18", "target_count": 10}
    provider._active_sync_progress = {"processed": 4, "total": 10, "success": 3, "failed": 1, "warning": 0}

    provider._persist_interrupted_provider_state(
        "all",
        None,
        [{"code": f"00000{index}"} for index in range(10)],
        TushareProviderError("network circuit", code="NETWORK_CIRCUIT_OPEN"),
    )
    state = json.loads((provider_dir / "provider_state.json").read_text(encoding="utf-8"))

    assert state["status"] == "failed"
    assert state["interrupted_progress"]["processed"] == 4
    assert state["interrupted_error"]["code"] == "NETWORK_CIRCUIT_OPEN"


def test_diagnostics_append_preserves_original_and_sanitizes_nested_secrets(monkeypatch, tmp_path):
    error_dir = tmp_path / "logs" / "errors"
    monkeypatch.setattr(diagnostics, "ERROR_DIR", error_dir)
    report = _report(error_dir)
    original = json.loads(report.read_text(encoding="utf-8"))

    diagnostics.append_diagnostic_run(report, {"diagnostic_id": "one", "token": "secret-one"})
    diagnostics.append_diagnostic_run(report, {"diagnostic_id": "two", "nested": {"api_key": "secret-two"}})
    payload = json.loads(report.read_text(encoding="utf-8"))

    assert {key: payload[key] for key in original if key != "diagnostics"} == {
        key: original[key] for key in original if key != "diagnostics"
    }
    assert [run["diagnostic_id"] for run in payload["diagnostics"]["runs"]] == ["one", "two"]
    assert payload["diagnostics"]["runs"][0]["token"] == "***REDACTED***"
    assert payload["diagnostics"]["runs"][1]["nested"]["api_key"] == "***REDACTED***"
    assert not list(error_dir.glob("*.tmp"))


def test_concurrent_diagnostic_appends_do_not_overwrite_history(monkeypatch, tmp_path):
    error_dir = tmp_path / "logs" / "errors"
    monkeypatch.setattr(diagnostics, "ERROR_DIR", error_dir)
    report = _report(error_dir)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda index: diagnostics.append_diagnostic_run(report, {"diagnostic_id": str(index)}), range(12)))

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert {run["diagnostic_id"] for run in payload["diagnostics"]["runs"]} == {str(index) for index in range(12)}


def test_atomic_append_failure_keeps_original_report(monkeypatch, tmp_path):
    error_dir = tmp_path / "logs" / "errors"
    monkeypatch.setattr(diagnostics, "ERROR_DIR", error_dir)
    report = _report(error_dir)
    original = report.read_bytes()
    monkeypatch.setattr(diagnostics.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        diagnostics.append_diagnostic_run(report, {"diagnostic_id": "failed-write"})

    assert report.read_bytes() == original
    assert not list(error_dir.glob("*.tmp"))


def test_report_resolution_rejects_path_traversal(monkeypatch, tmp_path):
    error_dir = tmp_path / "logs" / "errors"
    monkeypatch.setattr(diagnostics, "ERROR_DIR", error_dir)
    _report(error_dir)

    with pytest.raises(ValueError):
        diagnostics.resolve_update_error_report("../secret.json")


def test_report_resolution_rejects_corrupted_json(monkeypatch, tmp_path):
    error_dir = tmp_path / "logs" / "errors"
    monkeypatch.setattr(diagnostics, "ERROR_DIR", error_dir)
    error_dir.mkdir(parents=True)
    corrupt = error_dir / "20260619-120000-update-corrupt.json"
    corrupt.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON 损坏"):
        diagnostics.resolve_update_error_report(corrupt)


def test_missing_token_diagnostic_is_still_appended(monkeypatch, tmp_path):
    error_dir = tmp_path / "logs" / "errors"
    monkeypatch.setattr(diagnostics, "ERROR_DIR", error_dir)
    report = _report(error_dir)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(diagnostics, "load_config_file", lambda path: {"data_dir": str(tmp_path / "data")})

    result = diagnostics.run_update_diagnostics(report, mode="standard", project_root=tmp_path)
    payload = json.loads(report.read_text(encoding="utf-8"))

    assert result["status"] == "failed"
    assert result["primary_diagnosis"]["code"] == "TOKEN_MISSING"
    assert payload["diagnostics"]["runs"][-1]["diagnostic_id"] == result["diagnostic_id"]


def test_auto_snapshot_is_offline_and_records_repository_health(monkeypatch, tmp_path):
    error_dir = tmp_path / "logs" / "errors"
    monkeypatch.setattr(diagnostics, "ERROR_DIR", error_dir)
    report = _report(error_dir)
    monkeypatch.setattr(diagnostics, "load_active_provider", lambda root: {"active_provider": "tushare"})
    monkeypatch.setattr(diagnostics, "warehouse_summary", lambda root, provider: {"provider": provider, "stock_count": 5200, "coverage_ratio": 0.99})

    snapshot = diagnostics.attach_auto_snapshot(report, config={"data_dir": "data"}, project_root=tmp_path)

    assert snapshot["network_calls"] == 0
    assert snapshot["provider"]["tushare"]["coverage_ratio"] == 0.99
    assert json.loads(report.read_text(encoding="utf-8"))["diagnostics"]["auto_snapshot"]["network_calls"] == 0


def test_recursive_sensitive_field_sanitization():
    value = {"safe": [{"password": "p"}, {"client_secret": "s"}], "api_key": "k", "token_present": True, "token_source": "config"}
    assert sanitize_for_log(value) == {
        "safe": [{"password": "***REDACTED***"}, {"client_secret": "***REDACTED***"}],
        "api_key": "***REDACTED***",
        "token_present": True,
        "token_source": "config",
    }


def test_web_diagnostics_rejects_unauthorized_invalid_and_nonfailed_jobs(monkeypatch, tmp_path):
    client = web_server.app.test_client()
    job_id = web_server._create_update_job("tushare")
    try:
        response = client.post(f"/api/update/diagnostics/start/{job_id}", json={"mode": "standard"})
        assert response.status_code == 403

        response = client.post(
            f"/api/update/diagnostics/start/{job_id}",
            json={"mode": "invalid"},
            headers=_headers(),
        )
        assert response.status_code == 400

        response = client.post(
            f"/api/update/diagnostics/start/{job_id}",
            json={"mode": "standard"},
            headers=_headers(),
        )
        assert response.status_code == 409
    finally:
        with web_server.update_jobs_lock:
            web_server.update_jobs.pop(job_id, None)
            web_server.update_cancel_events.pop(job_id, None)


def test_update_api_accepts_akshare_provider(monkeypatch):
    class DeferredThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return None

    monkeypatch.setattr(web_server, "Thread", DeferredThread)
    client = web_server.app.test_client()
    response = client.post(
        "/api/update/start",
        json={"provider": "akshare", "max_stocks": 1},
        headers=_headers(),
    )
    payload = response.get_json()
    job_id = payload["job_id"]
    try:
        assert response.status_code == 200
        assert payload["data"]["provider"] == "akshare"
        assert payload["data"]["max_stocks"] == 1
    finally:
        with web_server.update_jobs_lock:
            web_server.update_jobs.pop(job_id, None)
            web_server.update_cancel_events.pop(job_id, None)


def test_three_provider_update_modal_and_diagnostics_are_wired():
    root = diagnostics.Path(__file__).resolve().parents[1]
    html = (root / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    javascript = (root / "web" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert 'data-provider="akshare"' in html
    assert 'data-provider="tencent"' in html
    assert 'data-provider="tushare"' in html
    assert 'id="update-diagnostic-standard-btn"' in html
    assert 'id="update-diagnostic-extended-btn"' in html
    assert "const archived = Boolean(status?.archived);" in javascript
    assert "selectUpdateProvider(button.dataset.provider)" in javascript
    assert "startUpdateDiagnostic('standard')" in javascript


def test_diagnostic_status_rejects_malformed_id():
    response = web_server.app.test_client().get("/api/update/diagnostics/status/not-safe")
    assert response.status_code == 400


def test_web_diagnostics_start_is_singleton_and_never_persists_temporary_token(monkeypatch, tmp_path):
    report = _report(tmp_path / "logs" / "errors")
    monkeypatch.setattr(web_server, "resolve_update_error_report", lambda value: report)

    class DeferredThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return None

    monkeypatch.setattr(web_server, "Thread", DeferredThread)
    job_id = web_server._create_update_job("tushare")
    web_server._update_update_job(job_id, status="failed", error_report_path=str(report))
    client = web_server.app.test_client()
    try:
        first = client.post(
            f"/api/update/diagnostics/start/{job_id}",
            json={"mode": "standard", "tushare_token": "temporary-secret"},
            headers=_headers(),
        )
        second = client.post(
            f"/api/update/diagnostics/start/{job_id}",
            json={"mode": "extended", "tushare_token": "another-secret"},
            headers=_headers(),
        )
        assert first.status_code == 200
        assert second.status_code == 409
        diagnostic_id = first.get_json()["diagnostic_id"]
        assert "token" not in json.dumps(web_server.diagnostic_jobs[diagnostic_id]).lower()
        assert "temporary-secret" not in json.dumps(web_server.diagnostic_jobs[diagnostic_id])
    finally:
        with web_server.update_jobs_lock:
            web_server.update_jobs.pop(job_id, None)
            web_server.update_cancel_events.pop(job_id, None)
        with web_server.diagnostic_jobs_lock:
            web_server.diagnostic_jobs.clear()
