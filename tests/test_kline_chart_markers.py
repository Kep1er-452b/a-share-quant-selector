from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matplotlib.axes import Axes
import pandas as pd

from utils.kline_chart import _normalize_key_candle_dates as normalize_standard
from utils.kline_chart_fast import generate_kline_chart_fast
from utils.kline_chart_fast import _normalize_key_candle_dates as normalize_fast


def test_fast_kline_chart_marks_key_candle_dates(monkeypatch, tmp_path):
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    frame = pd.DataFrame({
        "date": dates,
        "open": [10, 10.2, 10.1, 10.4, 10.3],
        "high": [10.5, 10.8, 10.7, 10.9, 10.6],
        "low": [9.8, 10.0, 9.9, 10.1, 10.0],
        "close": [10.3, 10.1, 10.5, 10.2, 10.4],
        "volume": [1000, 1100, 1200, 900, 1300],
        "short_term_trend": [10.2, 10.3, 10.35, 10.38, 10.4],
        "bull_bear_line": [10.0, 10.1, 10.15, 10.2, 10.25],
    })
    scatter_calls = []
    original_scatter = Axes.scatter

    def spy_scatter(self, *args, **kwargs):
        scatter_calls.append((args, kwargs))
        return original_scatter(self, *args, **kwargs)

    monkeypatch.setattr(Axes, "scatter", spy_scatter)

    output = generate_kline_chart_fast(
        "000001",
        "测试",
        frame,
        "bowl_center",
        {"M": 5},
        key_candle_dates=["2026-01-03"],
        output_dir=str(tmp_path),
    )

    assert Path(output).exists()
    assert scatter_calls


def test_standard_and_fast_kline_charts_share_key_date_normalizer():
    assert normalize_standard is normalize_fast
    assert normalize_standard(["2026/01/03", pd.Timestamp("2026-01-04"), None]) == {
        "2026-01-03",
        "2026-01-04",
    }
