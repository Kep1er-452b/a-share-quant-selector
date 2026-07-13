from pathlib import Path
import sys

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data.capabilities import market_capabilities
from web_api.equities import create_equities_blueprint


class FakeEquityService:
    def __init__(self, market):
        self.market = market
        self.started = []

    def overview(self):
        return {"instrument_count": 1}

    def list_instruments(self, query="", limit=50, offset=0):
        symbol = "00700.HK" if self.market == "hong_kong" else "000001.SZ"
        return {"items": [{"symbol": symbol}], "total": 1, "limit": limit, "offset": offset}

    def instrument_detail(self, symbol, limit=260, adjustment="raw"):
        return {"symbol": symbol, "items": [{"close": 420.0}]}

    def heatmap(self):
        return {"groups": []}

    def selection_options(self):
        return {
            "strategies": [
                {"name": "B1 V2.42.61", "scope": "a_share_only"},
                {"name": "shared_trend", "scope": "market_neutral"},
            ]
        }

    def strategy_scope(self, strategy):
        return {
            "B1 V2.42.61": "a_share_only",
            "shared_trend": "market_neutral",
        }.get(strategy)

    def start_selection(self, payload):
        self.started.append(dict(payload))
        return {"job_id": "select-1", "status": "queued"}

    def selection_status(self, job_id):
        return {"job_id": job_id, "status": "completed", "results": []}

    def watchlist(self):
        return {"items": []}

    def start_wyckoff(self, payload):
        return {"job_id": "wyckoff-1", "status": "queued", "symbol": payload["symbol"]}

    def wyckoff_status(self, job_id):
        return {"job_id": job_id, "status": "completed"}


def make_client(services=None):
    app = Flask(__name__)
    app.register_blueprint(
        create_equities_blueprint(
            services=services
            or {
                "a_share": FakeEquityService("a_share"),
                "hong_kong": FakeEquityService("hong_kong"),
            },
            capabilities=market_capabilities(),
        )
    )
    return app.test_client()


def test_hong_kong_stock_detail_formats_hkd():
    response = make_client().get("/api/equities/hong_kong/instrument/00700.HK")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["market"] == "hong_kong"
    assert payload["currency"] == "HKD"
    assert payload["symbol"] == "00700.HK"


def test_market_explicit_read_routes_keep_market_context():
    client = make_client()

    for suffix in ("overview", "instruments", "heatmap", "selection/options", "watchlist"):
        response = client.get(f"/api/equities/a_share/{suffix}")
        assert response.status_code == 200
        assert response.get_json()["market"] == "a_share"


def test_selection_rejects_a_share_only_strategy_for_hong_kong():
    response = make_client().post(
        "/api/equities/hong_kong/selection/start",
        json={"strategies": ["B1 V2.42.61"]},
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "STRATEGY_NOT_SUPPORTED"


def test_hong_kong_selection_allows_declared_market_neutral_strategy():
    response = make_client().post(
        "/api/equities/hong_kong/selection/start",
        json={"strategies": ["shared_trend"]},
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["market"] == "hong_kong"
    assert payload["job_id"] == "select-1"


def test_selection_status_and_wyckoff_routes_are_market_explicit():
    client = make_client()

    selection = client.get("/api/equities/hong_kong/selection/status/select-1")
    wyckoff = client.post(
        "/api/equities/hong_kong/wyckoff/start", json={"symbol": "00700.HK"}
    )
    wyckoff_status = client.get(
        "/api/equities/hong_kong/wyckoff/status/wyckoff-1"
    )

    assert selection.get_json()["market"] == "hong_kong"
    assert wyckoff.status_code == 202
    assert wyckoff.get_json()["currency"] == "HKD"
    assert wyckoff_status.get_json()["market"] == "hong_kong"


def test_unadapted_write_returns_explicit_capability_error():
    service = FakeEquityService("hong_kong")
    service.start_wyckoff = None
    client = make_client({"hong_kong": service, "a_share": FakeEquityService("a_share")})

    response = client.post(
        "/api/equities/hong_kong/wyckoff/start", json={"symbol": "00700.HK"}
    )

    assert response.status_code == 501
    payload = response.get_json()
    assert payload["code"] == "CAPABILITY_UNAVAILABLE"
    assert payload["capability"] == "wyckoff_start"


def test_unknown_market_and_invalid_pagination_are_rejected():
    client = make_client()

    assert client.get("/api/equities/us/overview").status_code == 404
    response = client.get("/api/equities/hong_kong/instruments?limit=99999")
    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_REQUEST"

