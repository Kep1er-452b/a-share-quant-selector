from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy.formula_strategy import FORMULA_STRATEGY_NAME
from utils.csv_manager import CSVManager
from utils.formula_engine import FormulaError, compile_formula, evaluate_formula
from utils.selection_worker import build_worker_context, process_selection_chunk
from utils.technical import MA, REF, prepare_selection_features


ROOT = Path(__file__).resolve().parents[1]


def _price_frame(rows=90):
    close = np.linspace(10, 30, rows)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=rows, freq="B"),
        "open": close - 0.2,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": np.linspace(1000, 5000, rows),
        "amount": np.linspace(100000, 500000, rows),
        "turnover": np.linspace(0.1, 1.0, rows),
        "market_cap": np.full(rows, 8_000_000_000.0),
    })


def test_formula_engine_matches_manual_series_logic():
    frame = prepare_selection_features(_price_frame().iloc[::-1].reset_index(drop=True))
    formula = "CLOSE > MA(CLOSE, 20) AND J < 100 AND VOL > REF(VOL, 1)"

    actual = evaluate_formula(formula, frame)
    expected = (
        (frame["close"] > MA(frame["close"], 20)) &
        (frame["J"] < 100) &
        (frame["volume"] > REF(frame["volume"], 1))
    ).fillna(False).astype(bool)

    pd.testing.assert_series_equal(actual, expected)


def test_formula_engine_rejects_unsafe_python_syntax():
    with pytest.raises(FormulaError):
        compile_formula("__import__('os').system('echo nope')")

    with pytest.raises(FormulaError):
        compile_formula("CLOSE[0] > 1")


def test_formula_strategy_can_run_as_runtime_selection_strategy(tmp_path):
    manager = CSVManager(tmp_path)
    manager.write_stock("000001", _price_frame())

    context = build_worker_context(
        str(tmp_path),
        [FORMULA_STRATEGY_NAME],
        str(ROOT / "config" / "strategy_params.yaml"),
        {
            FORMULA_STRATEGY_NAME: {
                "formula": "CLOSE > 0",
                "label": "测试公式",
            }
        },
    )

    result = process_selection_chunk([("000001", "测试股票")], "all", False, context)

    signals = result["results_by_strategy"][FORMULA_STRATEGY_NAME]
    assert len(signals) == 1
    assert signals[0]["code"] == "000001"
    assert signals[0]["signals"][0]["signal"] == "测试公式"
