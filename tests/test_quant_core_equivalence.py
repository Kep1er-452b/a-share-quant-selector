from __future__ import annotations

import contextlib
import importlib
import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy.strategy_registry import StrategyRegistry
from utils import quant_core
from utils.csv_manager import CSVManager
from utils.selection_worker import build_worker_context, process_selection_chunk
from utils.technical import (
    COUNT,
    EMA,
    EXIST,
    HHV,
    KDJ,
    LLV,
    MA,
    REF,
    SMA,
    SUM,
    calculate_zhixing_trend,
    prepare_selection_features,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def ensure_quant_core_built():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_quant_core.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip(f"quant core build failed: {result.stderr.strip()}")
    importlib.reload(quant_core)
    if not quant_core.available():
        pytest.skip(f"quant core unavailable: {quant_core.load_error()}")


def _price_frame(rows=180, seed=17):
    rng = np.random.default_rng(seed)
    close = 20 + rng.normal(0, 0.7, rows).cumsum()
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=rows, freq="B"),
        "open": close + rng.normal(0, 0.2, rows),
        "high": close + rng.uniform(0.3, 1.0, rows),
        "low": close - rng.uniform(0.3, 1.0, rows),
        "close": close,
        "volume": rng.integers(1000, 100000, rows),
        "amount": rng.integers(100000, 1000000, rows),
        "turnover": rng.random(rows),
        "market_cap": np.full(rows, 8_000_000_000.0),
    })


def _with_python_fallback(monkeypatch, func, *args, **kwargs):
    monkeypatch.setenv("QUANT_CORE_DISABLE", "1")
    try:
        return func(*args, **kwargs)
    finally:
        monkeypatch.delenv("QUANT_CORE_DISABLE", raising=False)


def test_quant_core_available_and_can_be_forced_off(monkeypatch):
    assert quant_core.available()
    monkeypatch.setenv("QUANT_CORE_DISABLE", "1")
    assert not quant_core.available()


def test_core_series_indicators_match_python_fallback(monkeypatch):
    frame = _price_frame(90).iloc[::-1].reset_index(drop=True)
    close = frame["close"]
    volume = frame["volume"]
    cond = close > REF(close, 1)

    comparisons = [
        (MA, (close, 9)),
        (EMA, (close, 10)),
        (LLV, (close, 13)),
        (HHV, (close, 13)),
        (SMA, (close, 9, 3)),
        (REF, (close, 2)),
        (COUNT, (cond, 7)),
        (SUM, (volume, 11)),
    ]
    for func, args in comparisons:
        expected = _with_python_fallback(monkeypatch, func, *args)
        actual = func(*args)
        pd.testing.assert_series_equal(actual, expected)

    expected_exist = _with_python_fallback(monkeypatch, EXIST, cond, 7)
    actual_exist = EXIST(cond, 7)
    pd.testing.assert_series_equal(actual_exist, expected_exist)


def test_core_dataframe_indicators_match_python_fallback(monkeypatch):
    frames = [
        _price_frame(120),
        _price_frame(120).iloc[::-1].reset_index(drop=True),
        _price_frame(120).sample(frac=1, random_state=3).reset_index(drop=True),
    ]
    for frame in frames:
        expected_kdj = _with_python_fallback(monkeypatch, KDJ, frame.copy())
        actual_kdj = KDJ(frame.copy())
        pd.testing.assert_frame_equal(actual_kdj.reset_index(drop=True), expected_kdj.reset_index(drop=True))

        expected_trend = _with_python_fallback(monkeypatch, calculate_zhixing_trend, frame.copy())
        actual_trend = calculate_zhixing_trend(frame.copy())
        pd.testing.assert_frame_equal(actual_trend.reset_index(drop=True), expected_trend.reset_index(drop=True))


def test_prepare_selection_features_matches_python_fallback(monkeypatch):
    frame = _price_frame(140).iloc[::-1].reset_index(drop=True)
    expected = _with_python_fallback(monkeypatch, prepare_selection_features, frame.copy())
    actual = prepare_selection_features(frame.copy())

    columns = ["ref_close_1", "ref_vol_1", "REAL_YANG", "REAL_YIN", "K", "D", "J", "short_term_trend", "bull_bear_line"]
    for column in columns:
        if expected[column].dtype == bool:
            pd.testing.assert_series_equal(actual[column], expected[column])
        else:
            pd.testing.assert_series_equal(actual[column], expected[column], rtol=1e-12, atol=1e-12)


def test_quant_core_missing_library_falls_back(monkeypatch):
    frame = _price_frame(40)
    expected = _with_python_fallback(monkeypatch, MA, frame["close"], 5)
    monkeypatch.setattr(quant_core, "_LIB", None)
    monkeypatch.setenv("QUANT_CORE_LIBRARY", str(ROOT / "build" / "quant_core" / "missing.dylib"))

    assert not quant_core.available()
    actual = MA(frame["close"], 5)
    pd.testing.assert_series_equal(actual, expected)


def _strategy_names():
    registry = StrategyRegistry(ROOT / "config" / "strategy_params.yaml")
    with contextlib.redirect_stdout(io.StringIO()):
        registry.auto_register_from_directory("strategy")
    return registry.list_strategies()


def _run_selection(data_dir: Path, candidates, monkeypatch, disabled: bool):
    if disabled:
        monkeypatch.setenv("QUANT_CORE_DISABLE", "1")
    else:
        monkeypatch.delenv("QUANT_CORE_DISABLE", raising=False)
    context = build_worker_context(str(data_dir), _strategy_names(), str(ROOT / "config" / "strategy_params.yaml"))
    with contextlib.redirect_stdout(io.StringIO()):
        return process_selection_chunk(candidates, "all", False, context)


def test_selection_results_match_with_and_without_core_on_synthetic_data(tmp_path, monkeypatch):
    manager = CSVManager(tmp_path)
    candidates = []
    for offset in range(4):
        code = f"00000{offset}"
        manager.write_stock(code, _price_frame(180, seed=30 + offset))
        candidates.append((code, f"Sample {offset}"))

    enabled = _run_selection(tmp_path, candidates, monkeypatch, disabled=False)
    disabled = _run_selection(tmp_path, candidates, monkeypatch, disabled=True)
    assert enabled == disabled


def _active_data_dir() -> Path:
    state_path = ROOT / "data" / "active_provider.json"
    if not state_path.exists():
        return ROOT / "data"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    provider_path = state.get("provider_state", {}).get("path")
    if provider_path:
        return ROOT / provider_path
    provider = state.get("active_provider")
    if provider:
        return ROOT / "data" / "providers" / provider
    return ROOT / "data"


def test_selection_results_match_with_and_without_core_on_local_sample(monkeypatch):
    data_dir = _active_data_dir()
    manager = CSVManager(data_dir)
    codes = manager.list_all_stocks()[:5]
    if len(codes) < 5:
        pytest.skip("local provider sample is unavailable")
    candidates = [(code, code) for code in codes]

    enabled = _run_selection(data_dir, candidates, monkeypatch, disabled=False)
    disabled = _run_selection(data_dir, candidates, monkeypatch, disabled=True)
    assert enabled == disabled
