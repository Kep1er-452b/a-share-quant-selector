"""Shared helpers for K-line chart rendering."""

from __future__ import annotations

import pandas as pd


def normalize_key_candle_dates(key_candle_dates: list | None) -> set[str]:
    result = set()
    for value in key_candle_dates or []:
        if isinstance(value, pd.Timestamp):
            result.add(value.strftime("%Y-%m-%d"))
            continue
        try:
            parsed = pd.to_datetime(value)
        except Exception:
            parsed = None
        if parsed is not None and not pd.isna(parsed):
            result.add(parsed.strftime("%Y-%m-%d"))
        else:
            text = str(value or "").strip()
            if text:
                result.add(text[:10])
    return result
