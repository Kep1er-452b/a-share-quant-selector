"""Output naming helpers for Wyckoff analysis artifacts."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    from pypinyin import lazy_pinyin
except ImportError:  # pragma: no cover - dependency is declared, fallback remains usable.
    lazy_pinyin = None


SAFE_PART_PATTERN = re.compile(r"[^a-z0-9.-]+")
SAFE_STOCK_FOLDER_PATTERN = re.compile(r'[\\/:*?"<>|\s]+')


def safe_filename_part(text: str) -> str:
    """Keep a filename part portable across macOS, Windows, Linux, and Docker."""
    value = str(text or "").strip().lower()
    value = value.replace("_", "-")
    value = SAFE_PART_PATTERN.sub("-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-.")
    return value or "unknown"


def to_pinyin_slug(stock_name: str | None, symbol: str) -> str:
    """Convert Chinese stock names to a safe pinyin slug, falling back to symbol."""
    cleaned_name = str(stock_name or "").strip()
    if cleaned_name and lazy_pinyin is not None:
        converted = "".join(lazy_pinyin(cleaned_name, errors="ignore"))
        slug = safe_filename_part(converted)
        if slug and slug != "unknown":
            return slug
    return safe_filename_part(symbol)


def safe_stock_folder_part(text: str) -> str:
    """Keep Chinese stock names readable while removing filesystem separators."""
    value = str(text or "").strip()
    value = SAFE_STOCK_FOLDER_PATTERN.sub("-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-.")
    return value or "未知股票"


def stock_output_folder_name(stock_name: str | None, symbol: str) -> str:
    """Return the stock-level Wyckoff output folder name."""
    name = safe_stock_folder_part(stock_name or symbol)
    code = safe_filename_part(symbol)
    return f"{name}-{code}"


def run_timestamp_folder(value: datetime | str | None = None) -> str:
    """Return a stable timestamp folder for one analysis run."""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d-%H%M%S")
    if value:
        text = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d-%H%M%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text[:19], fmt).strftime("%Y%m%d-%H%M%S")
            except ValueError:
                pass
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def get_latest_data_date(df) -> str:
    """Return latest trading date from a dataframe as YYYY-MM-DD."""
    if df is None or df.empty or "date" not in df.columns:
        raise ValueError("无法从空数据中获取最后交易日")
    dates = pd.to_datetime(df["date"], errors="coerce").dropna()
    if dates.empty:
        raise ValueError("无法解析行情日期")
    return dates.max().strftime("%Y-%m-%d")


def build_wyckoff_output_paths(
    symbol: str,
    stock_name: str | None,
    data_date: str,
    output_dir: str | Path = "outputs/wyckoff",
    run_timestamp: datetime | str | None = None,
) -> dict:
    """Build standard output paths for one Wyckoff run."""
    root = Path(output_dir)
    slug = to_pinyin_slug(stock_name, symbol)
    stem = f"{slug}-{data_date}-wyckoff"
    run_dir = root / stock_output_folder_name(stock_name, symbol) / run_timestamp_folder(run_timestamp)
    return {
        "analysis_path": str(run_dir / "json" / f"{stem}-analysis.json"),
        "chart_path": str(run_dir / "charts" / f"{stem}-chart.png"),
        "debug_path": str(run_dir / "debug" / f"{stem}-debug.txt"),
        "run_dir": str(run_dir),
        "stock_dir": str(run_dir.parent),
    }
