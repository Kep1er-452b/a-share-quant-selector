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
