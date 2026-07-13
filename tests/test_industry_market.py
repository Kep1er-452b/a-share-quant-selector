from __future__ import annotations

import pytest

from market_data.industry import IndustryService, industry_catalog
from market_data.models import SyncRequest
from market_data.store import DomainStore


@pytest.fixture
def service(tmp_path):
    store = DomainStore(tmp_path / "industry.sqlite")
    store.upsert_rows(
        "index_classify",
        [
            {
                "index_code": "801880.SI",
                "industry_name": "汽车",
                "level": "L1",
                "src": "SW2021",
            },
            {
                "index_code": "801010.SI",
                "industry_name": "农林牧渔",
                "level": "L1",
                "src": "SW2021",
            },
        ],
        key_fields=("index_code",),
        symbol_field="index_code",
    )
    store.upsert_rows(
        "index_member_all",
        [
            {
                "l1_code": "801880.SI",
                "l1_name": "汽车",
                "ts_code": "002594.SZ",
                "name": "比亚迪",
                "in_date": "20170101",
                "out_date": None,
                "is_new": "Y",
            },
            {
                "l1_code": "",
                "ts_code": "600000.SH",
                "name": "待分类样本",
                "in_date": "20260101",
                "is_new": "Y",
            },
        ],
        key_fields=("ts_code", "in_date"),
        symbol_field="ts_code",
    )
    store.upsert_rows(
        "sw_daily",
        [
            {
                "ts_code": "801880.SI",
                "trade_date": "20260710",
                "open": 5200.0,
                "high": 5260.0,
                "low": 5180.0,
                "close": 5240.0,
                "pct_change": 1.1,
            }
        ],
        key_fields=("ts_code", "trade_date"),
        symbol_field="ts_code",
        date_field="trade_date",
    )
    store.upsert_rows(
        "cn_pmi",
        [
            {"month": "202606", "pmi010000": 50.4},
            {"month": "202607", "pmi010000": 50.7},
        ],
        key_fields=("month",),
        date_field="month",
    )
    store.upsert_rows(
        "cn_ppi",
        [
            {"month": "202606", "ppi_yoy": -1.2},
            {"month": "202607", "ppi_yoy": -0.9},
        ],
        key_fields=("month",),
        date_field="month",
    )
    return IndustryService(store)


def test_industry_catalog_registers_classification_index_and_cycle_datasets():
    specs = {spec.dataset_id: spec for spec in industry_catalog()}

    assert set(specs) == {
        "index_classify",
        "index_member_all",
        "sw_daily",
        "cn_pmi",
        "cn_ppi",
    }
    assert specs["index_member_all"].method == "index_member_all"


def test_industry_plans_partition_hierarchy_and_index_symbols(tmp_path):
    store = DomainStore(tmp_path / "plan.sqlite")
    store.upsert_rows(
        "index_classify",
        [{"index_code": "801880.SI", "level": "L1", "src": "SW2021"}],
        key_fields=("index_code",),
        symbol_field="index_code",
    )
    catalog = industry_catalog()
    request = SyncRequest(domain="industry")

    classifications = tuple(catalog.get("index_classify").request_planner(request, None, store))
    members = tuple(catalog.get("index_member_all").request_planner(request, None, store))
    daily = tuple(catalog.get("sw_daily").request_planner(request, None, store))

    assert {page.params["level"] for page in classifications} == {"L1", "L2", "L3"}
    assert all(page.params["src"] == "SW2021" for page in classifications)
    assert members[0].params["l1_code"] == "801880.SI"
    assert daily and all(page.params["ts_code"] == "801880.SI" for page in daily)
    assert all(page.params.get("start_date") and page.params.get("end_date") for page in daily)


def test_classifications_filters_level_and_reports_member_coverage(service):
    payload = service.classifications("L1")

    assert [item["industry_id"] for item in payload["items"]] == [
        "801010.SI",
        "801880.SI",
    ]
    assert payload["coverage"] == {"mapped": 1, "total": 2, "ratio": 0.5}
    assert payload["unclassified"][0]["symbol"] == "600000.SH"


def test_industry_detail_returns_instrument_links_and_index_kline(service):
    payload = service.detail("801880.SI")

    assert payload["members"][0]["route"] == (
        "#/equities/a_share/instrument/002594.SZ"
    )
    assert payload["members"][0]["name"] == "比亚迪"
    assert payload["index_candles"][0]["close"] == 5240.0
    assert payload["coverage"]["ratio"] == 0.5
    assert payload["unclassified"]


def test_industry_cycle_keeps_different_units_on_separate_axes(service):
    payload = service.cycle_series(
        ["cn_pmi.headline", "cn_ppi.ppi_yoy"]
    )

    assert payload["axis_mode"] == "separate"
    assert [series["unit"] for series in payload["series"]] == ["index", "%"]
    assert payload["series"][0]["points"][-1] == ["202607", 50.7]


def test_industry_cycle_can_normalize_different_units(service):
    payload = service.cycle_series(
        ["cn_pmi.headline", "cn_ppi.ppi_yoy"], normalize=True
    )

    assert payload["axis_mode"] == "normalized"
    assert all(series["unit"] == "z-score" for series in payload["series"])


def test_industry_service_rejects_unknown_series(service):
    with pytest.raises(ValueError, match="unknown cycle series"):
        service.cycle_series(["missing.series"])
