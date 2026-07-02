from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import main
from strategy.bowl_rebound import BowlReboundStrategy
from strategy.b1_min_j_complex import B1MinJComplexStrategy
from strategy.b1_v242b import B1V242BStrategy
from strategy.b1_v242p import calculate_b1_v242p_indicators
from strategy.pattern_library import B1PatternLibrary
import utils.stock_exporter as stock_exporter
from utils.csv_manager import CSVManager
from utils.technical import (
    KDJ,
    _backset,
    _bars_last,
    _bars_last_count,
    _ref_by_variable_period,
    normalize_price_frame,
    prepare_selection_features,
)


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


def test_technical_sequence_helpers_match_reference_loops():
    cond = pd.Series([False, True, True, None, False, True, False, True, True], index=list("abcdefghi"))
    counts = pd.Series([0, 1, 3, 2, -1, 1, np.nan, 2, 1], index=cond.index)
    series = pd.Series([10, 11, 12, 13, 14, 15, 16, 17, 18], index=cond.index, dtype=float)
    periods = pd.Series([0, 1, 2, 9, -1, np.nan, 3, 4, 8], index=cond.index)

    expected_count = pd.Series([0, 1, 2, 0, 0, 1, 0, 1, 2], index=cond.index, dtype=int)
    expected_bars_last = pd.Series([-1, 0, 0, 1, 2, 0, 1, 0, 0], index=cond.index, dtype=int)
    expected_backset = pd.Series([True, True, True, False, False, True, True, True, True], index=cond.index, dtype=bool)
    expected_ref = pd.Series([10.0, 10.0, 10.0, np.nan, 15.0, 15.0, 13.0, 13.0, 10.0], index=cond.index)

    pd.testing.assert_series_equal(_bars_last_count(cond), expected_count)
    pd.testing.assert_series_equal(_bars_last(cond), expected_bars_last)
    pd.testing.assert_series_equal(_backset(cond, counts), expected_backset)
    pd.testing.assert_series_equal(_ref_by_variable_period(series, periods), expected_ref)


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


def test_bowl_rebound_signal_does_not_require_market_cap_column():
    strategy = BowlReboundStrategy(params={"M": 3})
    frame = pd.DataFrame([
        {
            "date": "2026-06-03",
            "open": 10.0,
            "high": 11.0,
            "low": 9.8,
            "close": 10.5,
            "volume": 5000,
            "J": 10.0,
            "trend_above": True,
            "j_low": True,
            "fall_in_bowl": True,
            "near_duokong": False,
            "near_short_trend": False,
            "key_candle": True,
            "vol_ratio": 5.0,
            "short_term_trend": 10.8,
            "bull_bear_line": 10.2,
        },
        {
            "date": "2026-06-02",
            "open": 9.0,
            "high": 10.0,
            "low": 8.8,
            "close": 9.5,
            "volume": 1000,
            "J": 15.0,
            "trend_above": True,
            "j_low": True,
            "fall_in_bowl": False,
            "near_duokong": False,
            "near_short_trend": False,
            "key_candle": False,
            "vol_ratio": 1.0,
            "short_term_trend": 10.0,
            "bull_bear_line": 9.5,
        },
    ])

    signals = strategy.select_stocks(frame, stock_name="测试")

    assert signals
    assert signals[0]["market_cap"] == 0


def test_b1_v242b_fd15_volume_ratio_uses_configurable_param():
    frame = _price_frame(80).sort_values("date", ascending=False).reset_index(drop=True)
    frame["ref_close_1"] = frame["close"].shift(-1).fillna(frame["close"])
    frame["ref_vol_1"] = 100.0
    frame.loc[0, "open"] = 10.0
    frame.loc[0, "close"] = 9.0
    frame.loc[0, "volume"] = 150.0
    frame.loc[0, "ref_close_1"] = 10.0
    frame.loc[0, "ref_vol_1"] = 100.0
    frame["K"] = 10.0
    frame["D"] = 10.0
    frame["J"] = 10.0

    result = B1V242BStrategy(params={"FD15_VOL_RATIO": 2.0}).calculate_indicators(frame)

    assert bool(result.loc[0, "FD15"]) is False


def test_stock_exporter_downloads_dir_uses_current_user_home():
    source = Path(stock_exporter.__file__).read_text(encoding="utf-8")
    assert 'Path("/Users/chenxingyu/Downloads")' not in source
    assert stock_exporter.DOWNLOADS_DIR == Path.home() / "Downloads"


def test_main_pattern_config_fallback_only_catches_import_error():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "except ImportError:" in source
    assert "except:\n        default_min_similarity" not in source
