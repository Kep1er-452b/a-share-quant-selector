from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def test_provider_activate_switches_and_reports_stale_warnings(monkeypatch, tmp_path):
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

    response = client.post("/api/provider/activate", json={"provider": "tencent"}, headers=_headers())

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["active_provider"]["active_provider"] == "tencent"
    assert any("落后于本地最新" in warning for warning in payload["data"]["warnings"])
    assert any("覆盖率" in warning for warning in payload["data"]["warnings"])


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
