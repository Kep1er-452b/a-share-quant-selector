from datetime import date

import pandas as pd
import pytest

import web_server
from utils.tushare_ext_store import TushareExtStore


def _index_store(tmp_path):
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
    return store


def test_index_kline_api_uses_tushare_cache_and_month_range(monkeypatch, tmp_path):
    store = _index_store(tmp_path)
    monkeypatch.setattr(web_server, "_tushare_ext_store", lambda: store, raising=False)
    monkeypatch.setattr(web_server, "_ensure_tushare_index_cache", lambda *args, **kwargs: {"status": "skipped"}, raising=False)
    monkeypatch.setattr(
        web_server,
        "_fetch_index_kline",
        lambda *args, **kwargs: {"source": "legacy", "candles": [{"date": "2026-06-30"}]},
        raising=False,
    )

    response = web_server.app.test_client().get("/api/index-kline?symbol=sh000001&months=99")
    payload = response.get_json()

    assert payload["success"] is True
    assert payload["data"]["source"] == "tushare:index_daily"
    assert payload["data"]["months"] == 6
    assert payload["data"]["candles"][-1]["MA50"] is not None
    assert payload["data"]["candles"][-1]["MA200"] is not None


class StockManager:
    data_dir = "/tmp/test-stock-data"

    def read_stock_for_analysis(self, code):
        rows = []
        for offset, trade_date in enumerate(pd.bdate_range("2026-06-29", "2026-06-30")):
            rows.append(
                {
                    "date": trade_date,
                    "open": 10 + offset / 100,
                    "high": 10.2 + offset / 100,
                    "low": 9.8 + offset / 100,
                    "close": 10.1 + offset / 100,
                    "volume": 100000 + offset,
                    "amount": 2000000 + offset,
                    "turnover": 1.0,
                    "market_cap": 10000000000,
                }
            )
        return pd.DataFrame(rows).sort_values("date", ascending=False).reset_index(drop=True)


class LongStockManager(StockManager):
    def read_stock_for_analysis(self, code):
        rows = []
        for offset, trade_date in enumerate(pd.bdate_range("2024-01-01", periods=500)):
            rows.append(
                {
                    "date": trade_date,
                    "open": 10 + offset / 100,
                    "high": 10.2 + offset / 100,
                    "low": 9.8 + offset / 100,
                    "close": 10.1 + offset / 100,
                    "volume": 100000 + offset,
                    "amount": 2000000 + offset,
                    "turnover": 1.0,
                    "market_cap": 10000000000,
                }
            )
        return pd.DataFrame(rows).sort_values("date", ascending=False).reset_index(drop=True)


def test_stock_detail_api_returns_bounded_indicator_context(monkeypatch, tmp_path):
    monkeypatch.setattr(web_server, "_active_csv_manager", lambda: LongStockManager())
    monkeypatch.setattr(web_server, "_load_stock_names", lambda: {"000001": "平安银行"})
    monkeypatch.setattr(web_server, "_tushare_ext_store", lambda: TushareExtStore(tmp_path / "extended"), raising=False)

    response = web_server.app.test_client().get(
        "/api/stock/000001?period=daily&limit=260&indicator_lookback=200"
    )
    payload = response.get_json()

    assert payload["success"] is True
    assert len(payload["data"]) == 260
    assert len(payload["calculation_data"]) == 459
    assert payload["calculation_data"][0]["date"] == payload["data"][0]["date"]
    assert payload["calculation_data"][259]["date"] == payload["data"][259]["date"]


def test_stock_detail_api_adds_extension_payload(monkeypatch, tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    store.upsert_rows(
        "stock_basic",
        [{"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "industry": "银行", "area": "深圳"}],
        key_fields=("ts_code",),
    )
    store.upsert_rows(
        "daily_basic",
        [{"ts_code": "000001.SZ", "trade_date": "20260630", "pe_ttm": 5.2, "pb": 0.6}],
        key_fields=("ts_code", "trade_date"),
    )
    store.upsert_rows(
        "fina_indicator",
        [{"ts_code": "000001.SZ", "end_date": "20260331", "roe": 12.3}],
        key_fields=("ts_code", "end_date"),
        trade_date_field="end_date",
    )
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
    monkeypatch.setattr(web_server, "_active_csv_manager", lambda: StockManager())
    monkeypatch.setattr(web_server, "_load_stock_names", lambda: {"000001": "平安银行"})
    monkeypatch.setattr(web_server, "_tushare_ext_store", lambda: store, raising=False)

    response = web_server.app.test_client().get("/api/stock/000001?period=daily")
    payload = response.get_json()

    assert payload["success"] is True
    assert payload["meta"]["industry"] == "银行"
    assert payload["valuation"]["pe_ttm"] == 5.2
    assert payload["financial"]["roe"] == 12.3
    assert payload["adjusted_data"][1]["close"] == 5.25
    assert isinstance(payload["data"], list)


class PartialAdjustedStockManager(StockManager):
    def read_stock_for_analysis(self, code):
        rows = []
        for offset, trade_date in enumerate(pd.bdate_range("2026-06-26", "2026-06-30")):
            rows.append(
                {
                    "date": trade_date,
                    "open": 10 + offset / 100,
                    "high": 10.2 + offset / 100,
                    "low": 9.8 + offset / 100,
                    "close": 10.1 + offset / 100,
                    "volume": 100000 + offset,
                    "amount": 2000000 + offset,
                    "turnover": 1.0,
                    "market_cap": 10000000000,
                }
            )
        return pd.DataFrame(rows).sort_values("date", ascending=False).reset_index(drop=True)


class LongStockManager(StockManager):
    def read_stock_for_analysis(self, code):
        rows = []
        for offset, trade_date in enumerate(pd.bdate_range("2023-01-02", "2026-06-30")):
            rows.append(
                {
                    "date": trade_date,
                    "open": 10 + offset / 100,
                    "high": 10.2 + offset / 100,
                    "low": 9.8 + offset / 100,
                    "close": 10.1 + offset / 100,
                    "volume": 100000 + offset,
                    "amount": 2000000 + offset,
                    "turnover": 1.0,
                    "market_cap": 10000000000,
                }
            )
        return pd.DataFrame(rows).sort_values("date", ascending=False).reset_index(drop=True)


def test_stock_detail_api_accepts_chart_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(web_server, "_active_csv_manager", lambda: LongStockManager())
    monkeypatch.setattr(web_server, "_load_stock_names", lambda: {"000001": "平安银行"})
    monkeypatch.setattr(web_server, "_tushare_ext_store", lambda: TushareExtStore(tmp_path / "extended"), raising=False)

    default_response = web_server.app.test_client().get("/api/stock/000001?period=daily")
    limited_response = web_server.app.test_client().get("/api/stock/000001?period=daily&limit=520")
    all_response = web_server.app.test_client().get("/api/stock/000001?period=daily&limit=all")

    default_payload = default_response.get_json()
    limited_payload = limited_response.get_json()
    all_payload = all_response.get_json()

    assert default_payload["success"] is True
    assert default_payload["limit"] == 260
    assert len(default_payload["data"]) == 260
    assert limited_payload["limit"] == 520
    assert len(limited_payload["data"]) == 520
    assert all_payload["limit"] == all_payload["total_bars"]
    assert len(all_payload["data"]) == all_payload["total_bars"]
    assert len(all_payload["data"]) > 520


def test_index_detail_api_accepts_chart_limit(monkeypatch, tmp_path):
    store = _index_store(tmp_path)
    monkeypatch.setattr(web_server, "_tushare_ext_store", lambda: store, raising=False)
    monkeypatch.setattr(web_server, "_ensure_tushare_index_cache", lambda *args, **kwargs: {"status": "skipped"}, raising=False)

    response = web_server.app.test_client().get("/api/index-detail/sh000001?period=daily&limit=520")
    payload = response.get_json()

    assert payload["success"] is True
    assert payload["data"]["limit"] == 520
    assert len(payload["data"]["candles"]) == 520


def test_stock_detail_api_drops_partial_adjusted_overlay(monkeypatch, tmp_path):
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
    monkeypatch.setattr(web_server, "_active_csv_manager", lambda: PartialAdjustedStockManager())
    monkeypatch.setattr(web_server, "_load_stock_names", lambda: {"000001": "平安银行"})
    monkeypatch.setattr(web_server, "_tushare_ext_store", lambda: store, raising=False)

    response = web_server.app.test_client().get("/api/stock/000001?period=daily")
    payload = response.get_json()

    assert payload["success"] is True
    assert payload["adjusted_data"] == []


def test_dashboard_pulse_api_adds_market_trading_summary(monkeypatch, tmp_path):
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
    monkeypatch.setattr(web_server, "_tushare_ext_store", lambda: store, raising=False)
    monkeypatch.setattr(
        web_server,
        "build_heatmap_payload",
        lambda **kwargs: {
            "latest_date": "2026-06-30",
            "stock_count": 1,
            "group_count": 1,
            "ticker_stats": {"up_count": 1, "down_count": 0},
            "groups": [],
            "header_indices": [],
        },
    )
    monkeypatch.setattr(web_server, "market_cache_health", lambda data_dir: {})
    monkeypatch.setattr(web_server, "load_active_provider", lambda data_root: {"provider": "tushare"})
    monkeypatch.setattr(web_server, "list_provider_statuses", lambda data_root: [])

    response = web_server.app.test_client().get("/api/dashboard-pulse")
    payload = response.get_json()

    assert payload["success"] is True
    trading = payload["data"]["market_trading"]
    assert trading["metrics"]["market_amount"]["value"] == 10.0
    assert trading["metrics"]["market_amount"]["delta"] == 4.0
    assert trading["metrics"]["dragon_tiger_net"]["value"] == 3.0
    assert trading["metrics"]["dragon_tiger_net"]["delta"] == 2.0


class ExtensionPro:
    def stock_basic(self, **kwargs):
        return pd.DataFrame([{"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行"}])

    def namechange(self, **kwargs):
        return pd.DataFrame([{"ts_code": "000001.SZ", "name": "平安银行", "start_date": "19910403"}])

    def hs_const(self, **kwargs):
        return pd.DataFrame([{"ts_code": "000001.SZ", "hs_type": kwargs["hs_type"], "in_date": "20200101"}])

    def trade_cal(self, **kwargs):
        return pd.DataFrame([{"exchange": "SSE", "cal_date": "20260630", "is_open": 1}])

    def index_daily(self, **kwargs):
        return pd.DataFrame([{"ts_code": kwargs["ts_code"], "trade_date": "20260630", "close": 3000}])

    def daily_basic(self, **kwargs):
        return pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": kwargs["trade_date"], "pe_ttm": 5.2}])

    def daily(self, **kwargs):
        return pd.DataFrame([{"ts_code": kwargs["ts_code"], "trade_date": "20260630", "close": 21.0}])

    def weekly(self, **kwargs):
        return pd.DataFrame([{"ts_code": kwargs["ts_code"], "trade_date": "20260626", "close": 20.0}])

    def monthly(self, **kwargs):
        return pd.DataFrame([{"ts_code": kwargs["ts_code"], "trade_date": "20260630", "close": 19.0}])

    def adj_factor(self, **kwargs):
        return pd.DataFrame([{"ts_code": kwargs["ts_code"], "trade_date": "20260630", "adj_factor": 2.0}])

    def top_list(self, **kwargs):
        return pd.DataFrame([{"trade_date": kwargs["trade_date"], "ts_code": "000001.SZ", "net_amount": 300}])

    def top_inst(self, **kwargs):
        return pd.DataFrame()

    def fina_indicator(self, **kwargs):
        return pd.DataFrame([{"ts_code": kwargs["ts_code"], "end_date": "20260331", "roe": 12.3}])


class ExtensionProvider:
    pro = ExtensionPro()


def test_refresh_tushare_extension_data_runs_update_stages(monkeypatch, tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    monkeypatch.setattr(web_server, "_tushare_ext_store", lambda: store, raising=False)
    monkeypatch.setattr(web_server, "_append_update_job_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_server, "_append_system_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_server, "_update_update_job", lambda *args, **kwargs: None)

    result = web_server._refresh_tushare_extension_data_for_job(
        "job-test",
        ExtensionProvider(),
        [{"code": "000001", "ts_code": "000001.SZ"}],
        "2026-06-30",
        full_backfill=True,
        financial_datasets=["fina_indicator"],
        trading_datasets=["top_list", "top_inst"],
    )

    assert result["status"] == "completed"
    assert store.latest_trade_date("stock_basic", ts_code="000001.SZ") is None
    assert store.latest_trade_date("daily", ts_code="000001.SZ") == "20260630"
    assert store.query_rows("adj_factor", ts_code="000001.SZ")[0]["adj_factor"] == 2.0
    assert store.latest_trade_date("daily_basic", ts_code="000001.SZ") == "20260630"
    assert store.query_rows("fina_indicator", ts_code="000001.SZ")[0]["roe"] == 12.3
    assert store.query_rows("top_list", ts_code="000001.SZ")[0]["net_amount"] == 300


def test_refresh_tushare_extension_data_skips_heavy_stages_by_default(monkeypatch, tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    monkeypatch.setattr(web_server, "_tushare_ext_store", lambda: store, raising=False)
    monkeypatch.setattr(web_server, "_append_update_job_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_server, "_append_system_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_server, "_update_update_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_server, "_load_config", lambda: {"data_source": {"tushare": {}}})

    result = web_server._refresh_tushare_extension_data_for_job(
        "job-test",
        ExtensionProvider(),
        [{"code": "000001", "ts_code": "000001.SZ"}],
        "2026-06-30",
        trading_datasets=["top_list"],
    )

    assert result["datasets"]["prices"]["status"] == "skipped"
    assert result["datasets"]["financial"]["status"] == "skipped"
    assert store.latest_trade_date("daily", ts_code="000001.SZ") is None
    assert store.query_rows("fina_indicator", ts_code="000001.SZ") == []
    assert store.query_rows("top_list", ts_code="000001.SZ")


def test_refresh_tushare_extension_data_honors_job_cancel_before_stages(monkeypatch, tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    monkeypatch.setattr(web_server, "_tushare_ext_store", lambda: store, raising=False)
    monkeypatch.setattr(web_server, "_append_update_job_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_server, "_append_system_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_server, "_update_update_job", lambda *args, **kwargs: None)

    with pytest.raises(InterruptedError):
        web_server._refresh_tushare_extension_data_for_job(
            "job-test",
            ExtensionProvider(),
            [{"code": "000001", "ts_code": "000001.SZ"}],
            "2026-06-30",
            halt_checker=lambda: True,
        )

    assert store.query_rows("stock_basic") == []


def test_warm_tushare_index_cache_background_is_best_effort(monkeypatch, tmp_path):
    store = _index_store(tmp_path)
    calls = []
    monkeypatch.setattr(web_server, "_tushare_ext_store", lambda: store, raising=False)
    monkeypatch.setattr(web_server, "_ensure_tushare_index_cache", lambda **kwargs: calls.append(kwargs) or {"status": "completed"})
    monkeypatch.setattr(web_server, "_append_system_log", lambda *args, **kwargs: None)

    web_server._warm_tushare_index_cache_background()

    assert calls
    assert calls[0]["store"] is store


def test_favicon_route_avoids_browser_console_404():
    response = web_server.app.test_client().get("/favicon.ico")
    assert response.status_code == 204
