from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import web_server
from market_data.futures import FuturesService
from ops.events import redact
from strategy.pattern_feature_extractor import PatternFeatureExtractor
from utils import technical
from utils.csv_manager import CSVManager


def _price_frame(rows: int = 140) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=rows, freq="D")
    close = np.linspace(10.0, 30.0, rows)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.linspace(1000, 2000, rows),
            "amount": np.linspace(10_000, 20_000, rows),
            "turnover": 1.0,
            "market_cap": 10e9,
        }
    ).sort_values("date", ascending=False).reset_index(drop=True)


def test_missing_tencent_metrics_do_not_overwrite_existing_amount():
    existing = _price_frame(3)
    incoming = existing.iloc[:2].copy()
    incoming[["amount", "turnover", "market_cap"]] = np.nan

    preserved = CSVManager._preserve_existing_metrics(existing, incoming)

    assert preserved["amount"].tolist() == existing.iloc[:2]["amount"].tolist()
    assert preserved["turnover"].tolist() == existing.iloc[:2]["turnover"].tolist()
    assert preserved["market_cap"].tolist() == existing.iloc[:2]["market_cap"].tolist()


def test_redaction_covers_dingtalk_access_token_and_proxy_credentials():
    message = (
        "https://oapi.dingtalk.com/robot/send?access_token=ding-secret "
        "proxy=http://proxy-user:proxy-pass@127.0.0.1:8080"
    )

    cleaned = redact(message)

    assert "ding-secret" not in cleaned
    assert "proxy-user" not in cleaned
    assert "proxy-pass" not in cleaned
    assert cleaned.count("[REDACTED]") >= 3


def test_pattern_features_use_latest_trend_values_not_oldest_values():
    frame = _price_frame()
    trend = technical.calculate_zhixing_trend(frame)
    expected_latest_bias = (
        (frame.iloc[0]["close"] - trend.iloc[0]["short_term_trend"])
        / trend.iloc[0]["short_term_trend"]
        * 100
    )

    features = PatternFeatureExtractor(lookback_days=len(frame)).extract(frame)

    assert features["trend_structure"]["price_vs_short_pct"] == pytest.approx(
        round(expected_latest_bias, 4)
    )
    assert features["trend_structure"]["short_slope"] > 0


def test_nan_indicator_fallbacks_treat_missing_conditions_as_false(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise technical.quant_core.QuantCoreUnavailable("force fallback")

    monkeypatch.setattr(technical.quant_core, "exist_forward", unavailable)
    monkeypatch.setattr(technical.quant_core, "count_forward", unavailable)
    monkeypatch.setattr(technical.quant_core, "kdj_ascending", unavailable)

    cond = pd.Series([np.nan, False, np.nan])
    assert technical.EXIST(cond, 2).tolist() == [False, False, False]
    assert technical.COUNT(cond, 2).tolist() == [0.0, 0.0, 0.0]

    frame = _price_frame(12).sort_values("date").reset_index(drop=True)
    frame.loc[4, "high"] = np.nan
    kdj = technical.KDJ(frame)
    assert np.isfinite(kdj[["K", "D", "J"]].to_numpy()).all()


def test_futures_continuous_symbols_queries_latest_rows_only():
    class Store:
        def __init__(self):
            self.calls = []

        def query_rows(self, *_args, **_kwargs):
            raise AssertionError("full-history query must not run")

        def query_latest_rows(self, dataset, **kwargs):
            self.calls.append((dataset, kwargs))
            if kwargs["offset"]:
                return []
            return [
                {
                    "ts_code": "IF.CFX",
                    "trade_date": "20260710",
                    "mapping_ts_code": "IF2607.CFX",
                }
            ]

    store = Store()
    payload = FuturesService(store).continuous_symbols(active_on="20260710")

    assert payload["total"] == 1
    assert store.calls[0][0] == "fut_mapping"
    assert store.calls[0][1]["rows_per_symbol"] == 1
    assert store.calls[0][1]["end_date"] == "20260710"


def test_heatmap_rebuild_requires_post_and_session_token(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(web_server, "_active_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        web_server,
        "_rebuild_market_caches_serialized",
        lambda **kwargs: calls.append(kwargs) or {"errors": {}},
    )
    monkeypatch.setattr(
        web_server,
        "market_cache_health",
        lambda **_kwargs: {"refresh_pending": False},
    )
    client = web_server.app.test_client()

    assert client.get("/api/heatmap?refresh=1").status_code == 405
    assert client.post("/api/heatmap/rebuild").status_code == 403
    response = client.post(
        "/api/heatmap/rebuild",
        headers={"X-Quant-Session": web_server.WEB_SESSION_TOKEN},
    )

    assert response.status_code == 200
    assert len(calls) == 1


def test_emergency_stop_schedules_exit_even_if_incident_write_fails(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        web_server,
        "_write_emergency_incident",
        lambda _reason: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(
        web_server,
        "_schedule_process_termination",
        lambda delay: scheduled.append(delay),
    )
    monkeypatch.setattr(web_server, "_mark_jobs_emergency_halted", lambda _reason: None)

    try:
        assert web_server._trigger_emergency_stop("test") is None
        assert scheduled == [web_server.EMERGENCY_EXIT_DELAY_SECONDS]
    finally:
        web_server.halt_event.clear()
        web_server.shutdown_event.clear()


def test_wyckoff_admission_rejects_a_second_active_job():
    with web_server.wyckoff_jobs_lock:
        previous_jobs = dict(web_server.wyckoff_jobs)
        previous_events = dict(web_server.wyckoff_cancel_events)
        web_server.wyckoff_jobs.clear()
        web_server.wyckoff_cancel_events.clear()
        web_server.wyckoff_jobs["active-job"] = {
            "job_id": "active-job",
            "status": "running",
            "market": "a_share",
        }
    try:
        with pytest.raises(web_server.JobAdmissionConflict) as exc_info:
            web_server._start_market_wyckoff_job("a_share", "000001")
        assert exc_info.value.job["job_id"] == "active-job"
    finally:
        with web_server.wyckoff_jobs_lock:
            web_server.wyckoff_jobs.clear()
            web_server.wyckoff_jobs.update(previous_jobs)
            web_server.wyckoff_cancel_events.clear()
            web_server.wyckoff_cancel_events.update(previous_events)
