from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import wyckoff_ai.pipeline as pipeline_module
from wyckoff_ai.pipeline import WyckoffPipeline


class FakeClient:
    def __init__(self, api_key, **kwargs):
        self.api_key = api_key

    def analyze(self, messages):
        return {
            "mode": "unclear",
            "current_phase": "unclear",
            "summary_text": "结构证据不足，暂不强行归类。",
            "ranges": [],
            "phases": [],
            "events": [],
            "scenarios": ["等待更清楚的交易区间。"],
            "risk_note": "本分析仅为基于历史量价结构的技术分析，不构成投资建议。",
        }, '{"mode":"unclear"}'


def test_pipeline_mock_api_writes_outputs(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    stock_dir = data_dir / "00"
    stock_dir.mkdir(parents=True)
    rows = 260
    dates = pd.date_range("2025-01-01", periods=rows, freq="D")
    pd.DataFrame({
        "date": list(reversed(dates.strftime("%Y-%m-%d"))),
        "open": [10.0] * rows,
        "high": [11.0] * rows,
        "low": [9.0] * rows,
        "close": [10.5] * rows,
        "volume": [1000] * rows,
        "amount": [0] * rows,
        "turnover": [0] * rows,
        "market_cap": [0] * rows,
    }).to_csv(stock_dir / "000001.csv", index=False)

    def fake_render(csv_path, analysis, output_path, title):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"png")
        return str(output_path)

    monkeypatch.setattr(pipeline_module, "DeepSeekWyckoffClient", FakeClient)
    monkeypatch.setattr(pipeline_module, "render_chart", fake_render)

    runner = WyckoffPipeline(
        config={"wyckoff_ai": {"deepseek_api_key": "test"}},
        data_dir=str(data_dir),
        output_dir=tmp_path / "outputs" / "wyckoff",
    )
    result = runner.analyze_stock("000001")
    assert result["success"] is True
    assert Path(result["paths"]["analysis_path"]).exists()
    assert Path(result["paths"]["chart_path"]).exists()
