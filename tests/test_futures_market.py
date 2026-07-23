from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from market_data.futures import (
    FuturesService,
    futures_catalog,
    normalize_fut_basic,
    normalize_fut_daily,
    normalize_fut_mapping,
)
from market_data.models import SyncRequest
from market_data.store import DomainStore


@pytest.fixture
def service(tmp_path):
    store = DomainStore(tmp_path / "futures.sqlite")
    store.upsert_rows(
        "fut_basic",
        normalize_fut_basic(
            pd.DataFrame(
                [
                    {
                        "ts_code": "IF2607.CFX",
                        "symbol": "IF2607",
                        "exchange": "CFFEX",
                        "name": "沪深300股指2607",
                        "fut_code": "IF",
                        "list_date": "20250721",
                        "delist_date": "20260717",
                    },
                    {
                        "ts_code": "IF2606.CFX",
                        "symbol": "IF2606",
                        "exchange": "CFFEX",
                        "name": "沪深300股指2606",
                        "fut_code": "IF",
                        "list_date": "20250623",
                        "delist_date": "20260619",
                    },
                    {
                        "ts_code": "AU2608.SHF",
                        "symbol": "AU2608",
                        "exchange": "SHFE",
                        "name": "黄金2608",
                        "fut_code": "AU",
                        "list_date": "20250818",
                        "delist_date": "20260817",
                    },
                ]
            )
        ),
        key_fields=("ts_code",),
        symbol_field="symbol",
        date_field="list_date",
    )
    store.upsert_rows(
        "fut_daily",
        normalize_fut_daily(
            pd.DataFrame(
                [
                    {
                        "ts_code": "IF2607.CFX",
                        "trade_date": "20260710",
                        "open": 4010.0,
                        "high": 4050.0,
                        "low": 3990.0,
                        "close": 4040.0,
                        "pre_settle": 4000.0,
                        "settle": 4032.0,
                        "vol": 12345,
                        "amount": 1532000.0,
                        "oi": 88765,
                    }
                ]
            )
        ),
        key_fields=("ts_code", "trade_date"),
        symbol_field="symbol",
        date_field="trade_date",
    )
    store.upsert_rows(
        "fut_mapping",
        normalize_fut_mapping(
            pd.DataFrame(
                [
                    {
                        "ts_code": "IF.CFX",
                        "trade_date": "20260710",
                        "mapping_ts_code": "IF2607.CFX",
                    }
                ]
            )
        ),
        key_fields=("ts_code", "trade_date"),
        symbol_field="continuous_symbol",
        date_field="trade_date",
    )
    return FuturesService(store)


def test_futures_catalog_registers_metadata_daily_and_verified_mapping():
    specs = {spec.dataset_id: spec for spec in futures_catalog()}

    assert set(specs) == {"fut_basic", "fut_daily", "fut_mapping"}
    assert specs["fut_daily"].key_fields == ("ts_code", "trade_date")
    assert specs["fut_mapping"].required is False


def test_futures_metadata_plan_partitions_verified_exchanges(tmp_path):
    spec = futures_catalog().get("fut_basic")
    store = DomainStore(tmp_path / "plan.sqlite")

    pages = tuple(spec.request_planner(SyncRequest(domain="futures"), None, store))

    assert {page.params["exchange"] for page in pages} == {
        "CFFEX", "SHFE", "DCE", "CZCE", "INE", "GFEX"
    }
    assert spec.fetch_page_size


def test_futures_mapping_plan_does_not_request_future_calendar_ranges(tmp_path):
    catalog = futures_catalog()
    store = DomainStore(tmp_path / "mapping-plan.sqlite")
    store.upsert_rows(
        "fut_basic",
        [{"ts_code": "IF2607.CFX", "fut_code": "IF"}],
        key_fields=("ts_code",),
    )

    pages = tuple(
        catalog.get("fut_mapping").request_planner(
            SyncRequest(domain="futures"), None, store
        )
    )

    assert pages
    assert all(page.params["end_date"] == date.today().strftime("%Y%m%d") for page in pages)


def test_futures_daily_plan_uses_contract_lifetime_and_completed_cursor(tmp_path):
    catalog = futures_catalog()
    store = DomainStore(tmp_path / "daily-plan.sqlite")
    store.upsert_rows(
        "fut_basic",
        [
            {
                "ts_code": "IF2607.CFX",
                "list_date": "20250721",
                "delist_date": "20260717",
            },
            {
                "ts_code": "IF1001.CFX",
                "list_date": "20090101",
                "delist_date": "20100115",
            },
        ],
        key_fields=("ts_code",),
    )

    pages = tuple(
        catalog.get("fut_daily").request_planner(
            SyncRequest(domain="futures"),
            {"status": "failed", "cursor": "20260101"},
            store,
        )
    )

    assert [page.params["ts_code"] for page in pages] == ["IF2607.CFX"]
    assert pages[0].params == {
        "ts_code": "IF2607.CFX",
        "start_date": "20260101",
        "end_date": min("20260717", date.today().strftime("%Y%m%d")),
    }


def test_futures_normalization_keeps_domain_specific_contract_fields():
    row = normalize_fut_basic(
        pd.DataFrame(
            [
                {
                    "ts_code": "IF2607.CFX",
                    "exchange": "CFFEX",
                    "fut_code": "if",
                    "list_date": "20250721",
                    "delist_date": "20260717",
                }
            ]
        )
    )[0]

    assert row["symbol"] == "IF2607.CFX"
    assert row["exchange"] == "CFFEX"
    assert row["product"] == "IF"
    assert row["contract_month"] == "202607"
    assert row["active_from"] == "20250721"
    assert row["active_to"] == "20260717"


def test_active_contracts_exclude_expired_and_page_after_filtering(service):
    contracts = service.contracts(
        active_on="20260713", exchange="CFFEX", product="if", limit=1, offset=0
    )

    assert contracts["total"] == 1
    assert [row["symbol"] for row in contracts["items"]] == ["IF2607.CFX"]
    assert all(row["active_to"] >= "20260713" for row in contracts["items"])


def test_contract_search_reads_beyond_first_sqlite_page(tmp_path):
    store = DomainStore(tmp_path / "many-contracts.sqlite")
    rows = [
        {
            "ts_code": f"T{index:04d}.DCE",
            "name": f"测试合约 {index}",
            "fut_code": "T",
            "exchange": "DCE",
            "list_date": "20260101",
            "delist_date": "20261231",
        }
        for index in range(2_005)
    ]
    store.upsert_rows("fut_basic", rows, key_fields=("ts_code",))

    payload = FuturesService(store).contracts(query="测试合约 2004", limit=10)

    assert payload["total"] == 1
    assert payload["items"][0]["symbol"] == "T2004.DCE"


def test_continuous_symbols_returns_latest_verified_contract_mapping(service):
    payload = service.continuous_symbols(exchange="CFFEX", active_on="20260710")

    assert payload["items"] == [
        {
            "continuous_symbol": "IF.CFX",
            "trade_date": "20260710",
            "contract_symbol": "IF2607.CFX",
            "exchange": "CFFEX",
            "product": "IF",
        }
    ]


def test_futures_kline_includes_settlement_and_open_interest(service):
    payload = service.kline("IF2607.CFX", limit=260)

    assert payload["symbol"] == "IF2607.CFX"
    assert {"pre_settle", "settle", "oi"}.issubset(payload["candles"][0])
    assert payload["candles"][0]["volume"] == 12345


@pytest.mark.parametrize("limit", [0, 2001])
def test_futures_service_rejects_unbounded_limits(service, limit):
    with pytest.raises(ValueError, match="limit"):
        service.kline("IF2607.CFX", limit=limit)
