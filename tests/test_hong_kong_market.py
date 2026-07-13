from __future__ import annotations

import pandas as pd
import pytest

from market_data.hong_kong import hong_kong_catalog, normalize_hk_daily
from market_data.models import SyncRequest
from market_data.services import HongKongService
from market_data.store import DomainStore


@pytest.fixture
def store(tmp_path):
    return DomainStore(tmp_path / "hong_kong.sqlite")


@pytest.fixture
def service(store):
    store.upsert_rows(
        "hk_basic",
        [
            {
                "ts_code": "00700.HK",
                "name": "腾讯控股",
                "enname": "Tencent Holdings",
                "cn_spell": "TXKG",
                "market": "主板",
                "list_status": "L",
            },
            {
                "ts_code": "01211.HK",
                "name": "比亚迪股份",
                "enname": "BYD Company",
                "cn_spell": "BYDGF",
                "market": "主板",
                "list_status": "L",
            },
            {
                "ts_code": "09988.HK",
                "name": "阿里巴巴-W",
                "enname": "Alibaba",
                "cn_spell": "ALBBW",
                "market": "主板",
                "list_status": "L",
            },
        ],
        key_fields=("ts_code",),
        symbol_field="ts_code",
    )
    store.upsert_rows(
        "hk_daily",
        [
            {
                "ts_code": "00700.HK",
                "trade_date": "20260708",
                "open": 400.0,
                "high": 412.0,
                "low": 398.0,
                "close": 410.0,
                "vol": 1000,
                "amount": 410000,
            },
            {
                "ts_code": "00700.HK",
                "trade_date": "20260709",
                "open": 410.0,
                "high": 421.0,
                "low": 408.0,
                "close": 420.0,
                "vol": 1200,
                "amount": 504000,
            },
            {
                "ts_code": "00700.HK",
                "trade_date": "20260710",
                "open": 419.0,
                "high": 425.0,
                "low": 416.0,
                "close": 418.0,
                "vol": 1100,
                "amount": 459800,
            },
            {
                "ts_code": "01211.HK",
                "trade_date": "20260710",
                "pre_close": 112.0,
                "close": 115.0,
            },
        ],
        key_fields=("ts_code", "trade_date"),
        symbol_field="ts_code",
        date_field="trade_date",
    )
    return HongKongService(store)


def test_hong_kong_catalog_registers_required_and_optional_datasets():
    catalog = hong_kong_catalog()

    assert catalog.get("hk_basic").required is True
    assert catalog.get("hk_daily").required is True
    assert catalog.get("hk_tradecal").required is False
    assert catalog.get("hk_adjfactor").method == "hk_adjfactor"
    assert catalog.get("hk_adjfactor").required is False
    assert {
        "hk_income",
        "hk_balancesheet",
        "hk_cashflow",
        "hk_fina_indicator",
    }.issubset({spec.dataset_id for spec in catalog.for_domain("hong_kong")})


def test_hong_kong_full_price_plan_is_symbol_and_date_explicit(store):
    store.upsert_rows(
        "hk_basic",
        [{"ts_code": "00700.HK", "list_status": "L"}],
        key_fields=("ts_code",),
        symbol_field="ts_code",
    )
    spec = hong_kong_catalog().get("hk_daily")

    pages = tuple(spec.request_planner(SyncRequest(domain="hong_kong"), None, store))

    assert pages
    assert spec.fetch_page_size
    assert {page.params["ts_code"] for page in pages} == {"00700.HK"}
    assert all(page.params.get("start_date") and page.params.get("end_date") for page in pages)
    assert all(page.params for page in pages)


def test_hong_kong_daily_normalizes_symbol_date_and_currency():
    rows = normalize_hk_daily(
        pd.DataFrame(
            [{"ts_code": "700.hk", "trade_date": "20260710", "close": 420.0}]
        )
    )

    assert rows == [
        {
            "ts_code": "00700.HK",
            "trade_date": "20260710",
            "close": 420.0,
            "symbol": "00700.HK",
            "date": "2026-07-10",
            "currency": "HKD",
        }
    ]


def test_hong_kong_search_matches_symbol_names_and_pages(service):
    first = service.search("股", limit=1, offset=0)
    second = service.search("股", limit=1, offset=1)
    by_symbol = service.search("9988", limit=20, offset=0)
    by_english = service.search("tencent", limit=20, offset=0)

    assert first["total"] == 2
    assert first["items"][0]["symbol"] == "00700.HK"
    assert second["items"][0]["symbol"] == "01211.HK"
    assert by_symbol["items"][0]["symbol"] == "09988.HK"
    assert by_english["items"][0]["name"] == "腾讯控股"
    assert all(item["currency"] == "HKD" for item in first["items"])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 0, "offset": 0}, "limit"),
        ({"limit": 201, "offset": 0}, "limit"),
        ({"limit": 20, "offset": -1}, "offset"),
    ],
)
def test_hong_kong_search_rejects_unbounded_pages(service, kwargs, message):
    with pytest.raises(ValueError, match=message):
        service.search("", **kwargs)


def test_hong_kong_kline_is_oldest_first_and_derives_change_pct(service):
    payload = service.kline("700", limit=2, adjustment="raw")

    assert payload["symbol"] == "00700.HK"
    assert payload["currency"] == "HKD"
    assert payload["adjustment"] == "raw"
    assert [row["date"] for row in payload["items"]] == [
        "2026-07-09",
        "2026-07-10",
    ]
    assert payload["items"][0]["change_pct"] == pytest.approx(2.439024, rel=1e-6)
    assert payload["items"][1]["change_pct"] == pytest.approx(-2 / 420 * 100)


def test_hong_kong_adjustment_falls_back_to_raw_when_factor_coverage_is_partial(
    service, store
):
    store.upsert_rows(
        "hk_adjfactor",
        [
            {
                "ts_code": "00700.HK",
                "trade_date": "20260710",
                "cum_adjfactor": 1.5,
            }
        ],
        key_fields=("ts_code", "trade_date"),
        symbol_field="ts_code",
        date_field="trade_date",
    )

    payload = service.kline("00700.HK", limit=2, adjustment="qfq")

    assert payload["requested_adjustment"] == "qfq"
    assert payload["adjustment"] == "raw"
    assert payload["adjustment_fallback"] is True
    assert [row["close"] for row in payload["items"]] == [420.0, 418.0]


def test_hong_kong_adjustment_applies_only_with_complete_factor_coverage(service, store):
    store.upsert_rows(
        "hk_adjfactor",
        [
            {
                "ts_code": "00700.HK",
                "trade_date": "20260709",
                "cum_adjfactor": 1.0,
            },
            {
                "ts_code": "00700.HK",
                "trade_date": "20260710",
                "cum_adjfactor": 2.0,
            },
        ],
        key_fields=("ts_code", "trade_date"),
        symbol_field="ts_code",
        date_field="trade_date",
    )

    payload = service.kline("00700.HK", limit=2, adjustment="qfq")

    assert payload["adjustment"] == "qfq"
    assert payload["adjustment_fallback"] is False
    assert [row["close"] for row in payload["items"]] == [210.0, 418.0]


def test_hong_kong_finance_groups_optional_datasets(service, store):
    store.upsert_rows(
        "hk_income",
        [
            {
                "ts_code": "00700.HK",
                "end_date": "20241231",
                "ind_name": "营业额",
                "ind_value": 652_498_000_000,
            }
        ],
        key_fields=("ts_code", "end_date", "ind_name"),
        symbol_field="ts_code",
        date_field="end_date",
    )
    store.upsert_rows(
        "hk_fina_indicator",
        [
            {
                "ts_code": "00700.HK",
                "end_date": "20241231",
                "report_type": "Q4",
                "roe_avg": 20.5,
            }
        ],
        key_fields=("ts_code", "end_date", "report_type"),
        symbol_field="ts_code",
        date_field="end_date",
    )

    payload = service.finance("700")

    assert payload["symbol"] == "00700.HK"
    assert payload["currency"] == "HKD"
    assert payload["income"][0]["period"] == "2024-12-31"
    assert payload["indicators"][0]["roe_avg"] == 20.5
    assert payload["balancesheet"] == []
    assert payload["cashflow"] == []


def test_hong_kong_overview_does_not_claim_market_as_industry(service):
    payload = service.overview()

    assert payload["grouping_mode"] == "market_segment"
    assert payload["industry_coverage"] == 0
    assert payload["industry_mapped"] == 0
    assert payload["market_segments"] == [{"name": "主板", "count": 3}]
    assert payload["performance"]["advancers"] == 1
    assert payload["performance"]["decliners"] == 1


def test_hong_kong_overview_and_heatmap_do_not_page_full_daily_history(
    service, store, monkeypatch
):
    original_query_rows = store.query_rows
    latest_calls = []
    original_query_latest_rows = store.query_latest_rows

    def reject_daily_history(dataset, **kwargs):
        if dataset == "hk_daily":
            raise AssertionError("hk_daily history must stay inside SQLite")
        return original_query_rows(dataset, **kwargs)

    def record_latest(dataset, **kwargs):
        latest_calls.append((dataset, kwargs))
        return original_query_latest_rows(dataset, **kwargs)

    monkeypatch.setattr(store, "query_rows", reject_daily_history)
    monkeypatch.setattr(store, "query_latest_rows", record_latest)

    overview = service.overview()
    heatmap = service.heatmap()

    assert overview["performance"] == {
        "advancers": 1,
        "decliners": 1,
        "unchanged": 0,
    }
    assert heatmap["total"] == 2
    assert any(kwargs["rows_per_symbol"] == 2 for _, kwargs in latest_calls)
    assert any(kwargs["rows_per_symbol"] == 1 for _, kwargs in latest_calls)
    assert all(dataset == "hk_daily" for dataset, _ in latest_calls)


def test_hong_kong_overview_uses_performance_distribution_without_segments(tmp_path):
    store = DomainStore(tmp_path / "no-segment.sqlite")
    store.upsert_rows(
        "hk_basic",
        [{"ts_code": "00700.HK", "name": "腾讯控股", "market": ""}],
        key_fields=("ts_code",),
        symbol_field="ts_code",
    )

    payload = HongKongService(store).overview()

    assert payload["grouping_mode"] == "performance_distribution"
    assert payload["industry_coverage"] == 0
