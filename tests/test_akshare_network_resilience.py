import pandas as pd

import utils.akshare_fetcher as akshare_fetcher
from utils.akshare_fetcher import AKShareFetcher


def _sample_history(source):
    dates = pd.date_range(end="2026-05-29", periods=220, freq="D")
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000,
            "amount": 10000,
            "turnover": 1.2,
            "market_cap": 0,
        }
    ).sort_values("date", ascending=False)
    frame["data_source"] = source
    return frame


def test_fetch_history_falls_back_to_tencent_when_akshare_fails(monkeypatch, tmp_path):
    fetcher = AKShareFetcher(data_dir=str(tmp_path))

    def fail_akshare_history(**kwargs):
        raise RuntimeError("ProxyError: broken system proxy")

    seen = {}

    def fake_tencent_history(stock_code, years=6, source="tencent:fqkline"):
        seen["stock_code"] = stock_code
        seen["source"] = source
        return _sample_history(source)

    monkeypatch.setattr(akshare_fetcher.ak, "stock_zh_a_hist", fail_akshare_history)
    monkeypatch.setattr(fetcher, "_fetch_stock_history_http", fake_tencent_history)

    frame = fetcher.fetch_stock_history("001220", years=1)

    assert not frame.empty
    assert seen == {"stock_code": "001220", "source": "tencent:fqkline:fallback"}
    assert set(frame["data_source"]) == {"tencent:fqkline:fallback"}
    assert fetcher.get_runtime_stats()["akshare_history_error"] == 1
    assert fetcher.get_runtime_stats()["tencent_history_fallback_success"] == 1


def test_normalize_akshare_history_does_not_fetch_per_stock_market_cap(monkeypatch, tmp_path):
    fetcher = AKShareFetcher(data_dir=str(tmp_path))
    raw = pd.DataFrame(
        {
            "日期": ["2026-05-29", "2026-05-28"],
            "开盘": [10, 9],
            "最高": [11, 10],
            "最低": [9, 8],
            "收盘": [10.5, 9.5],
            "成交量": [1000, 900],
            "成交额": [10000, 9000],
            "换手率": [1.2, 1.1],
        }
    )

    def fail_market_cap(stock_code):
        raise AssertionError("per-stock market cap request should not run")

    monkeypatch.setattr(fetcher, "_get_realtime_market_cap", fail_market_cap)

    frame = fetcher._normalize_akshare_history("000001", raw, "akshare:stock_zh_a_hist")

    assert list(frame["market_cap"]) == [0, 0]
    assert set(frame["data_source"]) == {"akshare:stock_zh_a_hist"}


def test_fetch_update_falls_back_to_tencent_when_akshare_fails(monkeypatch, tmp_path):
    fetcher = AKShareFetcher(data_dir=str(tmp_path))

    def fail_akshare_history(**kwargs):
        raise RuntimeError("ProxyError: broken system proxy")

    def fake_tencent_update(stock_code, days=10, source="tencent:fqkline:update"):
        return _sample_history(source).head(5)

    monkeypatch.setattr(akshare_fetcher.ak, "stock_zh_a_hist", fail_akshare_history)
    monkeypatch.setattr(fetcher, "_fetch_stock_update_http", fake_tencent_update)

    frame = fetcher.fetch_stock_update("001220", days=3)

    assert not frame.empty
    assert set(frame["data_source"]) == {"tencent:fqkline:update:fallback"}
    assert fetcher.get_runtime_stats()["akshare_update_error"] == 1
    assert fetcher.get_runtime_stats()["tencent_update_fallback_success"] == 1


def test_recent_listing_short_history_is_accepted(tmp_path):
    fetcher = AKShareFetcher(data_dir=str(tmp_path))
    recent = pd.DataFrame(
        {
            "date": pd.date_range(end=pd.Timestamp.today().normalize(), periods=6, freq="D"),
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000,
            "amount": 10000,
            "turnover": 1.2,
            "market_cap": 0,
        }
    )

    coverage = fetcher._history_coverage_report(recent, years=6)

    assert coverage["ok"] is True
    assert coverage["recent_listing"] is True
