from datetime import date

import pandas as pd

from utils.tushare_ext_store import TushareExtStore
from utils.tushare_ext_views import (
    build_adjusted_candles,
    build_index_kline_payload,
    build_market_trading_summary,
    build_stock_extension_payload,
    calculate_macd,
    calculate_moving_average,
)


def test_calculate_moving_average_and_macd_keep_input_length():
    values = [float(item) for item in range(1, 61)]

    ma5 = calculate_moving_average(values, 5)
    macd = calculate_macd(values, fast=12, slow=26, signal=9)

    assert ma5[:4] == [None, None, None, None]
    assert ma5[4] == 3.0
    assert len(macd["dif"]) == len(values)
    assert len(macd["dea"]) == len(values)
    assert len(macd["macd"]) == len(values)
    assert macd["dif"][-1] > 0


def test_index_payload_clamps_month_range_and_adds_ma_lines(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    rows = []
    for offset, trade_date in enumerate(pd.bdate_range("2025-08-01", "2026-06-30")):
        rows.append(
            {
                "ts_code": "000001.SH",
                "trade_date": trade_date.strftime("%Y%m%d"),
                "open": 3000 + offset,
                "high": 3010 + offset,
                "low": 2990 + offset,
                "close": 3005 + offset,
                "vol": 1000 + offset,
                "amount": 2000 + offset,
            }
        )
    store.upsert_rows("index_daily", rows, key_fields=("ts_code", "trade_date"))

    payload = build_index_kline_payload(store, "sh000001", months=10, today=date(2026, 7, 1))

    assert payload["months"] == 6
    assert payload["source"] == "tushare:index_daily"
    assert payload["candles"][0]["date"] >= "2026-01-01"
    assert payload["candles"][-1]["MA50"] is not None
    assert payload["candles"][-1]["MA200"] is not None


def test_index_payload_accepts_detail_limit_over_month_window(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    rows = []
    for offset, trade_date in enumerate(pd.bdate_range("2024-01-01", "2026-06-30")):
        rows.append(
            {
                "ts_code": "000001.SH",
                "trade_date": trade_date.strftime("%Y%m%d"),
                "open": 3000 + offset,
                "high": 3010 + offset,
                "low": 2990 + offset,
                "close": 3005 + offset,
                "vol": 1000 + offset,
                "amount": 2000 + offset,
            }
        )
    store.upsert_rows("index_daily", rows, key_fields=("ts_code", "trade_date"))

    payload = build_index_kline_payload(store, "sh000001", months=3, today=date(2026, 7, 1), limit=520)

    assert payload["limit"] == 520
    assert len(payload["candles"]) == 520
    assert payload["candles"][0]["date"] < "2026-01-01"


def test_market_trading_summary_returns_previous_day_deltas(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    store.upsert_rows(
        "daily",
        [
            {"trade_date": "20260630", "ts_code": "000001.SZ", "amount": 1_000_000},
            {"trade_date": "20260629", "ts_code": "000001.SZ", "amount": 600_000},
        ],
        key_fields=("trade_date", "ts_code"),
    )
    store.upsert_rows(
        "top_list",
        [
            {"trade_date": "20260630", "ts_code": "000001.SZ", "net_amount": 300_000_000},
            {"trade_date": "20260629", "ts_code": "000001.SZ", "net_amount": 100_000_000},
        ],
        key_fields=("trade_date", "ts_code"),
    )
    store.upsert_rows(
        "block_trade",
        [
            {"trade_date": "20260630", "ts_code": "000001.SZ", "amount": 1000},
            {"trade_date": "20260629", "ts_code": "000001.SZ", "amount": 600},
        ],
        key_fields=("trade_date", "ts_code"),
    )
    store.upsert_rows(
        "moneyflow",
        [
            {"trade_date": "20260630", "ts_code": "000001.SZ", "net_mf_amount": 30_000},
            {"trade_date": "20260629", "ts_code": "000001.SZ", "net_mf_amount": 10_000},
        ],
        key_fields=("trade_date", "ts_code"),
    )
    store.upsert_rows(
        "margin",
        [
            {"trade_date": "20260630", "exchange_id": "SSE", "rzrqye": 50_000_000_000},
            {"trade_date": "20260629", "exchange_id": "SSE", "rzrqye": 45_000_000_000},
        ],
        key_fields=("trade_date", "exchange_id"),
    )
    store.upsert_rows(
        "moneyflow_hsgt",
        [
            {"trade_date": "20260630", "north_money": 12.5},
            {"trade_date": "20260629", "north_money": -2.5},
        ],
        key_fields=("trade_date",),
    )

    summary = build_market_trading_summary(store, "2026-06-30")

    assert summary["metrics"]["market_amount"]["value"] == 10.0
    assert summary["metrics"]["market_amount"]["delta"] == 4.0
    assert summary["metrics"]["main_money_flow"]["value"] == 3.0
    assert summary["metrics"]["dragon_tiger_net"]["value"] == 3.0
    assert summary["metrics"]["dragon_tiger_net"]["delta"] == 2.0
    assert summary["metrics"]["block_trade_amount"]["delta"] == 0.04
    assert summary["metrics"]["margin_balance"]["value"] == 500.0
    assert summary["metrics"]["northbound_money"]["delta"] == 15.0
    assert summary["metrics"]["market_amount"]["unit"] == "亿元"


def test_market_trading_summary_keeps_missing_money_data_empty(tmp_path):
    store = TushareExtStore(tmp_path / "extended")

    summary = build_market_trading_summary(store, "2026-06-30")

    assert summary["metrics"]["market_amount"]["value"] is None
    assert summary["metrics"]["market_amount"]["delta"] is None
    assert summary["metrics"]["margin_balance"]["value"] is None
    assert summary["metrics"]["northbound_money"]["value"] is None


def test_stock_extension_payload_combines_meta_valuation_and_financials(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    store.upsert_rows(
        "stock_basic",
        [{"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "industry": "银行", "area": "深圳"}],
        key_fields=("ts_code",),
    )
    store.upsert_rows(
        "daily_basic",
        [{
            "ts_code": "000001.SZ",
            "trade_date": "20260630",
            "pe_ttm": 5.2,
            "pb": 0.6,
            "total_mv": 1200000,
            "circ_mv": 1000000,
            "turnover_rate": 1.2,
        }],
        key_fields=("ts_code", "trade_date"),
    )
    store.upsert_rows(
        "fina_indicator",
        [{
            "ts_code": "000001.SZ",
            "end_date": "20260331",
            "roe": 12.3,
            "roa": 1.1,
            "grossprofit_margin": 20.0,
            "netprofit_margin": 15.0,
            "eps": 0.8,
            "or_yoy": 6.1,
            "netprofit_yoy": 7.2,
            "dt_netprofit_yoy": 5.4,
        }],
        key_fields=("ts_code", "end_date"),
        trade_date_field="end_date",
    )

    payload = build_stock_extension_payload(store, "000001")

    assert payload["meta"]["industry"] == "银行"
    assert payload["valuation"]["pe_ttm"] == 5.2
    assert payload["financial"]["roe"] == 12.3
    assert payload["sync_warnings"] == []


def test_build_adjusted_candles_uses_adj_factor_for_qfq_prices(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    store.upsert_rows(
        "daily",
        [
            {"ts_code": "000001.SZ", "trade_date": "20260630", "open": 20, "high": 22, "low": 19, "close": 21, "vol": 1000, "amount": 2000},
            {"ts_code": "000001.SZ", "trade_date": "20260629", "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 900, "amount": 1800},
        ],
        key_fields=("ts_code", "trade_date"),
    )
    store.upsert_rows(
        "adj_factor",
        [
            {"ts_code": "000001.SZ", "trade_date": "20260630", "adj_factor": 2.0},
            {"ts_code": "000001.SZ", "trade_date": "20260629", "adj_factor": 1.0},
        ],
        key_fields=("ts_code", "trade_date"),
    )

    candles = build_adjusted_candles(store, "000001")

    assert candles[0]["date"] == "2026-06-30"
    assert candles[0]["close"] == 21.0
    assert candles[1]["close"] == 5.25


def test_build_adjusted_candles_requires_full_visible_coverage(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    store.upsert_rows(
        "daily",
        [
            {"ts_code": "000001.SZ", "trade_date": "20260630", "open": 20, "high": 22, "low": 19, "close": 21},
            {"ts_code": "000001.SZ", "trade_date": "20260629", "open": 10, "high": 11, "low": 9, "close": 10.5},
        ],
        key_fields=("ts_code", "trade_date"),
    )
    store.upsert_rows(
        "adj_factor",
        [
            {"ts_code": "000001.SZ", "trade_date": "20260630", "adj_factor": 2.0},
            {"ts_code": "000001.SZ", "trade_date": "20260629", "adj_factor": 1.0},
        ],
        key_fields=("ts_code", "trade_date"),
    )

    candles = build_adjusted_candles(
        store,
        "000001",
        required_trade_dates=["20260630", "20260629", "20260626"],
    )

    assert candles == []
