"""Runtime output paths that stay outside the Git repository."""

from __future__ import annotations

import os
from pathlib import Path


OUTPUT_ROOT_ENV = "A_SHARE_QUANT_OUTPUT_ROOT"
DEFAULT_OUTPUT_FOLDER = "A股量化选股系统数据"


def runtime_output_root() -> Path:
    configured = str(os.environ.get(OUTPUT_ROOT_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "DocumentsData" / DEFAULT_OUTPUT_FOLDER


def selection_results_dir() -> Path:
    return runtime_output_root() / "选股结果"


def wyckoff_results_dir() -> Path:
    return runtime_output_root() / "威科夫分析结果"
