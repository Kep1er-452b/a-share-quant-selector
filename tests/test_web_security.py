from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_server


def _headers():
    return {"X-Quant-Session": web_server.WEB_SESSION_TOKEN}


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
