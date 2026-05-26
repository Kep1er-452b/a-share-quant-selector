"""Load and normalize local OHLCV CSV data for Wyckoff analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


COLUMN_ALIASES = {
    "date": ["date", "日期", "trade_date", "datetime", "time", "交易日期", "时间"],
    "open": ["open", "开盘", "开盘价"],
    "high": ["high", "最高", "最高价"],
    "low": ["low", "最低", "最低价"],
    "close": ["close", "收盘", "收盘价", "adj_close", "复权收盘"],
    "volume": ["volume", "vol", "成交量"],
}

MODEL_ROWS = 500
ANALYSIS_ROWS = 500
MIN_REQUIRED_ROWS = 250
ADJUSTMENT_GAP_THRESHOLD = 0.26
PRICE_COLUMNS = ["open", "high", "low", "close"]


class WyckoffDataError(ValueError):
    """Raised when a CSV cannot support Wyckoff analysis."""


def _find_column(columns: Iterable[str], aliases: list[str]) -> str | None:
    normalized = {str(column).strip().lower(): column for column in columns}
    for alias in aliases:
        match = normalized.get(alias.lower())
        if match is not None:
            return match
    return None


def _repair_adjustment_gaps(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Repair mixed-adjustment price jumps for analysis without changing source CSV."""
    result = df.copy()
    repairs = []
    for index in range(1, len(result)):
        previous_close = float(result.at[index - 1, "close"])
        current_open = float(result.at[index, "open"])
        current_close = float(result.at[index, "close"])
        if previous_close <= 0 or current_open <= 0 or current_close <= 0:
            continue
        overnight_gap = current_open / previous_close - 1
        close_gap = current_close / previous_close - 1
        if abs(overnight_gap) < ADJUSTMENT_GAP_THRESHOLD or abs(close_gap) < ADJUSTMENT_GAP_THRESHOLD:
            continue
        factor = current_open / previous_close
        if factor <= 0 or factor > 5:
            continue
        result.loc[: index - 1, PRICE_COLUMNS] = result.loc[: index - 1, PRICE_COLUMNS] * factor
        repairs.append({
            "date": pd.Timestamp(result.at[index, "date"]).strftime("%Y-%m-%d"),
            "gap_pct": round(overnight_gap * 100, 4),
            "factor": round(factor, 8),
            "previous_close_before_repair": round(previous_close, 4),
            "current_open": round(current_open, 4),
        })
    return result, repairs


def normalize_ohlcv(df: pd.DataFrame, min_rows: int = MIN_REQUIRED_ROWS) -> pd.DataFrame:
    """Return an ascending-date dataframe with standard OHLCV columns and indicators."""
    if df is None or df.empty:
        raise WyckoffDataError("CSV 数据为空，无法进行威科夫分析")

    rename = {}
    for target, aliases in COLUMN_ALIASES.items():
        source = _find_column(df.columns, aliases)
        if source is not None:
            rename[source] = target

    result = df.rename(columns=rename).copy()
    missing = {"date", "close"} - set(result.columns)
    if missing:
        raise WyckoffDataError(f"CSV 缺少必要字段: {', '.join(sorted(missing))}")

    for column in ["open", "high", "low"]:
        if column not in result.columns:
            result[column] = result["close"]
    if "volume" not in result.columns:
        result["volume"] = np.nan

    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    result = result.dropna(subset=["date", "open", "high", "low", "close"])
    result = result.drop_duplicates(subset=["date"], keep="last")
    result = result.sort_values("date", ascending=True).reset_index(drop=True)
    if len(result) < min_rows:
        raise WyckoffDataError(f"有效行情不足 {min_rows} 条，当前只有 {len(result)} 条")

    result, adjustment_repairs = _repair_adjustment_gaps(result)
    result["ma50"] = result["close"].rolling(50, min_periods=1).mean()
    result["ma200"] = result["close"].rolling(200, min_periods=1).mean()
    result["vol_ma20"] = result["volume"].rolling(20, min_periods=1).mean()
    result["vol_ratio"] = result["volume"] / result["vol_ma20"].replace(0, np.nan)
    result["vol_ratio"] = result["vol_ratio"].replace([np.inf, -np.inf], np.nan).fillna(1.0)
    result["pct_change"] = result["close"].pct_change().fillna(0.0) * 100
    result.attrs["adjustment_repairs"] = adjustment_repairs
    return result


def load_stock_csv(path: str | Path, min_rows: int = MIN_REQUIRED_ROWS) -> pd.DataFrame:
    """Read a local CSV file and return normalized Wyckoff-ready data."""
    csv_path = Path(path)
    if not csv_path.exists():
        raise WyckoffDataError(f"CSV 文件不存在: {csv_path}")
    try:
        raw = pd.read_csv(csv_path)
    except UnicodeDecodeError:
        raw = pd.read_csv(csv_path, encoding="gb18030")
    return normalize_ohlcv(raw, min_rows=min_rows)


def model_frame(df: pd.DataFrame, rows: int = MODEL_ROWS) -> pd.DataFrame:
    """Return the recent rows sent to the model."""
    result = df.tail(rows).copy().reset_index(drop=True)
    result.attrs.update(getattr(df, "attrs", {}) or {})
    return result


def compact_records(df: pd.DataFrame) -> list[dict]:
    """Serialize model rows with stable rounding to keep prompts compact."""
    fields = ["date", "open", "high", "low", "close", "volume", "ma50", "ma200", "vol_ratio", "pct_change"]
    recent = df[fields].copy()
    recent["date"] = recent["date"].dt.strftime("%Y-%m-%d")
    for column in ["open", "high", "low", "close", "ma50", "ma200", "vol_ratio", "pct_change"]:
        recent[column] = recent[column].astype(float).round(4)
    recent["volume"] = recent["volume"].fillna(0).astype(float).round(2)
    return recent.to_dict("records")


def compact_csv(df: pd.DataFrame) -> str:
    """Serialize recent rows as compact CSV to avoid huge JSON prompt overhead."""
    fields = ["date", "open", "high", "low", "close", "volume", "ma50", "ma200", "vol_ratio", "pct_change"]
    recent = df[fields].copy()
    recent["date"] = recent["date"].dt.strftime("%Y-%m-%d")
    for column in ["open", "high", "low", "close", "ma50", "ma200", "vol_ratio", "pct_change"]:
        recent[column] = recent[column].astype(float).round(4)
    recent["volume"] = recent["volume"].fillna(0).astype(float).round(2)
    return recent.to_csv(index=False)
