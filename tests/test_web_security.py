from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import error_logging
import web_server


def _headers():
    return {"X-Quant-Session": web_server.WEB_SESSION_TOKEN}


def _write_provider_fixture(root, provider, *, code="000001", latest="2026-06-01", state=None):
    provider_root = root / "providers" / provider
    csv_dir = provider_root / code[:2]
    csv_dir.mkdir(parents=True, exist_ok=True)
    (csv_dir / f"{code}.csv").write_text(
        f"date,open,high,low,close,volume\n{latest},1,1,1,1,100\n",
        encoding="utf-8",
    )
    payload = {
        "provider": provider,
        "status": "ready",
        "latest_trade_date": latest,
        "target_count": 1,
        "success_count": 1,
        "failed_count": 0,
        "warning_count": 0,
        "coverage_ratio": 1.0,
        "is_complete": True,
    }
    payload.update(state or {})
    (provider_root / "provider_state.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_side_effect_select_requires_post_and_session_token():
    client = web_server.app.test_client()

    assert client.get("/api/select").status_code == 405

    response = client.post("/api/select", json={})
    assert response.status_code == 403


def test_job_status_rejects_malformed_ids():
    client = web_server.app.test_client()

    response = client.get("/api/select/status/not-a-uuid")
    assert response.status_code == 400

    response = client.get("/api/update/status/not-a-uuid")
    assert response.status_code == 400


def test_status_accepts_existing_short_job_ids():
    client = web_server.app.test_client()
    selection_job_id = web_server._create_selection_job(["main"], ["B1V242BStrategy"])
    update_job_id = web_server._create_update_job("tencent")

    try:
        response = client.get(f"/api/select/status/{selection_job_id}")
        assert response.status_code == 200
        assert response.get_json()["data"]["job_id"] == selection_job_id

        response = client.get(f"/api/update/status/{update_job_id}")
        assert response.status_code == 200
        assert response.get_json()["data"]["job_id"] == update_job_id
    finally:
        with web_server.selection_jobs_lock:
            web_server.selection_jobs.pop(selection_job_id, None)
        with web_server.update_jobs_lock:
            web_server.update_jobs.pop(update_job_id, None)
            web_server.update_cancel_events.pop(update_job_id, None)


def test_terminal_web_jobs_are_pruned_without_dropping_active_jobs(monkeypatch):
    monkeypatch.setattr(web_server, "MAX_RETAINED_TERMINAL_JOBS", 2)
    active_update_id = "active-update"
    stale_update_ids = [f"stale-update-{index}" for index in range(4)]
    active_selection_id = "active-selection"
    stale_selection_ids = [f"stale-selection-{index}" for index in range(4)]

    try:
        with web_server.update_jobs_lock:
            web_server.update_jobs.clear()
            web_server.update_cancel_events.clear()
            web_server.update_jobs[active_update_id] = {
                "job_id": active_update_id,
                "status": "running",
                "created_at": "2026-07-02T09:00:00",
                "updated_at": "2026-07-02T09:00:00",
            }
            web_server.update_cancel_events[active_update_id] = web_server.Event()
            for index, job_id in enumerate(stale_update_ids):
                web_server.update_jobs[job_id] = {
                    "job_id": job_id,
                    "status": "completed",
                    "created_at": f"2026-07-02T08:0{index}:00",
                    "updated_at": f"2026-07-02T08:0{index}:00",
                }
                web_server.update_cancel_events[job_id] = web_server.Event()

        with web_server.selection_jobs_lock:
            web_server.selection_jobs.clear()
            web_server.selection_jobs[active_selection_id] = {
                "job_id": active_selection_id,
                "status": "queued",
                "created_at": "2026-07-02T09:00:00",
                "updated_at": "2026-07-02T09:00:00",
            }
            for index, job_id in enumerate(stale_selection_ids):
                web_server.selection_jobs[job_id] = {
                    "job_id": job_id,
                    "status": "error",
                    "created_at": f"2026-07-02T08:0{index}:00",
                    "updated_at": f"2026-07-02T08:0{index}:00",
                }

        new_update_id = web_server._create_update_job("tushare")
        new_selection_id = web_server._create_selection_job(["main"], ["B1V242BStrategy"])

        with web_server.update_jobs_lock:
            assert active_update_id in web_server.update_jobs
            assert new_update_id in web_server.update_jobs
            terminal_updates = [
                job_id
                for job_id, job in web_server.update_jobs.items()
                if job.get("status") not in {"queued", "running"}
            ]
            assert terminal_updates == stale_update_ids[-2:]
            assert set(web_server.update_cancel_events) == {active_update_id, new_update_id, *terminal_updates}

        with web_server.selection_jobs_lock:
            assert active_selection_id in web_server.selection_jobs
            assert new_selection_id in web_server.selection_jobs
            terminal_selections = [
                job_id
                for job_id, job in web_server.selection_jobs.items()
                if job.get("status") not in {"queued", "running"}
            ]
            assert terminal_selections == stale_selection_ids[-2:]
    finally:
        with web_server.update_jobs_lock:
            web_server.update_jobs.clear()
            web_server.update_cancel_events.clear()
        with web_server.selection_jobs_lock:
            web_server.selection_jobs.clear()


def test_stock_snapshot_rows_are_sorted_by_code_before_pagination(monkeypatch):
    client = web_server.app.test_client()
    snapshot = {
        "stocks": [
            {"code": "300001", "name": "Gamma", "latest_price": 3, "latest_date": "2026-05-29", "market_cap": 300},
            {"code": "000002", "name": "Beta", "latest_price": 2, "latest_date": "2026-05-29", "market_cap": 200},
            {"code": "000001", "name": "Alpha", "latest_price": 1, "latest_date": "2026-05-29", "market_cap": 100},
        ]
    }
    monkeypatch.setattr(web_server, "load_market_caches", lambda data_dir: {"snapshot": snapshot})
    monkeypatch.setattr(web_server, "_load_stock_names", lambda: {})
    monkeypatch.setattr(web_server, "_load_stock_row_counts", lambda data_dir: {})

    response = client.get("/api/stocks?page=1&per_page=2")
    assert response.status_code == 200
    payload = response.get_json()
    assert [item["code"] for item in payload["data"]] == ["000001", "000002"]

    response = client.get("/api/stocks?page=2&per_page=2")
    assert response.status_code == 200
    payload = response.get_json()
    assert [item["code"] for item in payload["data"]] == ["300001"]


def test_formula_validate_endpoint_accepts_safe_formula():
    client = web_server.app.test_client()
    response = client.post(
        "/api/formula/validate",
        json={"formula": "CLOSE > MA(CLOSE, 20) AND J < 20"},
        headers=_headers(),
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True


def test_selection_options_groups_b1_strategies(monkeypatch):
    class EmptyManager:
        @staticmethod
        def list_all_stocks():
            return []

    monkeypatch.setattr(web_server, "_active_csv_manager", lambda: EmptyManager())
    monkeypatch.setattr(web_server, "_load_stock_names", lambda: {})
    client = web_server.app.test_client()

    response = client.get("/api/selection/options")

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert [group["key"] for group in data["strategy_groups"][:3]] == ["b1", "b2", "bowl"]
    strategy = next(item for item in data["strategies"] if item["name"] == "B1V24261Strategy")
    assert strategy["group"] == "b1"
    assert strategy["display_name"] == "V2.42.61"


def test_provider_activate_requires_session_token_and_valid_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(web_server, "_data_root_dir", lambda: tmp_path)
    monkeypatch.setattr(web_server, "_find_running_update_job", lambda: None)
    monkeypatch.setattr(web_server, "_find_running_job", lambda: None)
    client = web_server.app.test_client()

    response = client.post("/api/provider/activate", json={"provider": "akshare"})
    assert response.status_code == 403

    response = client.post("/api/provider/activate", json=[], headers=_headers())
    assert response.status_code == 400

    response = client.post("/api/provider/activate", json={"provider": "bad"}, headers=_headers())
    assert response.status_code == 400


def test_provider_activate_rejects_empty_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(web_server, "_data_root_dir", lambda: tmp_path)
    monkeypatch.setattr(web_server, "_find_running_update_job", lambda: None)
    monkeypatch.setattr(web_server, "_find_running_job", lambda: None)
    client = web_server.app.test_client()

    response = client.post("/api/provider/activate", json={"provider": "akshare"}, headers=_headers())

    assert response.status_code == 400
    assert "本地数据仓为空" in response.get_json()["error"]


def test_provider_activate_allows_akshare_and_tencent_when_warehouse_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(web_server, "_data_root_dir", lambda: tmp_path)
    monkeypatch.setattr(web_server, "_find_running_update_job", lambda: None)
    monkeypatch.setattr(web_server, "_find_running_job", lambda: None)
    _write_provider_fixture(tmp_path, "akshare", latest="2026-06-01")
    _write_provider_fixture(
        tmp_path,
        "tencent",
        latest="2026-05-30",
        state={
            "status": "partial",
            "target_count": 2,
            "success_count": 1,
            "failed_count": 1,
            "warning_count": 1,
            "coverage_ratio": 0.5,
            "is_complete": False,
        },
    )
    client = web_server.app.test_client()

    response = client.post("/api/provider/activate", json={"provider": "akshare"}, headers=_headers())

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["active_provider"]["active_provider"] == "akshare"

    response = client.post("/api/provider/activate", json={"provider": "tencent"}, headers=_headers())

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["active_provider"]["active_provider"] == "tencent"


def test_write_endpoints_validate_payload_shape_and_lengths():
    client = web_server.app.test_client()

    response = client.post("/api/update/start", json=[], headers=_headers())
    assert response.status_code == 400
    response = client.post(
        "/api/watchlist",
        json={"query": "0" * 81},
        headers=_headers(),
    )
    assert response.status_code == 400


def test_error_report_id_is_sanitized_before_path_join(monkeypatch, tmp_path):
    monkeypatch.setattr(error_logging, "ERROR_DIR", tmp_path)

    report_path = error_logging.write_error_report(
        "web/../server",
        RuntimeError("boom"),
        error_id="../outside/id",
    )

    assert report_path.parent == tmp_path
    assert ".." not in report_path.name
    assert "/" not in report_path.name
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["error_id"] == "---outside-id"
    assert payload["module"] == "web/../server"


def test_selection_endpoints_reject_running_update():
    client = web_server.app.test_client()
    job_id = web_server._create_update_job("tushare")
    web_server._update_update_job(job_id, status="running")

    try:
        sync_response = client.post("/api/select", json={}, headers=_headers())
        async_response = client.post("/api/select/start", json={}, headers=_headers())

        assert sync_response.status_code == 409
        assert async_response.status_code == 409
        assert "数据更新任务" in sync_response.get_json()["error"]
    finally:
        with web_server.update_jobs_lock:
            web_server.update_jobs.pop(job_id, None)
            web_server.update_cancel_events.pop(job_id, None)


def test_selection_job_surfaces_strategy_errors(monkeypatch, tmp_path):
    class Manager:
        data_dir = tmp_path

        @staticmethod
        def list_all_stocks():
            return ["000001"]

    monkeypatch.setattr(web_server, "_active_csv_manager", lambda: Manager())
    monkeypatch.setattr(web_server, "_load_stock_names", lambda: {"000001": "Test"})
    monkeypatch.setattr(web_server, "_get_web_selection_settings", lambda: {
        "backend": "sequential",
        "max_workers": 1,
        "chunk_size": 1,
    })
    monkeypatch.setattr(web_server, "_resolve_selection_backend", lambda *args: "sequential")
    monkeypatch.setattr(web_server, "build_worker_context", lambda *args, **kwargs: {})
    monkeypatch.setattr(web_server, "_save_selection_markdown", lambda *args, **kwargs: str(tmp_path / "result.md"))
    monkeypatch.setattr(web_server, "process_selection_chunk", lambda *args, **kwargs: {
        "processed_count": 1,
        "valid_count": 1,
        "skipped_count": 0,
        "results_by_strategy": {"B1V242BStrategy": []},
        "error_counts": {"B1V242BStrategy": 1},
        "error_details": [{
            "code": "000001",
            "name": "Test",
            "strategy": "B1V242BStrategy",
            "error": "boom",
            "type": "RuntimeError",
        }],
        "last_processed_code": "000001",
        "last_processed_name": "Test",
    })
    job_id = web_server._create_selection_job(["main"], ["B1V242BStrategy"])

    try:
        web_server._run_selection_job(job_id, ["main"], ["B1V242BStrategy"])
        with web_server.selection_jobs_lock:
            job = dict(web_server.selection_jobs[job_id])

        assert job["status"] == "completed_with_warnings"
        assert job["error_counts"] == {"B1V242BStrategy": 1}
        assert job["error_details"][0]["error"] == "boom"
    finally:
        with web_server.selection_jobs_lock:
            web_server.selection_jobs.pop(job_id, None)


def test_update_cancel_requires_session_token_and_marks_running_job():
    client = web_server.app.test_client()
    job_id = web_server._create_update_job("akshare")
    web_server._update_update_job(job_id, status="running")

    try:
        response = client.post(f"/api/update/cancel/{job_id}")
        assert response.status_code == 403

        response = client.post(
            f"/api/update/cancel/{job_id}",
            headers=_headers(),
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["success"] is True
        assert payload["data"]["cancel_requested"] is True
        assert payload["data"]["current_step"] == "正在停止此次更新"
        assert web_server.update_cancel_events[job_id].is_set()
        assert any(
            "保留日志" in item["message"]
            for item in payload["data"]["logs"]
        )
    finally:
        with web_server.update_jobs_lock:
            web_server.update_jobs.pop(job_id, None)
            web_server.update_cancel_events.pop(job_id, None)


def test_update_cancel_releases_failed_job_without_overwriting_error():
    client = web_server.app.test_client()
    job_id = web_server._create_update_job("tencent")
    web_server._update_update_job(
        job_id,
        status="failed",
        error="WAF 501",
        error_report_path="/tmp/update-error.json",
    )

    try:
        response = client.post(
            f"/api/update/cancel/{job_id}",
            headers=_headers(),
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["data"]["status"] == "failed"
        assert payload["data"]["error"] == "WAF 501"
        assert payload["data"]["error_report_path"] == "/tmp/update-error.json"
        assert web_server.update_cancel_events[job_id].is_set() is False
    finally:
        with web_server.update_jobs_lock:
            web_server.update_jobs.pop(job_id, None)
            web_server.update_cancel_events.pop(job_id, None)


def test_ops_cancel_uses_per_job_events_for_selection_diagnostic_and_wyckoff():
    assert hasattr(web_server, "selection_cancel_events")
    assert hasattr(web_server, "diagnostic_cancel_events")
    assert hasattr(web_server, "wyckoff_cancel_events")
    client = web_server.app.test_client()
    selection_id = web_server._create_selection_job(["main"], ["B1V242BStrategy"])
    diagnostic_id = web_server._create_diagnostic_job(
        {"job_id": "source-update", "error_report_path": "safe-report.json"},
        "standard",
    )
    wyckoff_id = "00000000-0000-4000-8000-000000000001"
    with web_server.wyckoff_jobs_lock:
        web_server.wyckoff_jobs[wyckoff_id] = {
            "job_id": wyckoff_id,
            "status": "running",
            "created_at": "2026-07-13 12:00:00",
            "updated_at": "2026-07-13 12:00:00",
        }
        web_server.wyckoff_cancel_events[wyckoff_id] = web_server.Event()

    try:
        for task_type, job_id in (
            ("selection", selection_id),
            ("diagnostic", diagnostic_id),
            ("wyckoff", wyckoff_id),
        ):
            assert client.post(f"/api/ops/tasks/{task_type}/{job_id}/cancel").status_code == 403
            response = client.post(
                f"/api/ops/tasks/{task_type}/{job_id}/cancel",
                headers=_headers(),
            )
            assert response.status_code == 200
            assert response.get_json()["data"]["status"] == "cancelling"

        assert web_server.selection_cancel_events[selection_id].is_set()
        assert web_server.diagnostic_cancel_events[diagnostic_id].is_set()
        assert web_server.wyckoff_cancel_events[wyckoff_id].is_set()
        assert web_server.halt_event.is_set() is False
    finally:
        with web_server.selection_jobs_lock:
            web_server.selection_jobs.pop(selection_id, None)
            web_server.selection_cancel_events.pop(selection_id, None)
        with web_server.diagnostic_jobs_lock:
            web_server.diagnostic_jobs.pop(diagnostic_id, None)
            web_server.diagnostic_cancel_events.pop(diagnostic_id, None)
        with web_server.wyckoff_jobs_lock:
            web_server.wyckoff_jobs.pop(wyckoff_id, None)
            web_server.wyckoff_cancel_events.pop(wyckoff_id, None)


def test_api_requests_record_bounded_performance_signals():
    before = web_server.ops_performance.summary()
    before_calls = before.get("signals", {}).get("api_call_count", {}).get("value", 0)

    response = web_server.app.test_client().get("/api/system_status")

    assert response.status_code == 200
    payload = web_server.ops_performance.summary()
    signals = payload["signals"]
    assert signals["api_call_count"]["value"] >= before_calls + 1
    assert signals["api_latency_ms"]["status"] == "available"
    assert signals["response_payload_bytes"]["status"] == "available"
    assert signals["retained_task_count"]["status"] == "available"
    assert signals["retained_event_count"]["status"] == "available"
    assert signals["cache_hit_rate"]["status"] in {"available", "unavailable"}


def test_pre_cancelled_selection_diagnostic_and_wyckoff_do_not_start_work(monkeypatch):
    assert hasattr(web_server, "selection_cancel_events")
    selection_id = web_server._create_selection_job(["main"], ["B1V242BStrategy"])
    diagnostic_id = web_server._create_diagnostic_job(
        {"job_id": "source-update", "error_report_path": "safe-report.json"},
        "standard",
    )
    wyckoff_id = "00000000-0000-4000-8000-000000000002"
    now = "2026-07-13 12:00:00"
    with web_server.wyckoff_jobs_lock:
        web_server.wyckoff_jobs[wyckoff_id] = {
            "job_id": wyckoff_id,
            "query": "000001",
            "status": "queued",
            "created_at": now,
            "updated_at": now,
        }
        web_server.wyckoff_cancel_events[wyckoff_id] = web_server.Event()
    web_server.selection_cancel_events[selection_id].set()
    web_server.diagnostic_cancel_events[diagnostic_id].set()
    web_server.wyckoff_cancel_events[wyckoff_id].set()

    monkeypatch.setattr(web_server, "_active_csv_manager", lambda: (_ for _ in ()).throw(AssertionError("selection work started")))
    monkeypatch.setattr(web_server, "run_update_diagnostics", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("diagnostic work started")))
    monkeypatch.setattr(web_server, "WyckoffPipeline", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Wyckoff work started")))
    try:
        web_server._run_selection_job(selection_id, ["main"], ["B1V242BStrategy"])
        web_server._run_update_diagnostic_job(diagnostic_id, "safe-report.json", "standard", "")
        web_server._run_wyckoff_job(wyckoff_id, "000001")

        assert web_server.selection_jobs[selection_id]["status"] == "cancelled"
        assert web_server.diagnostic_jobs[diagnostic_id]["status"] == "cancelled"
        assert web_server.wyckoff_jobs[wyckoff_id]["status"] == "cancelled"
    finally:
        with web_server.selection_jobs_lock:
            web_server.selection_jobs.pop(selection_id, None)
            web_server.selection_cancel_events.pop(selection_id, None)
        with web_server.diagnostic_jobs_lock:
            web_server.diagnostic_jobs.pop(diagnostic_id, None)
            web_server.diagnostic_cancel_events.pop(diagnostic_id, None)
        with web_server.wyckoff_jobs_lock:
            web_server.wyckoff_jobs.pop(wyckoff_id, None)
            web_server.wyckoff_cancel_events.pop(wyckoff_id, None)


def test_pre_cancelled_update_job_finishes_as_cancelled(monkeypatch, tmp_path):
    job_id = web_server._create_update_job("akshare")
    web_server.update_cancel_events[job_id].set()
    report_path = tmp_path / "cancelled-update.json"
    monkeypatch.setattr(web_server, "write_error_report", lambda *args, **kwargs: report_path)
    monkeypatch.setattr(web_server, "_append_system_log", lambda *args, **kwargs: None)

    try:
        web_server._run_update_job(job_id, "akshare", "")

        with web_server.update_jobs_lock:
            job = dict(web_server.update_jobs[job_id])
        assert job["status"] == "cancelled"
        assert job["current_step"] == "更新已停止"
        assert job["error_report_path"] == str(report_path)
        assert any("已写入的数据和现有日志均已保留" in item["message"] for item in job["logs"])
    finally:
        with web_server.update_jobs_lock:
            web_server.update_jobs.pop(job_id, None)
            web_server.update_cancel_events.pop(job_id, None)


def test_wyckoff_job_honors_global_halt_before_pipeline(monkeypatch, tmp_path):
    job_id = "wyckoff-halt-test"
    now = "2026-07-02 12:00:00"
    with web_server.wyckoff_jobs_lock:
        web_server.wyckoff_jobs[job_id] = {
            "job_id": job_id,
            "query": "000001",
            "status": "queued",
            "current_step": "排队",
            "message": "",
            "progress_pct": 0,
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": None,
            "error_report_path": None,
        }

    class FailIfConstructed:
        def __init__(self, *args, **kwargs):
            raise AssertionError("halted Wyckoff job should not construct pipeline")

    monkeypatch.setattr(web_server, "WyckoffPipeline", FailIfConstructed)
    monkeypatch.setattr(web_server, "write_error_report", lambda *args, **kwargs: tmp_path / "wyckoff-error.json")
    web_server.halt_event.set()

    try:
        web_server._run_wyckoff_job(job_id, "000001")
        with web_server.wyckoff_jobs_lock:
            job = dict(web_server.wyckoff_jobs[job_id])
        assert job["status"] == "cancelled"
        assert job["current_step"] == "系统已急停"
    finally:
        web_server.halt_event.clear()
        with web_server.wyckoff_jobs_lock:
            web_server.wyckoff_jobs.pop(job_id, None)
