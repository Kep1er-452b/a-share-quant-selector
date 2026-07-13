from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data.equity_policy import (
    AShareEquityReader,
    HongKongEquityReader,
    equity_policy,
)


class FakeCsvManager:
    def list_all_stocks(self):
        return ["000001", "600000"]

    def read_stock_for_analysis(self, symbol):
        return pd.DataFrame([{"symbol": symbol, "close": 10.0}])


class FakeHongKongService:
    def search(self, query, limit=20, offset=0):
        items = [{"symbol": "00005.HK", "name": "汇丰控股"}]
        return {"items": items, "total": 1, "limit": limit, "offset": offset}

    def kline(self, symbol, limit=260, adjustment="raw"):
        return {
            "symbol": symbol,
            "currency": "HKD",
            "items": [{"date": "2026-07-10", "close": 100.0}],
        }


def test_hong_kong_policy_does_not_apply_a_share_instrument_rules():
    policy = equity_policy("hong_kong")

    assert policy.is_instrument_allowed({"symbol": "00005.HK", "name": "ST 汇丰控股"})
    assert policy.display_rules.price_limit_model is None
    assert policy.display_rules.st_filter is False
    assert policy.display_rules.board_model is None
    assert policy.display_rules.listing_limit_model is None


def test_a_share_policy_retains_existing_instrument_rules():
    policy = equity_policy("a_share")

    assert not policy.is_instrument_allowed({"symbol": "600000.SH", "name": "ST浦发"})
    assert policy.display_rules.price_limit_model == "a_share_board_rules"
    assert policy.display_rules.st_filter is True
    assert policy.display_rules.board_model == "a_share_boards"
    assert policy.display_rules.listing_limit_model == "a_share_ipo_window"


def test_strategy_scope_keeps_a_share_formulas_out_of_hong_kong():
    hong_kong = equity_policy("hong_kong")
    a_share = equity_policy("a_share")

    assert hong_kong.strategy_scope("B1 V2.42.61") == "a_share_only"
    assert not hong_kong.is_strategy_allowed("B1 V2.42.61")
    assert a_share.is_strategy_allowed("B1 V2.42.61")
    assert hong_kong.is_strategy_allowed("shared_trend", declared_scope="market_neutral")


def test_equity_policy_rejects_unknown_market():
    with pytest.raises(KeyError, match="unknown equity market"):
        equity_policy("us")


def test_a_share_reader_adapts_existing_csv_manager_without_changing_storage():
    reader = AShareEquityReader(
        FakeCsvManager(),
        metadata={"000001": {"name": "平安银行"}},
    )

    page = reader.list_instruments(query="平安", limit=20, offset=0)
    frame = reader.read_analysis_frame("000001.SZ")

    assert page["items"] == [
        {"symbol": "000001.SZ", "code": "000001", "name": "平安银行"}
    ]
    assert frame.iloc[0]["symbol"] == "000001"
    assert reader.instrument_metadata("000001.SZ")["currency"] == "CNY"


def test_hong_kong_reader_uses_hong_kong_service_and_hkd_metadata():
    reader = HongKongEquityReader(FakeHongKongService())

    page = reader.list_instruments(query="汇丰", limit=20, offset=0)
    frame = reader.read_analysis_frame("00005.HK")
    metadata = reader.instrument_metadata("00005.HK")

    assert page["items"][0]["symbol"] == "00005.HK"
    assert frame.iloc[0]["close"] == 100.0
    assert metadata["currency"] == "HKD"

