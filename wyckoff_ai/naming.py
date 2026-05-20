"""Output naming helpers for Wyckoff analysis artifacts."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

try:
    from pypinyin import lazy_pinyin
except ImportError:  # pragma: no cover - dependency is declared, fallback remains usable.
    lazy_pinyin = None


SAFE_PART_PATTERN = re.compile(r"[^a-z0-9.-]+")


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
) -> dict:
    """Build standard output paths for one Wyckoff run."""
    root = Path(output_dir)
    slug = to_pinyin_slug(stock_name, symbol)
    stem = f"{slug}-{data_date}-wyckoff"
    return {
        "analysis_path": str(root / "json" / f"{stem}-analysis.json"),
        "chart_path": str(root / "charts" / f"{stem}-chart.png"),
        "debug_path": str(root / "debug" / f"{stem}-debug.txt"),
    }
