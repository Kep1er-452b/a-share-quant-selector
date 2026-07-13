from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_server


def test_market_capability_endpoint_is_read_only_and_json():
    client = web_server.app.test_client()

    assert client.get("/api/markets/capabilities").is_json
    # The app's global side-effect guard runs before Flask's method handling.
    assert client.post("/api/markets/capabilities").status_code == 403
