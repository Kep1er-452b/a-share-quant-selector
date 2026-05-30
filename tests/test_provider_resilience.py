import os
from threading import Lock

from utils.csv_manager import CSVManager
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
