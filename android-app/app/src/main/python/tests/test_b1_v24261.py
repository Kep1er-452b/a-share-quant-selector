from pathlib import Path
import contextlib
import io
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from strategy.b1_v24261 import B1V24261Strategy
from strategy.strategy_registry import StrategyRegistry
from utils.technical import COUNT, HHV, LLV, MA, REF, prepare_selection_features, prepare_strategy_shared_features


ROOT = Path(__file__).resolve().parents[1]


def _price_frame(rows=200):
    rng = np.random.default_rng(24261)
    close = 20 + rng.normal(0, 0.7, rows).cumsum()
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=rows, freq="B"),
        "open": close + rng.normal(0, 0.25, rows),
        "high": close + rng.uniform(0.3, 1.0, rows),
        "low": close - rng.uniform(0.3, 1.0, rows),
        "close": close,
        "volume": rng.integers(10_000, 800_000, rows),
        "market_cap": np.full(rows, 8_000_000_000.0),
    })
    return frame.sort_values("date", ascending=False).reset_index(drop=True)


def test_b1_v24261_matches_formula_windows_and_thresholds():
    strategy = B1V24261Strategy()
    prepared = prepare_selection_features(_price_frame())
    prepared = prepare_strategy_shared_features(prepared, ["B1V24261Strategy"])
    result = strategy.calculate_indicators(prepared)

    ref_close_1 = prepared["ref_close_1"]
    ref_vol_1 = prepared["ref_vol_1"]
    real_yang_volume = prepared["volume"] * prepared["REAL_YANG"].astype(int)
    real_yin_volume = prepared["volume"] * prepared["REAL_YIN"].astype(int)

    expected_yang_28 = real_yang_volume.iloc[::-1].rolling(28, min_periods=1).sum().iloc[::-1].reset_index(drop=True)
    expected_yin_28 = real_yin_volume.iloc[::-1].rolling(28, min_periods=1).sum().iloc[::-1].reset_index(drop=True)
    pd.testing.assert_series_equal(result["VOL_YANG1"], expected_yang_28, check_names=False)
    pd.testing.assert_series_equal(result["VOL_YIN1"], expected_yin_28, check_names=False)

    expected_yangyin_1 = result["VOL_YANG1"] > 1.6 * result["VOL_YIN1"]
    expected_yangyin_2 = result["VOL_YANG2"] > 2.2 * result["VOL_YIN2"]
    expected_yangyin_3 = result["VOL_YANG3"] > 1.2 * result["VOL_YIN3"]
    pd.testing.assert_series_equal(result["YANGYIN_OK1"], expected_yangyin_1, check_names=False)
    pd.testing.assert_series_equal(result["YANGYIN_OK2"], expected_yangyin_2, check_names=False)
    pd.testing.assert_series_equal(result["YANGYIN_OK3"], expected_yangyin_3, check_names=False)

    o85 = LLV(prepared["open"], 21) + 0.95 * (HHV(prepared["open"], 21) - LLV(prepared["open"], 21))
    fd15 = (
        (prepared["close"] < ref_close_1)
        & (prepared["close"] <= prepared["open"])
        & (prepared["volume"] >= 1.2 * ref_vol_1)
    )
    expected_good = COUNT((prepared["open"] >= o85) & fd15, 14) <= 0
    pd.testing.assert_series_equal(result["GOOD28"], expected_good, check_names=False)

    plry = (
        (prepared["volume"] > 1.95 * ref_vol_1)
        & (prepared["close"] > prepared["open"])
        & (prepared["volume"] > MA(prepared["volume"], 40))
    )
    expected_plry_count = (COUNT(plry, 14) >= 2) | (COUNT(plry, 57) >= 3)
    pd.testing.assert_series_equal(result["PLRY_CNT"], expected_plry_count, check_names=False)

    common = (
        result["PLRY_CNT"]
        & result["YANGYIN_OK3"]
        & (result["J"] <= 13)
        & (result["MV"] >= 50)
        & result["GOOD28"]
        & result["MAX14_OK"]
    )
    expected_a1 = (
        common & result["YANGYIN_OK1"] & result["THREE_SUM_OK"]
    ) | (
        common & result["YANGYIN_OK2"]
    )
    expected_signal = (
        (result["HMSHORTWL"] >= result["HMLONGYL"] * 0.995)
        & (result["close"] >= result["HMLONGYL"] * 0.995)
        & expected_a1
    )
    pd.testing.assert_series_equal(result["A1"], expected_a1, check_names=False)
    pd.testing.assert_series_equal(result["B1_V24261_SIGNAL"], expected_signal, check_names=False)


def test_b1_v24261_builds_expected_signal_category():
    strategy = B1V24261Strategy()
    frame = pd.DataFrame([{
        "date": pd.Timestamp("2026-06-05"),
        "open": 9.8,
        "close": 10.0,
        "volume": 100_000,
        "J": 8.5,
        "MV": 80.0,
        "B1_V24261_SIGNAL": True,
        "PLRY_CNT": True,
        "YANGYIN_OK1": True,
        "YANGYIN_OK2": False,
        "YANGYIN_OK3": True,
        "GOOD28": True,
        "THREE_SUM_OK": True,
        "MAX14_OK": True,
        "VOL_YANG1": 160,
        "VOL_YIN1": 80,
        "VOL_YANG2": 220,
        "VOL_YIN2": 100,
        "VOL_YANG3": 240,
        "VOL_YIN3": 160,
        "HMSHORTWL": 9.9,
        "HMLONGYL": 9.8,
    }])

    signals = strategy.select_stocks(frame, "测试股份")

    assert len(signals) == 1
    assert signals[0]["category"] == "b1_v24261"
    assert signals[0]["market_cap"] == 80.0
    assert "57日阳量优势" in signals[0]["reasons"]


def test_b1_v24261_is_auto_registered():
    registry = StrategyRegistry(ROOT / "config" / "strategy_params.yaml")
    with contextlib.redirect_stdout(io.StringIO()):
        registry.auto_register_from_directory(ROOT / "strategy")

    strategy = registry.get_strategy("B1V24261Strategy")
    assert strategy is not None
    assert strategy.params["YANGYIN_RATIO_28"] == 1.6
    assert strategy.params["B1_TREND_TOLERANCE"] == 0.995
