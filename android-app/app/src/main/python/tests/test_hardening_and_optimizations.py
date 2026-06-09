from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from strategy.b1_min_j_complex import B1MinJComplexStrategy
from strategy.b1_v242p import calculate_b1_v242p_indicators
from strategy.pattern_library import B1PatternLibrary
from utils.csv_manager import CSVManager
from utils.technical import KDJ, normalize_price_frame, prepare_selection_features


def _price_frame(rows=180):
    rng = np.random.default_rng(11)
    close = 20 + rng.normal(0, 0.6, rows).cumsum()
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=rows, freq="B"),
        "open": close + rng.normal(0, 0.2, rows),
        "high": close + rng.uniform(0.3, 0.9, rows),
        "low": close - rng.uniform(0.3, 0.9, rows),
        "close": close,
        "volume": rng.integers(1000, 100000, rows),
        "amount": rng.integers(100000, 1000000, rows),
        "turnover": rng.random(rows),
        "market_cap": np.full(rows, 8_000_000_000.0),
    })


def test_price_frame_normalization_sorts_newest_first():
    frame = _price_frame(20).sample(frac=1, random_state=2).reset_index(drop=True)
    normalized = normalize_price_frame(frame)

    assert normalized["date"].is_monotonic_decreasing
    prepared = prepare_selection_features(frame)
    assert prepared["date"].is_monotonic_decreasing
    assert {"K", "D", "J", "short_term_trend", "bull_bear_line"}.issubset(prepared.columns)


def test_kdj_matches_sorted_descending_for_shuffled_input():
    frame = _price_frame(80)
    shuffled = frame.sample(frac=1, random_state=4).reset_index(drop=True)
    expected = KDJ(frame.sort_values("date", ascending=False).reset_index(drop=True))
    actual = KDJ(shuffled)

    pd.testing.assert_frame_equal(actual.reset_index(drop=True), expected.reset_index(drop=True))


def test_b1_min_j_complex_reuses_b1p_indicators_without_changing_columns():
    prepared = prepare_selection_features(_price_frame())
    strategy = B1MinJComplexStrategy()

    result = strategy.calculate_indicators(prepared)
    baseline = calculate_b1_v242p_indicators(prepared, strategy.params)

    assert "MIN_J" in result.columns
    assert "B1_MIN_J_COMPLEX_SIGNAL" in result.columns
    shared_columns = ["VOL_YANG1", "VOL_YIN1", "HMSHORTWL", "HMLONGYL", "PLRY_CNT"]
    for column in shared_columns:
        pd.testing.assert_series_equal(result[column], baseline[column])


def test_pattern_match_ignores_future_cases(monkeypatch):
    library = object.__new__(B1PatternLibrary)
    library.cases = {
        "future": {
            "meta": {"name": "Future", "breakout_date": "2026-01-01", "code": "000001", "tags": []},
            "features": {"price_shape": {"normalized_curve": [0, 1]}},
        },
        "past": {
            "meta": {"name": "Past", "breakout_date": "2025-01-01", "code": "000002", "tags": []},
            "features": {"price_shape": {"normalized_curve": [0, 1]}},
        },
    }
    library.extractor = type("Extractor", (), {"extract": lambda self, df, lookback_days=None: {"price_shape": {"normalized_curve": [0, 1]}}})()
    library.matcher = type("Matcher", (), {"match": lambda self, candidate, case: {"total_score": 88, "breakdown": {}}})()

    result = library.find_best_match("000001", _price_frame(20), as_of_date="2025-06-01")

    assert [item["case_id"] for item in result["all_matches"]] == ["past"]


def test_csv_update_stock_is_locked_and_keeps_descending_order(tmp_path):
    manager = CSVManager(tmp_path)
    base = _price_frame(5)
    manager.write_stock("000001", base)

    def update(offset):
        row = _price_frame(1)
        row.loc[0, "date"] = pd.Timestamp("2025-02-01") + pd.Timedelta(days=offset)
        row.loc[0, "close"] = 30 + offset
        manager.update_stock("000001", row)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(update, range(8)))

    result = manager.read_stock("000001")
    assert result["date"].is_monotonic_decreasing
    assert result["date"].nunique() == len(result)
