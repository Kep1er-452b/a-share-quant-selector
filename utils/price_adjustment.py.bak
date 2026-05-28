"""Shared guards for mixed-adjustment OHLCV price series."""

from __future__ import annotations

from typing import Iterable

import pandas as pd


PRICE_COLUMNS = ["open", "high", "low", "close"]
DEFAULT_GAP_THRESHOLD = 0.26


def _prepare_prices(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    required = {"date", "open", "close"}
    if not required.issubset(df.columns):
        return pd.DataFrame()
    result = df.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in PRICE_COLUMNS:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce").astype(float)
    result = result.dropna(subset=["date", "open", "close"])
    result = result.drop_duplicates(subset=["date"], keep="last")
    return result.sort_values("date", ascending=True).reset_index(drop=True)


def detect_adjustment_gaps(
    df: pd.DataFrame,
    threshold: float = DEFAULT_GAP_THRESHOLD,
    stock_code: str | None = None,
) -> list[dict]:
    """Find likely mixed-qfq discontinuities in an ascending analysis series."""
    prices = _prepare_prices(df)
    if len(prices) < 2:
        return []

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


def repair_adjustment_gaps(
    df: pd.DataFrame,
    threshold: float = DEFAULT_GAP_THRESHOLD,
) -> tuple[pd.DataFrame, list[dict]]:
    """Return an adjusted analysis view without mutating the source CSV."""
    result = _prepare_prices(df)
    if result.empty:
        return df.copy() if df is not None else pd.DataFrame(), []

    for column in PRICE_COLUMNS:
        if column not in result.columns and column in df.columns:
            result[column] = pd.to_numeric(df[column], errors="coerce")

    repairs = []
    for index in range(1, len(result)):
        previous_close = float(result.at[index - 1, "close"])
        current_open = float(result.at[index, "open"])
        current_close = float(result.at[index, "close"])
        if previous_close <= 0 or current_open <= 0 or current_close <= 0:
            continue
        overnight_gap = current_open / previous_close - 1
        close_gap = current_close / previous_close - 1
        if abs(overnight_gap) < threshold or abs(close_gap) < threshold:
            continue
        factor = current_open / previous_close
        if factor <= 0 or factor > 5:
            continue
        available_columns: Iterable[str] = [column for column in PRICE_COLUMNS if column in result.columns]
        result.loc[: index - 1, available_columns] = result.loc[: index - 1, available_columns] * factor
        repairs.append({
            "date": pd.Timestamp(result.at[index, "date"]).strftime("%Y-%m-%d"),
            "gap_pct": round(overnight_gap * 100, 4),
            "factor": round(factor, 8),
            "previous_close_before_repair": round(previous_close, 4),
            "current_open": round(current_open, 4),
        })

    result.attrs["adjustment_repairs"] = repairs
    return result.sort_values("date", ascending=False).reset_index(drop=True), repairs
