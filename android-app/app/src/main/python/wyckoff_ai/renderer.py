"""Trusted local chart renderer for Wyckoff AI annotations."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from .data import normalize_ohlcv
from .schema import to_chart_annotations


class WyckoffRenderError(RuntimeError):
    """Raised when the local renderer cannot produce a chart."""


def _load_chart_module():
    os.environ.setdefault("MPLBACKEND", "Agg")
    script_path = Path(__file__).resolve().parent.parent / "wyckoff-second" / "scripts" / "wyckoff_chart.py"
    if not script_path.exists():
        raise WyckoffRenderError(f"本地威科夫绘图脚本不存在: {script_path}")
    spec = importlib.util.spec_from_file_location("_local_wyckoff_chart", script_path)
    if spec is None or spec.loader is None:
        raise WyckoffRenderError("无法加载本地威科夫绘图脚本")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def render_chart(csv_path: str | Path, analysis: dict, output_path: str | Path, title: str) -> str:
    """Render a PNG with trusted local plotting code and validated annotations."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    module = _load_chart_module()
    try:
        prices = normalize_ohlcv(module.load_prices(str(csv_path), lookback=500), min_rows=2)
        annotations = to_chart_annotations(analysis)
        if not any(annotations.get(key) for key in ("events", "ranges", "phases")) and hasattr(module, "choose_structure"):
            annotations = module.choose_structure(prices)
            annotations["summary"] = f"本地启发式兜底：{annotations.get('summary', '')}".strip()
        module.draw_chart(prices, annotations, str(output), title)
    except Exception as exc:
        raise WyckoffRenderError(f"威科夫图表渲染失败: {exc}") from exc
    if not output.exists() or output.stat().st_size == 0:
        raise WyckoffRenderError("威科夫图表未成功生成")
    return str(output)
