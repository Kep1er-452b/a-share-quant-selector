from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

import web_server
import web_api.domain_data as domain_data
from market_data.commodity_providers import (
    CommodityHTTPError,
    SinaCFDProvider,
)
from market_data.commodity_ratios import (
    RATIO_DEFINITIONS,
    build_ratio_series,
    calculate_ratio,
    convert_price,
)
from market_data.global_commodities import (
    COMMODITY_SERIES,
    DATASET,
    DOMAIN,
    commodity_catalog,
    normalize_commodity_rows,
    series_spec,
    CommodityService,
)
from market_data.models import SyncRequest
from market_data.store import DomainStore
from market_data.sync_engine import SyncEngine


def _row(series_id, day, close, unit, **extra):
    return {
        "series_id": series_id,
        "session_date": day,
        "price_basis": "close",
        "definition_version": "sina-cfd-v1",
        "normalized_price": close,
        "normalized_unit": unit,
        "raw_close": close,
        "raw_unit": unit,
        "quality_status": "ok",
        "finality": "final",
        **extra,
    }


def test_unit_conversions_are_explicit_and_do_not_guess_copper_scale():
    assert convert_price(400, "cents/lb", "USD/lb") == pytest.approx(4)
    assert convert_price(4, "USD/lb", "USD/tonne") == pytest.approx(8818.4903, rel=1e-6)
    assert convert_price(2400, "USD/troy_oz", "USD/kg") == pytest.approx(
        2400 / 0.0311034768
    )
    with pytest.raises(ValueError, match="unsupported commodity price unit"):
        convert_price(400, "looks-like-cents", "USD/lb")


def test_ratio_definitions_match_the_declared_manual_sample():
    rows = {
        "gold.sina_cfd": [_row("gold.sina_cfd", "20260901", 2400, "USD/troy_oz")],
        "brent.sina_cfd": [_row("brent.sina_cfd", "20260901", 80, "USD/barrel")],
        "silver.sina_cfd": [_row("silver.sina_cfd", "20260901", 30, "USD/troy_oz")],
        "copper.sina_cfd": [_row("copper.sina_cfd", "20260901", 4, "USD/lb")],
    }

    assert calculate_ratio(rows["gold.sina_cfd"][0], rows["brent.sina_cfd"][0], "gold_brent")["value"] == 30
    assert calculate_ratio(rows["gold.sina_cfd"][0], rows["silver.sina_cfd"][0], "gold_silver")["value"] == 80
    assert calculate_ratio(rows["gold.sina_cfd"][0], rows["copper.sina_cfd"][0], "gold_copper_quote")["value"] == 600
    assert calculate_ratio(
        rows["gold.sina_cfd"][0],
        {**rows["copper.sina_cfd"][0], "normalized_unit": "USD/kg", "normalized_price": 4 / 0.45359237},
        "gold_copper_same_mass",
    )["value"] == 8750
    assert {item.ratio_id for item in RATIO_DEFINITIONS} == {
        "gold_brent", "gold_silver", "gold_copper_quote", "gold_copper_same_mass"
    }


def test_ratio_series_intersects_dates_and_keeps_missing_legs_null():
    rows = {
        "gold.sina_cfd": [
            _row("gold.sina_cfd", "20260901", 2400, "USD/troy_oz"),
            _row("gold.sina_cfd", "20260902", 2410, "USD/troy_oz"),
        ],
        "silver.sina_cfd": [
            _row("silver.sina_cfd", "20260902", 30, "USD/troy_oz"),
            _row("silver.sina_cfd", "20260903", 31, "USD/troy_oz"),
        ],
    }
    intersection = build_ratio_series(rows, "gold_silver")
    assert [point["date"] for point in intersection] == ["2026-09-02"]
    assert intersection[0]["value"] == pytest.approx(80.33333333)

    union = build_ratio_series(rows, "gold_silver", alignment="union")
    assert [point["reason"] for point in union] == ["missing_leg", None, "missing_leg"]
    assert [point["value"] for point in union] == [None, pytest.approx(80.33333333), None]


def test_normalizer_retains_raw_value_and_explicit_conversion_metadata():
    copper_cents = replace(series_spec("copper.sina_cfd"), raw_unit="cents/lb")
    rows = normalize_commodity_rows(
        [
            {"date": "2026-09-01", "open": 390, "high": 410, "low": 380, "close": 400},
            {"date": "2026-09-01", "open": 400, "high": 420, "low": 390, "close": 410},
            {"date": "2026-09-02", "open": 0, "high": 0, "low": 0, "close": 0},
            {"date": "not-a-date", "close": 400},
        ],
        copper_cents,
    )
    assert len(rows) == 1
    assert rows[0]["raw_close"] == 410
    assert rows[0]["raw_unit"] == "cents/lb"
    assert rows[0]["normalized_price"] == pytest.approx(4.1)
    assert rows[0]["normalized_unit"] == "USD/lb"
    assert rows[0]["conversion_version"] == "commodity-units-v1"
    assert rows[0]["quality_status"] == "warning"
    assert rows[0]["quality_flags"] == ["duplicate_session_date"]


def test_catalog_plans_one_bounded_full_history_fetch_per_series():
    spec = commodity_catalog().get(DATASET)
    pages = tuple(
        spec.request_planner(
            SyncRequest(
                domain=DOMAIN,
                params={"start_date": "20250101", "end_date": "20250131"},
            ),
            None,
            object(),
        )
    )
    assert [page.params["series_id"] for page in pages] == [item.series_id for item in COMMODITY_SERIES]
    assert all(page.params["start_date"] == "20250101" for page in pages)
    assert spec.fetch_page_size is None


class _FakeResponse:
    def __init__(self, text, status_code=200, reason=""):
        self.text = text
        self.status_code = status_code
        self.reason = reason

    def raise_for_status(self):
        if self.status_code >= 400:
            raise CommodityHTTPError(self.status_code, self.reason)


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def test_sina_provider_has_bounded_request_contract_and_normalizes_jsonp(monkeypatch):
    session = _FakeSession([
        _FakeResponse(
            'var _S2026_9_7=[{"date":"2026-09-01","open":"390","high":"410","low":"380","close":"400"}]'
        )
    ])
    monkeypatch.setattr("market_data.commodity_providers._today_token", lambda: "2026_9_7")
    provider = SinaCFDProvider(session=session, retries=0, connect_timeout=2, read_timeout=4)

    rows = provider.fetch_daily(series_id="copper.sina_cfd", start_date="20260901", end_date="20260901")

    assert rows[0]["normalized_price"] == 400
    assert rows[0]["raw_unit"] == "USD/lb"
    assert session.calls[0][1]["timeout"] == (2.0, 4.0)
    assert session.calls[0][1]["params"]["symbol"] == "HG"
    assert session.headers["Referer"].startswith("https://")
    provider.close()
    assert session.closed


def test_sina_provider_retries_transient_http_errors_only(monkeypatch):
    session = _FakeSession([
        _FakeResponse("busy", status_code=503, reason="upstream"),
        _FakeResponse('callback([{ "date": "2026-09-01", "close": "80" }])'),
    ])
    sleeps = []
    monkeypatch.setattr("market_data.commodity_providers.time.sleep", sleeps.append)
    provider = SinaCFDProvider(session=session, retries=1)

    rows = provider.fetch_daily(series_id="brent.sina_cfd")

    assert len(rows) == 1
    assert sleeps == [0.4]
    assert len(session.calls) == 2


def test_sync_engine_writes_each_commodity_page_to_the_independent_store(tmp_path):
    class Client:
        def fetch_daily(self, **params):
            spec = series_spec(params["series_id"])
            return normalize_commodity_rows(
                [
                    {
                        "date": "2026-09-01",
                        "close": 80 if spec.asset == "brent" else 2400,
                        "open": 80 if spec.asset == "brent" else 2400,
                        "high": 80 if spec.asset == "brent" else 2400,
                        "low": 80 if spec.asset == "brent" else 2400,
                    }
                ],
                spec,
            )

    store = DomainStore(Path(tmp_path) / "global.sqlite")
    result = SyncEngine(catalog=commodity_catalog(), store=store, client=Client()).run(
        SyncRequest(
            domain=DOMAIN,
            params={"start_date": "20260901", "end_date": "20260901"},
        ),
        cancel_event=Event(),
    )

    assert result.status == "completed"
    assert result.rows_written == 4
    assert store.count_rows(DATASET) == 4
    assert store.get_sync_state(DATASET)["status"] == "completed"


def test_commodity_service_exposes_catalog_series_and_ratio_views(tmp_path):
    store = DomainStore(Path(tmp_path) / "service.sqlite")
    rows = [
        _row("gold.sina_cfd", "20260901", 2400, "USD/troy_oz"),
        _row("silver.sina_cfd", "20260901", 30, "USD/troy_oz"),
    ]
    store.upsert_rows(
        DATASET,
        rows,
        key_fields=("series_id", "session_date", "price_basis", "definition_version"),
        symbol_field="series_id",
        date_field="session_date",
    )
    service = CommodityService(store)

    catalog = service.catalog()
    series = service.series("gold.sina_cfd", limit=10)
    ratio = service.ratios("gold_silver", limit=10)

    assert catalog["offline_readable"] is True
    assert catalog["items"][1]["row_count"] == 1
    assert series["points"][0]["date"] == "2026-09-01"
    assert ratio["points"][0]["value"] == 80
    assert service.ratios("gold_oil")["ratio"]["ratio_id"] == "gold_brent"


def test_commodity_http_routes_read_the_independent_store(tmp_path, monkeypatch):
    store = DomainStore(Path(tmp_path) / "routes.sqlite")
    store.upsert_rows(
        DATASET,
        [
            _row("gold.sina_cfd", "20260901", 2400, "USD/troy_oz"),
            _row("silver.sina_cfd", "20260901", 30, "USD/troy_oz"),
        ],
        key_fields=("series_id", "session_date", "price_basis", "definition_version"),
        symbol_field="series_id",
        date_field="session_date",
    )
    monkeypatch.setattr(domain_data, "_store", lambda domain: store if domain == DOMAIN else store)
    client = web_server.app.test_client()

    catalog_response = client.get("/api/commodities/catalog")
    series_response = client.get("/api/commodities/series/gold.sina_cfd?limit=10")
    ratio_response = client.get("/api/commodities/ratios/gold_silver?limit=10")
    overview_response = client.get("/api/commodities/overview")

    assert catalog_response.status_code == 200
    assert series_response.get_json()["points"][0]["normalized_price"] == 2400
    assert ratio_response.get_json()["points"][0]["value"] == 80
    assert overview_response.get_json()["cards"][0]["instrument_type"] == "cfd"


def test_commodity_http_routes_reject_unknown_series_and_unbounded_limits():
    client = web_server.app.test_client()

    assert client.get("/api/commodities/series/missing?limit=10").status_code == 400
    assert client.get("/api/commodities/series/gold.sina_cfd?limit=999999").status_code == 400
    assert client.get("/api/commodities/ratios/gold_silver?alignment=nearest").status_code == 400
