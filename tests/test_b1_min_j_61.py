from pathlib import Path
import contextlib
import io
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from strategy.b1_min_j_61_complex import B1MinJ61ComplexStrategy
from strategy.b1_min_j_simple import B1MinJSimpleStrategy
from strategy.strategy_registry import StrategyRegistry
from utils.technical import prepare_selection_features, prepare_strategy_shared_features


ROOT = Path(__file__).resolve().parents[1]


def _price_frame(rows=220):
    rng = np.random.default_rng(610061)
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


def test_original_min_j_simple_keeps_its_zhixing_conditions():
    strategy = B1MinJSimpleStrategy()
    result = strategy.calculate_indicators(_price_frame())

    assert {"ZX_SHORT", "ZX_LONG", "COND_TREND", "COND_J", "B1_MIN_J_SIMPLE_SIGNAL"}.issubset(result.columns)
    expected = (result["ZX_SHORT"] > result["ZX_LONG"]) & (result["J"] < result["MIN_J"])
    pd.testing.assert_series_equal(result["B1_MIN_J_SIMPLE_SIGNAL"], expected, check_names=False)
    assert "A1" not in result.columns


def test_min_j_61_complex_uses_v24261_conditions_with_dynamic_j():
    strategy = B1MinJ61ComplexStrategy()
    prepared = prepare_selection_features(_price_frame())
    prepared = prepare_strategy_shared_features(prepared, ["B1MinJ61ComplexStrategy"])
    result = strategy.calculate_indicators(prepared)

    pd.testing.assert_series_equal(result["J_OK"], result["J"] <= result["MIN_J"], check_names=False)
    assert "YANGYIN_OK3" in result.columns
    common = (
        result["PLRY_CNT"]
        & result["YANGYIN_OK3"]
        & result["J_OK"]
        & result["MVOK"]
        & result["GOOD28"]
        & result["MAX14_OK"]
    )
    expected_a1 = (
        common & result["YANGYIN_OK1"] & result["THREE_SUM_OK"]
    ) | (
        common & result["YANGYIN_OK2"]
    )
    pd.testing.assert_series_equal(result["A1"], expected_a1, check_names=False)
    pd.testing.assert_series_equal(
        result["B1_MIN_J_61_COMPLEX_SIGNAL"],
        result["B1_V24261_SIGNAL"],
        check_names=False,
    )


def test_min_j_61_complex_is_registered_without_replacing_old_strategies():
    registry = StrategyRegistry(ROOT / "config" / "strategy_params.yaml")
    with contextlib.redirect_stdout(io.StringIO()):
        registry.auto_register_from_directory(ROOT / "strategy")

    assert registry.get_strategy("B1MinJSimpleStrategy") is not None
    assert registry.get_strategy("B1MinJComplexStrategy") is not None
    strategy = registry.get_strategy("B1MinJ61ComplexStrategy")
    assert strategy is not None
    assert strategy.params["YANGYIN_RATIO_57"] == 1.2
    assert strategy.params["B1_TREND_TOLERANCE"] == 0.995
