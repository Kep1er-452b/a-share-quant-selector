from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

import numpy as np
import pandas as pd

from utils.akshare_fetcher import AKShareFetcher
from utils.price_adjustment import detect_adjustment_gaps, repair_adjustment_gaps
from utils.technical import KDJ, SMA


def _reference_sma(series, n, m):
    if len(series) == 0:
        return pd.Series(index=series.index, dtype=float)

    reversed_series = series.iloc[::-1].reset_index(drop=True)
    result_reversed = pd.Series(index=reversed_series.index, dtype=float)
    result_reversed.iloc[0] = reversed_series.iloc[0]
    for index in range(1, len(reversed_series)):
        result_reversed.iloc[index] = (
            reversed_series.iloc[index] * m + result_reversed.iloc[index - 1] * (n - m)
        ) / n
    return result_reversed.iloc[::-1].reset_index(drop=True).set_axis(series.index)


def _reference_kdj(df, n=9, m1=3, m2=3):
    is_descending = df["date"].iloc[0] > df["date"].iloc[-1]
    df_calc = df.iloc[::-1].copy().reset_index(drop=True) if is_descending else df.copy().reset_index(drop=True)

    low_min = df_calc["low"].rolling(window=n, min_periods=1).min()
    high_max = df_calc["high"].rolling(window=n, min_periods=1).max()
    range_val = high_max - low_min
    rsv = pd.Series(index=df_calc.index, dtype=float)

    for index in range(len(df_calc)):
        if index < n - 1 or range_val.iloc[index] == 0:
            rsv.iloc[index] = 50.0
        else:
            rsv.iloc[index] = (
                (df_calc["close"].iloc[index] - low_min.iloc[index]) / range_val.iloc[index] * 100
            )

    k = pd.Series(index=df_calc.index, dtype=float)
    d = pd.Series(index=df_calc.index, dtype=float)
    k.iloc[0] = 50.0
    d.iloc[0] = 50.0
    for index in range(1, len(df_calc)):
        k.iloc[index] = (rsv.iloc[index] + k.iloc[index - 1] * (m1 - 1)) / m1
        d.iloc[index] = (k.iloc[index] + d.iloc[index - 1] * (m2 - 1)) / m2

    result = pd.DataFrame({"K": k, "D": d, "J": 3 * k - 2 * d})
    if is_descending:
        result = result.iloc[::-1].reset_index(drop=True)
    result.index = df.index
    return result


def _reference_gaps(df, threshold=0.26, stock_code=None):
    prices = df.copy()
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    for column in ["open", "high", "low", "close"]:
        if column in prices.columns:
            prices[column] = pd.to_numeric(prices[column], errors="coerce").astype(float)
    prices = prices.dropna(subset=["date", "open", "close"])
    prices = prices.drop_duplicates(subset=["date"], keep="last")
    prices = prices.sort_values("date", ascending=True).reset_index(drop=True)

    gaps = []
    for index in range(1, len(prices)):
        previous_close = float(prices.at[index - 1, "close"])
        current_open = float(prices.at[index, "open"])
        current_close = float(prices.at[index, "close"])
        if previous_close <= 0 or current_open <= 0 or current_close <= 0:
            continue
        overnight_gap = current_open / previous_close - 1
        close_gap = current_close / previous_close - 1
        if abs(overnight_gap) < threshold or abs(close_gap) < threshold:
            continue
        factor = current_open / previous_close
        if factor <= 0 or factor > 5:
            continue
        gaps.append({
            "stock_code": stock_code,
            "date": pd.Timestamp(prices.at[index, "date"]).strftime("%Y-%m-%d"),
            "previous_date": pd.Timestamp(prices.at[index - 1, "date"]).strftime("%Y-%m-%d"),
            "gap_pct": round(overnight_gap * 100, 4),
            "close_gap_pct": round(close_gap * 100, 4),
            "factor": round(factor, 8),
            "previous_close": round(previous_close, 4),
            "current_open": round(current_open, 4),
            "current_close": round(current_close, 4),
            "threshold": threshold,
        })
    return gaps


def _price_frame(rows=80):
    rng = np.random.default_rng(7)
    close = 20 + rng.normal(0, 0.8, rows).cumsum()
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=rows, freq="B"),
        "open": close + rng.normal(0, 0.2, rows),
        "high": close + rng.uniform(0.4, 1.2, rows),
        "low": close - rng.uniform(0.4, 1.2, rows),
        "close": close,
        "volume": rng.integers(1000, 100000, rows),
    })


def test_vectorized_sma_and_kdj_match_loop_reference():
    for frame in [_price_frame(), _price_frame().iloc[::-1].reset_index(drop=True)]:
        pd.testing.assert_series_equal(SMA(frame["close"], 9, 3), _reference_sma(frame["close"], 9, 3))
        pd.testing.assert_frame_equal(KDJ(frame), _reference_kdj(frame))


def test_vectorized_adjustment_detection_matches_loop_reference():
    frame = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=8, freq="B"),
        "open": [10, 10.1, 5.0, 5.1, 5.2, 15.0, 15.1, 15.3],
        "high": [10.5, 10.6, 5.3, 5.4, 5.5, 15.5, 15.6, 15.7],
        "low": [9.8, 9.9, 4.8, 4.9, 5.0, 14.7, 14.9, 15.0],
        "close": [10.0, 10.2, 5.1, 5.0, 5.1, 15.2, 15.0, 15.4],
    })

    assert detect_adjustment_gaps(frame, stock_code="TEST") == _reference_gaps(frame, stock_code="TEST")

    repaired, repairs = repair_adjustment_gaps(frame)
    assert len(repairs) == 2
    assert repaired.iloc[0]["date"] > repaired.iloc[-1]["date"]


def test_adjustment_detection_exempts_actual_ipo_trading_rows():
    frame = pd.DataFrame({
        "date": pd.to_datetime([
            "2024-09-30",
            "2024-10-08",
            "2024-10-09",
            "2024-10-10",
            "2024-10-11",
            "2024-10-14",
        ]),
        "open": [10, 14, 20, 28, 40, 20],
        "high": [11, 15, 21, 29, 41, 21],
        "low": [9, 13, 19, 27, 39, 19],
        "close": [10, 14, 20, 28, 40, 20],
    })

    gaps = detect_adjustment_gaps(
        frame,
        stock_code="301001",
        list_date="20240930",
        board="chinext",
    )

    assert [gap["date"] for gap in gaps] == ["2024-10-14"]


def test_main_board_only_exempts_listing_day():
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2024-09-30", "2024-10-08", "2024-10-09"]),
        "open": [10, 14, 20],
        "high": [11, 15, 21],
        "low": [9, 13, 19],
        "close": [10, 14, 20],
    })

    gaps = detect_adjustment_gaps(
        frame,
        stock_code="601001",
        list_date="20240930",
        board="main",
    )

    assert [gap["date"] for gap in gaps] == ["2024-10-08", "2024-10-09"]


def test_provider_can_bootstrap_stock_names_from_shared_cache(tmp_path):
    tushare_dir = tmp_path / "providers" / "tushare"
    tushare_dir.mkdir(parents=True)
    (tushare_dir / "stock_names.json").write_text(
        json.dumps({"000001": "Ping An", "600519": "Moutai", "300750": "CATL"}),
        encoding="utf-8",
    )

    provider = AKShareFetcher(data_dir=str(tmp_path / "providers" / "akshare")).configure_storage(tmp_path, "akshare")
    names, source = provider._load_shared_stock_names(min_count=3)

    assert source == tushare_dir / "stock_names.json"
    assert names == {"000001": "Ping An", "600519": "Moutai", "300750": "CATL"}


def test_provider_prefers_neutral_stock_names_over_other_provider_cache(tmp_path):
    tushare_dir = tmp_path / "providers" / "tushare"
    tushare_dir.mkdir(parents=True)
    (tushare_dir / "stock_names.json").write_text(
        json.dumps({"000001": "Tushare A", "600519": "Tushare B", "300750": "Tushare C"}),
        encoding="utf-8",
    )
    neutral_path = tmp_path / "stock_names.json"
    neutral_path.write_text(
        json.dumps({"000001": "Neutral A", "600519": "Neutral B", "300750": "Neutral C"}),
        encoding="utf-8",
    )

    provider = AKShareFetcher(data_dir=str(tmp_path / "providers" / "akshare")).configure_storage(tmp_path, "akshare")
    names, source = provider._load_shared_stock_names(min_count=3)

    assert source == neutral_path
    assert names == {"000001": "Neutral A", "600519": "Neutral B", "300750": "Neutral C"}
