from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_server
from utils.runtime_paths import OUTPUT_ROOT_ENV, selection_results_dir, wyckoff_results_dir
from wyckoff_ai.pipeline import WyckoffPipeline


def test_runtime_output_paths_can_be_kept_outside_repository(tmp_path, monkeypatch):
    output_root = tmp_path / "runtime-results"
    monkeypatch.setenv(OUTPUT_ROOT_ENV, str(output_root))

    assert selection_results_dir() == output_root / "选股结果"
    assert wyckoff_results_dir() == output_root / "威科夫分析结果"
    assert web_server._wyckoff_outputs_root() == output_root / "威科夫分析结果"

    pipeline = WyckoffPipeline(config={}, data_dir=str(tmp_path / "data"))
    assert pipeline.output_dir == output_root / "威科夫分析结果"


def test_selection_report_is_written_to_runtime_output_root(tmp_path, monkeypatch):
    output_root = tmp_path / "runtime-results"
    monkeypatch.setenv(OUTPUT_ROOT_ENV, str(output_root))

    report_path = Path(web_server._save_selection_markdown({}, "2026-06-10 16:00:00"))

    assert report_path.parent == output_root / "选股结果"
    assert report_path.exists()


def test_external_wyckoff_chart_is_served_from_runtime_output_root(tmp_path, monkeypatch):
    output_root = tmp_path / "runtime-results"
    monkeypatch.setenv(OUTPUT_ROOT_ENV, str(output_root))
    chart_path = output_root / "威科夫分析结果" / "测试股票-000001" / "run" / "charts" / "chart.png"
    chart_path.parent.mkdir(parents=True)
    chart_path.write_bytes(b"png")

    chart_url = web_server._wyckoff_chart_url(chart_path)
    response = web_server.app.test_client().get(chart_url)

    assert chart_url == "/outputs/wyckoff/files/%E6%B5%8B%E8%AF%95%E8%82%A1%E7%A5%A8-000001/run/charts/chart.png"
    assert response.status_code == 200
    assert response.data == b"png"
