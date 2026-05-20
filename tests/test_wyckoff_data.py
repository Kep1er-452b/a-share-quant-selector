from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from wyckoff_ai.data import WyckoffDataError, normalize_ohlcv


def _frame(rows=260):
    dates = pd.date_range("2025-01-01", periods=rows, freq="D")
    return pd.DataFrame({
        "日期": list(reversed(dates.strftime("%Y-%m-%d"))),
        "开盘": [10.0] * rows,
        "最高": [11.0] * rows,
        "最低": [9.0] * rows,
        "收盘": [10.5] * rows,
        "成交量": [1000] * rows,
    })


def test_normalize_ohlcv_ascending_and_indicators():
    df = normalize_ohlcv(_frame())
    assert df.iloc[0]["date"] < df.iloc[-1]["date"]
    assert {"ma50", "ma200", "vol_ma20", "vol_ratio", "pct_change"}.issubset(df.columns)
    assert len(df) == 260


def test_normalize_ohlcv_rejects_short_data():
    with pytest.raises(WyckoffDataError):
        normalize_ohlcv(_frame(50))
