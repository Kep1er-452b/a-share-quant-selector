import os
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from collections import deque
from threading import Lock

import pandas as pd

import utils.data_provider as data_provider_module
import utils.tushare_fetcher as tushare_fetcher_module
from utils.csv_manager import CSVManager
from utils.data_provider import BaseDataProvider
from utils.tushare_fetcher import TushareFetcher


def test_csv_manager_lists_only_current_warehouse(tmp_path):
    root = tmp_path / "data"
    (root / "providers" / "akshare" / "00").mkdir(parents=True)
    (root / "providers" / "akshare" / "00" / "000001.csv").write_text("date,close\n2026-01-01,1\n")
    (root / "00").mkdir(parents=True)
    (root / "00" / "000002.csv").write_text("date,close\n2026-01-01,1\n")

    assert CSVManager(root).list_all_stocks() == ["000002"]


def test_tushare_proxy_fallback_clears_proxy_env(monkeypatch):
    fetcher = TushareFetcher.__new__(TushareFetcher)
    fetcher.proxy_fallback_lock = Lock()

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")

    observed = {}

    def probe():
        observed.update({key: os.environ.get(key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")})
        return "ok"

    assert fetcher._call_without_proxy(probe) == "ok"
    assert observed == {
        "HTTP_PROXY": None,
        "HTTPS_PROXY": None,
        "ALL_PROXY": None,
        "NO_PROXY": "*",
    }
    assert os.environ.get("HTTP_PROXY") == "http://127.0.0.1:9"


def test_tushare_proxy_fallback_retries_generic_sdk_error(monkeypatch):
    fetcher = TushareFetcher.__new__(TushareFetcher)
    fetcher._prefer_direct_network = False

    monkeypatch.setattr(fetcher, "_has_proxy_configured", lambda: True)
    monkeypatch.setattr(fetcher, "_call_without_proxy", lambda func, *args, **kwargs: "direct")

    def failing_call():
        raise OSError("ERROR.")

    assert fetcher._call_with_proxy_fallback(failing_call) == "direct"
    assert fetcher._prefer_direct_network is True


def test_tushare_daily_basic_date_cache_deduplicates_concurrent_requests(monkeypatch):
    fetcher = TushareFetcher.__new__(TushareFetcher)
    fetcher.daily_basic_cache_lock = Lock()
    fetcher.daily_basic_by_date_cache = {}
    fetcher.daily_basic_by_date_failures = set()
    fetcher.daily_basic_cache_hits = 0
    fetcher.daily_basic_rate_limit_wait = 0
    fetcher.daily_basic_calls = []
    call_count = 0
    call_lock = Lock()

    def daily_basic(**kwargs):
        nonlocal call_count
        with call_lock:
            call_count += 1
        time.sleep(0.02)
        return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [kwargs["trade_date"]]})

    monkeypatch.setattr(fetcher, "_call_daily_basic", daily_basic)
    with ThreadPoolExecutor(max_workers=8) as pool:
        frames = list(pool.map(fetcher._fetch_daily_basic_trade_date, ["20260618"] * 8))

    assert call_count == 1
    assert fetcher.daily_basic_cache_hits == 7
    assert all(len(frame) == 1 for frame in frames)


def test_tushare_rate_limit_sleep_happens_outside_daily_basic_lock(monkeypatch):
    fetcher = TushareFetcher.__new__(TushareFetcher)
    fetcher.daily_basic_lock = Lock()
    fetcher.daily_basic_calls = deque([100.0])
    fetcher.daily_basic_limit_per_minute = 1
    timeline = iter([100.0, 160.6, 160.6])

    monkeypatch.setattr(tushare_fetcher_module.time, "time", lambda: next(timeline))

    def fake_sleep(seconds):
        acquired = fetcher.daily_basic_lock.acquire(blocking=False)
        if acquired:
            fetcher.daily_basic_lock.release()
        assert acquired, "daily_basic throttle slept while holding the shared rate-limit lock"

    monkeypatch.setattr(tushare_fetcher_module.time, "sleep", fake_sleep)

    fetcher._throttle_daily_basic()

    assert list(fetcher.daily_basic_calls) == [160.6]


def _history_frame(adj_factor=None):
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=80, freq="B"),
        "open": 10.0,
        "high": 10.5,
        "low": 9.5,
        "close": 10.0,
        "volume": 1000,
        "amount": 10000,
        "turnover": 1.0,
        "market_cap": 1_000_000_000,
    })
    if adj_factor is not None:
        frame["adj_factor"] = adj_factor
    return frame.sort_values("date", ascending=False).reset_index(drop=True)


def test_qfq_anchor_change_requires_full_refresh(tmp_path):
    class Provider(BaseDataProvider):
        def fetch_stock_update(self, stock_code, days=10):
            return _history_frame(adj_factor=1.02).head(5)

    provider = Provider(str(tmp_path))
    provider.csv_manager.write_stock("000001", _history_frame(adj_factor=1.0))
    status_map = {"000001": {"latest_date": "2025-04-21"}}

    result = provider._sync_one_incremental(
        {"code": "000001", "name": "Test", "board": "main"},
        date(2025, 4, 22),
        status_map,
        {},
    )

    assert result["fallback_full"] is True
    assert result["qfq_anchor_changed"] is True
    assert provider.csv_manager.read_stock("000001")["adj_factor"].iloc[0] == 1.0


def test_old_qfq_csv_migrates_incrementally_when_overlap_matches(tmp_path):
    class Provider(BaseDataProvider):
        def fetch_stock_update(self, stock_code, days=10):
            return _history_frame(adj_factor=1.0).head(5)

    provider = Provider(str(tmp_path))
    provider.csv_manager.write_stock("000001", _history_frame())
    status_map = {"000001": {"latest_date": "2025-04-21"}}

    result = provider._sync_one_incremental(
        {"code": "000001", "name": "Test", "board": "main"},
        date(2025, 4, 22),
        status_map,
        {},
    )

    assert result["ok"] is True
    migrated = provider.csv_manager.read_stock("000001")
    assert "adj_factor" in migrated.columns
    assert migrated["adj_factor"].notna().sum() == 5


def test_old_qfq_csv_requires_full_refresh_when_overlap_changed(tmp_path):
    class Provider(BaseDataProvider):
        def fetch_stock_update(self, stock_code, days=10):
            changed = _history_frame(adj_factor=1.0).head(5)
            changed[["open", "high", "low", "close"]] *= 0.5
            return changed

    provider = Provider(str(tmp_path))
    provider.csv_manager.write_stock("000001", _history_frame())
    status_map = {"000001": {"latest_date": "2025-04-21"}}

    result = provider._sync_one_incremental(
        {"code": "000001", "name": "Test", "board": "main"},
        date(2025, 4, 22),
        status_map,
        {},
    )

    assert result["fallback_full"] is True
    assert result["qfq_anchor_changed"] is True


def test_full_refresh_retry_is_not_reported_as_failed_progress():
    events = []
    progress_state = {
        "processed": 0,
        "total": 1,
        "planned_total": 1,
        "retry": 0,
        "success": 0,
        "failed": 0,
        "warning": 0,
    }

    BaseDataProvider._emit_batch_progress(
        item={"code": "000001", "name": "Test"},
        result={"ok": False, "fallback_full": True, "qfq_anchor_changed": True},
        stage="sync",
        current_step="增量转全量重抓",
        progress_callback=events.append,
        progress_state=progress_state,
    )

    assert progress_state["retry"] == 1
    assert progress_state["failed"] == 0
    assert events[-1]["failed_count"] == 0
    assert events[-1]["retry_count"] == 1


def test_incremental_timeout_prevents_late_csv_write(monkeypatch, tmp_path):
    class SlowProvider(BaseDataProvider):
        def fetch_stock_update(self, stock_code, days=10):
            time.sleep(0.08)
            return _history_frame().head(5)

    provider = SlowProvider(str(tmp_path))
    provider._sync_max_workers = 1
    monkeypatch.setattr(data_provider_module, "DATA_SYNC_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(data_provider_module, "DATA_SYNC_POLL_SECONDS", 0.005)

    ok, fallback = provider._sync_parallel_batch(
        [{"code": "000001", "name": "Slow", "board": "main"}],
        date(2025, 4, 22),
        {"000001": {"latest_date": None}},
        {},
    )
    time.sleep(0.12)

    assert ok == []
    assert fallback[0]["_worker_error"] == "sync_timeout"
    assert provider.csv_manager.read_stock("000001").empty


def test_tushare_universe_refreshes_metadata_and_exposes_list_date(monkeypatch, tmp_path):
    fetcher = TushareFetcher.__new__(TushareFetcher)
    BaseDataProvider.__init__(fetcher, str(tmp_path))
    fetcher.stock_meta_file = tmp_path / "tushare_stock_map.json"
    fetcher.stock_meta_refresh_file = tmp_path / "tushare_stock_map_state.json"
    fetcher.stock_meta_file.write_text(
        json.dumps({"000001": {"name": "Old", "market": "主板"}}),
        encoding="utf-8",
    )

    def refresh(max_retries=3):
        fetcher._save_stock_metadata({
            "300001": {
                "name": "New",
                "market": "创业板",
                "exchange": "SZSE",
                "ts_code": "300001.SZ",
                "list_date": "20260601",
            }
        })
        return {"300001": "New"}

    monkeypatch.setattr(fetcher, "get_all_stock_codes", refresh)

    universe = fetcher.get_stock_universe()

    assert universe == [{
        "code": "300001",
        "name": "New",
        "board": "chinext",
        "market": "创业板",
        "exchange": "SZSE",
        "ts_code": "300001.SZ",
        "list_date": "20260601",
    }]
    state = json.loads(fetcher.stock_meta_refresh_file.read_text(encoding="utf-8"))
    assert state["stock_count"] == 1


def test_provider_state_is_not_complete_when_statuses_remain_stale(tmp_path):
    provider = BaseDataProvider(str(tmp_path / "providers" / "akshare")).configure_storage(tmp_path, "akshare")

    state = provider._write_provider_state(
        "all",
        None,
        [{"code": "000001", "name": "Test"}],
        date(2025, 4, 22),
        {"000001": {"status": "stale"}},
        {"stale": 1},
        success_count=0,
        failed_count=0,
        warning_count=0,
    )

    assert state["status"] == "partial"
    assert state["is_complete"] is False
