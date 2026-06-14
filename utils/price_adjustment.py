"""Shared guards for mixed-adjustment OHLCV price series."""

from __future__ import annotations

from typing import Iterable

import numpy as np
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
    list_date=None,
    board: str | None = None,
) -> list[dict]:
    """Find likely mixed-qfq discontinuities in an ascending analysis series."""
    prices = _prepare_prices(df)
    if len(prices) < 2:
        return []

    # 向量化检测 gap
    close_arr = prices["close"].values.astype(float)
    open_arr = prices["open"].values.astype(float)
    date_arr = prices["date"].values
    
    prev_close = close_arr[:-1]
    curr_open = open_arr[1:]
    curr_close = close_arr[1:]
    
    # 避免除以零
    valid_mask = (prev_close > 0) & (curr_open > 0) & (curr_close > 0)
    
    overnight_gap = np.full(len(prev_close), 0.0)
    close_gap = np.full(len(prev_close), 0.0)
    factor = np.full(len(prev_close), 0.0)
    
    overnight_gap[valid_mask] = curr_open[valid_mask] / prev_close[valid_mask] - 1
    close_gap[valid_mask] = curr_close[valid_mask] / prev_close[valid_mask] - 1
    factor[valid_mask] = curr_open[valid_mask] / prev_close[valid_mask]
    
    # 检测满足条件的 gap
    is_gap = (
        valid_mask &
        (np.abs(overnight_gap) >= threshold) &
        (np.abs(close_gap) >= threshold) &
        (factor > 0) &
        (factor <= 5)
    )

    if list_date:
        listed_on = pd.to_datetime(list_date, errors="coerce")
        if pd.notna(listed_on):
            listed_rows = prices.index[prices["date"] >= listed_on].tolist()
            exemption_count = 5 if board in {"chinext", "star"} else 1
            exempt_rows = set(listed_rows[:exemption_count])
            for row_index in exempt_rows:
                if row_index > 0:
                    is_gap[row_index - 1] = False
    
    gap_indices = np.where(is_gap)[0]
    
    gaps = []
    for idx in gap_indices:
        actual_idx = idx + 1  # 因为 prev_close 是从 index 0 开始的
        gaps.append({
            "stock_code": stock_code,
            "date": pd.Timestamp(date_arr[actual_idx]).strftime("%Y-%m-%d"),
            "previous_date": pd.Timestamp(date_arr[idx]).strftime("%Y-%m-%d"),
            "gap_pct": round(overnight_gap[idx] * 100, 4),
            "close_gap_pct": round(close_gap[idx] * 100, 4),
            "factor": round(factor[idx], 8),
            "previous_close": round(float(prev_close[idx]), 4),
            "current_open": round(float(curr_open[idx]), 4),
            "current_close": round(float(curr_close[idx]), 4),
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

    # 向量化检测 gap
    close_arr = result["close"].values.astype(float)
    open_arr = result["open"].values.astype(float)
    length = len(result)
    
    repairs = []
    
    if length < 2:
        result.attrs["adjustment_repairs"] = repairs
        return result.sort_values("date", ascending=False).reset_index(drop=True), repairs
    
    prev_close = close_arr[:-1]
    curr_open = open_arr[1:]
    curr_close = close_arr[1:]
    
    # 避免除以零
    valid_mask = (prev_close > 0) & (curr_open > 0) & (curr_close > 0)
    
    overnight_gap = np.full(len(prev_close), 0.0)
    close_gap = np.full(len(prev_close), 0.0)
    factor = np.full(len(prev_close), 0.0)
    
    overnight_gap[valid_mask] = curr_open[valid_mask] / prev_close[valid_mask] - 1
    close_gap[valid_mask] = curr_close[valid_mask] / prev_close[valid_mask] - 1
    factor[valid_mask] = curr_open[valid_mask] / prev_close[valid_mask]
    
    # 检测满足条件的 gap
    is_gap = (
        valid_mask &
        (np.abs(overnight_gap) >= threshold) &
        (np.abs(close_gap) >= threshold) &
        (factor > 0) &
        (factor <= 5)
    )
    
    gap_indices = np.where(is_gap)[0]
    
    # 只对检测到的 gap 执行修复（绝大多数股票 0 个 gap）
    available_columns = [column for column in PRICE_COLUMNS if column in result.columns]
    
    for idx in gap_indices:
        actual_idx = idx + 1  # 因为 prev_close 是从 index 0 开始的
        factor_val = factor[idx]
        
        # 修复：将 gap 之前的所有价格乘以 factor
        result.loc[:actual_idx - 1, available_columns] = (
            result.loc[:actual_idx - 1, available_columns] * factor_val
        )
        
        repairs.append({
            "date": pd.Timestamp(result.at[actual_idx, "date"]).strftime("%Y-%m-%d"),
            "gap_pct": round(overnight_gap[idx] * 100, 4),
            "factor": round(factor_val, 8),
            "previous_close_before_repair": round(float(prev_close[idx]), 4),
            "current_open": round(float(curr_open[idx]), 4),
        })

    result.attrs["adjustment_repairs"] = repairs
    return result.sort_values("date", ascending=False).reset_index(drop=True), repairs
