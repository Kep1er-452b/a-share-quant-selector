from datetime import date

import pandas as pd
import pytest

from utils.tushare_ext_store import TushareExtStore
from utils.tushare_ext_sync import TushareExtSync


class FakePro:
    def __init__(self):
        self.calls = []

    def index_daily(self, **kwargs):
        self.calls.append(("index_daily", kwargs))
        return pd.DataFrame(
            [
                {
                    "ts_code": kwargs["ts_code"],
                    "trade_date": "20260630",
                    "open": 3000,
                    "high": 3020,
                    "low": 2990,
                    "close": 3010,
                    "vol": 1200,
                    "amount": 3400,
                }
            ]
        )

    def stock_basic(self, **kwargs):
        self.calls.append(("stock_basic", kwargs))
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "symbol": "000001",
                    "name": "平安银行",
                    "industry": "银行",
                    "market": "主板",
                    "exchange": "SZSE",
                    "list_date": "19910403",
                }
            ]
        )

    def namechange(self, **kwargs):
        self.calls.append(("namechange", kwargs))
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "start_date": "19910403",
                    "end_date": None,
                    "change_reason": "上市",
                }
            ]
        )

    def hs_const(self, **kwargs):
        self.calls.append(("hs_const", kwargs))
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "hs_type": kwargs["hs_type"],
                    "in_date": "20200101",
                    "out_date": None,
                    "is_new": "1",
                }
            ]
        )

    def trade_cal(self, **kwargs):
        self.calls.append(("trade_cal", kwargs))
        return pd.DataFrame(
            [
                {"exchange": "SSE", "cal_date": "20260630", "is_open": 1, "pretrade_date": "20260629"},
                {"exchange": "SSE", "cal_date": "20260701", "is_open": 1, "pretrade_date": "20260630"},
            ]
        )

    def daily_basic(self, **kwargs):
        self.calls.append(("daily_basic", kwargs))
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": kwargs.get("trade_date", "20260630"),
                    "pe_ttm": 5.0,
                    "pb": 0.6,
                    "total_mv": 1200000,
                    "circ_mv": 1000000,
                }
            ]
        )

    def daily(self, **kwargs):
        self.calls.append(("daily", kwargs))
        return pd.DataFrame([{"ts_code": kwargs["ts_code"], "trade_date": "20260630", "close": 10.5}])

    def weekly(self, **kwargs):
        self.calls.append(("weekly", kwargs))
        return pd.DataFrame([{"ts_code": kwargs["ts_code"], "trade_date": "20260626", "close": 10.2}])

    def monthly(self, **kwargs):
        self.calls.append(("monthly", kwargs))
        return pd.DataFrame([{"ts_code": kwargs["ts_code"], "trade_date": "20260630", "close": 10.1}])

    def adj_factor(self, **kwargs):
        self.calls.append(("adj_factor", kwargs))
        return pd.DataFrame([{"ts_code": kwargs["ts_code"], "trade_date": "20260630", "adj_factor": 2.0}])

    def fina_indicator(self, **kwargs):
        self.calls.append(("fina_indicator", kwargs))
        raise RuntimeError("没有访问该接口的权限")

    def top_list(self, **kwargs):
        self.calls.append(("top_list", kwargs))
        return pd.DataFrame([{"trade_date": kwargs["trade_date"], "ts_code": "000001.SZ", "net_amount": 1000}])

    def top_inst(self, **kwargs):
        self.calls.append(("top_inst", kwargs))
        raise RuntimeError("没有访问该接口的权限")


def test_sync_index_cache_fetches_from_history_when_empty(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    pro = FakePro()
    sync = TushareExtSync(store, pro)

    result = sync.ensure_index_cache(symbols=["000001.SH"], today=date(2026, 7, 1))

    assert result["fetched_rows"] == 1
    assert pro.calls[0][0] == "index_daily"
    assert pro.calls[0][1]["start_date"] == "20200622"
    assert store.latest_trade_date("index_daily", ts_code="000001.SH") == "20260630"


def test_sync_index_cache_fetches_incrementally_after_latest_local_date(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    store.upsert_rows(
        "index_daily",
        [{"ts_code": "000001.SH", "trade_date": "20260629", "close": 3000}],
        key_fields=("ts_code", "trade_date"),
    )
    pro = FakePro()
    sync = TushareExtSync(store, pro)

    sync.ensure_index_cache(symbols=["000001.SH"], today=date(2026, 7, 1))

    assert pro.calls[0][1]["start_date"] == "20200622"


def test_sync_index_cache_repairs_history_that_does_not_reach_target_start(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    rows = [
        {
            "ts_code": "000001.SH",
            "trade_date": trade_date.strftime("%Y%m%d"),
            "close": 2800 + offset,
        }
        for offset, trade_date in enumerate(pd.bdate_range("2021-06-01", periods=1300))
    ]
    store.upsert_rows(
        "index_daily",
        rows,
        key_fields=("ts_code", "trade_date"),
    )
    pro = FakePro()
    sync = TushareExtSync(store, pro)

    sync.ensure_index_cache(symbols=["000001.SH"], today=date(2026, 7, 1))

    expected = (pd.Timestamp("2026-07-01") - pd.Timedelta(days=2200)).strftime("%Y%m%d")
    assert pro.calls[0][1]["start_date"] == expected


def test_sync_trading_snapshot_records_permission_warnings(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    pro = FakePro()
    sync = TushareExtSync(store, pro)

    result = sync.sync_trading_snapshot(["20260630"], datasets=["top_list", "top_inst"])

    assert result["datasets"]["top_list"]["rows"] == 1
    assert result["datasets"]["top_inst"]["status"] == "warning"
    assert store.list_warnings()[0]["dataset"] == "top_inst"


def test_sync_basics_writes_stock_metadata_and_calendar(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    pro = FakePro()
    sync = TushareExtSync(store, pro)

    result = sync.sync_basics(today=date(2026, 7, 1))

    assert result["datasets"]["stock_basic"]["rows"] == 1
    assert result["datasets"]["trade_cal"]["rows"] == 2
    meta = store.query_rows("stock_basic", ts_code="000001.SZ")[0]
    assert meta["name"] == "平安银行"
    assert store.latest_trade_date("trade_cal") == "20260701"


def test_sync_daily_basic_and_finance_permission_warning(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    pro = FakePro()
    sync = TushareExtSync(store, pro)

    valuation = sync.sync_valuation_snapshot(["20260630"])
    finance = sync.sync_financials_for_universe(
        [{"code": "000001", "ts_code": "000001.SZ"}],
        start_date="20240101",
        end_date="20260630",
        datasets=["fina_indicator"],
    )

    assert valuation["datasets"]["daily_basic"]["rows"] == 1
    assert finance["datasets"]["fina_indicator"]["status"] == "warning"
    warnings = store.list_warnings()
    assert warnings[0]["dataset"] == "fina_indicator"


def test_sync_valuation_and_trading_honor_halt_checker(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    pro = FakePro()
    sync = TushareExtSync(store, pro)

    with pytest.raises(InterruptedError):
        sync.sync_valuation_snapshot(["20260630"], halt_checker=lambda: True)
    with pytest.raises(InterruptedError):
        sync.sync_trading_snapshot(["20260630"], datasets=["top_list"], halt_checker=lambda: True)

    assert pro.calls == []


def test_sync_price_tracks_writes_raw_periods_and_adj_factor(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    pro = FakePro()
    sync = TushareExtSync(store, pro)

    result = sync.sync_price_tracks(
        [{"code": "000001", "ts_code": "000001.SZ"}],
        start_date="20260101",
        end_date="20260630",
    )

    assert result["datasets"]["daily"]["rows"] == 1
    assert result["datasets"]["weekly"]["rows"] == 1
    assert result["datasets"]["monthly"]["rows"] == 1
    assert result["datasets"]["adj_factor"]["rows"] == 1
    assert store.latest_trade_date("daily", ts_code="000001.SZ") == "20260630"
    assert store.query_rows("adj_factor", ts_code="000001.SZ")[0]["adj_factor"] == 2.0


def test_sync_price_tracks_writes_qfq_when_pro_bar_available(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    pro = FakePro()
    pro_bar_calls = []

    def fake_pro_bar(**kwargs):
        pro_bar_calls.append(kwargs)
        return pd.DataFrame(
            [{"ts_code": kwargs["ts_code"], "trade_date": "20260630", "close": 11.0, "adj_factor": 2.0}]
        )

    sync = TushareExtSync(store, pro, pro_bar=fake_pro_bar)

    result = sync.sync_price_tracks(
        [{"code": "000001", "ts_code": "000001.SZ"}],
        start_date="20260101",
        end_date="20260630",
        datasets=["daily_qfq"],
    )

    assert result["datasets"]["daily_qfq"]["rows"] == 1
    assert pro_bar_calls[0]["adj"] == "qfq"
    assert store.latest_trade_date("daily_qfq", ts_code="000001.SZ") == "20260630"
