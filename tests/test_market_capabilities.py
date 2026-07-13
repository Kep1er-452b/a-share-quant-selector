from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_server


def test_market_capabilities_keep_policy_separate():
    response = web_server.app.test_client().get("/api/markets/capabilities")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["markets"]["a_share"]["policy"] == "a_share"
    assert payload["markets"]["hong_kong"]["policy"] == "hong_kong"
    assert "price_limits" in payload["markets"]["a_share"]["features"]
    assert "price_limits" not in payload["markets"]["hong_kong"]["features"]


def test_market_capabilities_do_not_expose_credentials():
    payload = web_server.app.test_client().get("/api/markets/capabilities").get_json()
    serialized = str(payload).lower()

    assert "token" not in serialized
    assert "api_key" not in serialized
    assert payload["default_market"] == "a_share"
