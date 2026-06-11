import pandas as pd
import pytest

import utils.akshare_fetcher as akshare_fetcher
from utils.akshare_fetcher import AKShareFetcher
from utils.data_provider import DataProviderError
from utils.tencent_fetcher import TencentFetcher


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


def test_tencent_provider_uses_conservative_parallelism(tmp_path):
    fetcher = TencentFetcher(data_dir=str(tmp_path))
    configured = TencentFetcher(
        data_dir=str(tmp_path / "configured"),
        config={"data_source": {"tencent": {"max_workers": 2}}},
    )

    assert fetcher._sync_max_workers == 4
    assert configured._sync_max_workers == 2


def test_tencent_waf_response_aborts_without_route_retry(monkeypatch, tmp_path):
    fetcher = TencentFetcher(
        data_dir=str(tmp_path),
        config={"data_source": {"tencent": {"min_request_interval_seconds": 0}}},
    )
    calls = []

    class FakeResponse:
        status_code = 501
        text = (
            '<script>window.location.href='
            '"https://waf.tencent.com/501page.html?id=test"</script>'
        )

        def raise_for_status(self):
            raise AssertionError("WAF response should be detected first")

    class FakeSession:
        trust_env = True

        def get(self, url, **kwargs):
            calls.append((url, self.trust_env))
            return FakeResponse()

    monkeypatch.setattr(akshare_fetcher.requests, "Session", FakeSession)

    with pytest.raises(DataProviderError, match="WAF 501"):
        fetcher._request_get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        )

    assert calls == [
        ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get", True),
    ]
    assert fetcher.get_runtime_stats()["tencent_waf_501"] == 1


def test_sync_batch_propagates_data_provider_error(monkeypatch, tmp_path):
    fetcher = TencentFetcher(data_dir=str(tmp_path))

    def fail_sync(*args, **kwargs):
        raise DataProviderError("Tencent WAF blocked")

    monkeypatch.setattr(fetcher, "_sync_one_incremental", fail_sync)

    with pytest.raises(DataProviderError, match="WAF blocked"):
        fetcher._sync_parallel_batch(
            [{"code": "000001", "name": "平安银行"}],
            latest_trade_date=pd.Timestamp("2026-06-11").date(),
            status_map={"000001": {"latest_date": "2026-06-10"}},
            market_cap_map={},
            progress_state={
                "processed": 0,
                "total": 1,
                "planned_total": 1,
                "retry": 0,
                "success": 0,
                "failed": 0,
            },
        )
