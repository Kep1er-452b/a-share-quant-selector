from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from utils.tushare_ext_store import TushareExtStore
from utils.tushare_ext_workflow import refresh_tushare_extension_data


class WorkflowPro:
    def stock_basic(self, **kwargs):
        return pd.DataFrame([{"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行"}])

    def namechange(self, **kwargs):
        return pd.DataFrame()

    def hs_const(self, **kwargs):
        return pd.DataFrame()

    def trade_cal(self, **kwargs):
        return pd.DataFrame([
            {"exchange": "SSE", "cal_date": "20260629", "is_open": 1},
            {"exchange": "SSE", "cal_date": "20260630", "is_open": 1},
        ])

    def index_daily(self, **kwargs):
        return pd.DataFrame()

    def daily_basic(self, **kwargs):
        return pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": kwargs["trade_date"], "total_mv": 100}])

    def top_list(self, **kwargs):
        return pd.DataFrame([{"trade_date": kwargs["trade_date"], "ts_code": "000001.SZ", "net_amount": 10}])


class WorkflowProvider:
    pro = WorkflowPro()
    ts = None


def test_refresh_tushare_extension_workflow_skips_heavy_stages_by_default(tmp_path):
    store = TushareExtStore(tmp_path / "extended")

    result = refresh_tushare_extension_data(
        WorkflowProvider(),
        [{"code": "000001", "ts_code": "000001.SZ"}],
        "2026-06-30",
        store=store,
        config={"data_source": {"tushare": {}}},
        trading_datasets=["top_list"],
    )

    assert result["status"] == "completed"
    assert result["datasets"]["prices"]["status"] == "skipped"
    assert result["datasets"]["financial"]["status"] == "skipped"
    assert store.query_rows("daily") == []
    assert store.latest_trade_date("daily_basic", ts_code="000001.SZ") == "20260630"
    assert [row["trade_date"] for row in store.query_rows("top_list", ts_code="000001.SZ", descending=False)] == [
        "20260629",
        "20260630",
    ]


def test_cli_update_runs_shared_tushare_extension_workflow(monkeypatch, tmp_path):
    calls = []

    class FakeFetcher:
        pro = object()
        ts = None

        def run_preflight(self):
            return {"status": "ok", "latest_trade_date": "2026-06-30"}

        def sync_target_data(self, target_universe, **kwargs):
            return {"latest_trade_date": "2026-06-30", "status": "ready"}

    quant = main.QuantSystem.__new__(main.QuantSystem)
    quant.config = {"data_source": {"tushare": {}}}
    quant.data_dir = str(tmp_path / "data")
    quant.provider_name = "tushare"
    quant.fetcher = FakeFetcher()
    quant._last_sync_summary = None
    quant._resolve_target_universe = lambda **kwargs: [{"code": "000001", "ts_code": "000001.SZ"}]
    quant._activate_fetcher_provider = lambda: None

    def fake_refresh(provider, target_universe, latest_trade_date, **kwargs):
        calls.append({
            "provider": provider,
            "target_universe": target_universe,
            "latest_trade_date": latest_trade_date,
            "data_root": kwargs.get("data_root"),
            "config": kwargs.get("config"),
        })
        return {"status": "completed", "datasets": {}, "warnings": []}

    monkeypatch.setattr(main, "refresh_tushare_extension_data", fake_refresh)

    quant.update_data(max_stocks=1)

    assert calls == [{
        "provider": quant.fetcher,
        "target_universe": [{"code": "000001", "ts_code": "000001.SZ"}],
        "latest_trade_date": "2026-06-30",
        "data_root": quant.data_dir,
        "config": quant.config,
    }]
