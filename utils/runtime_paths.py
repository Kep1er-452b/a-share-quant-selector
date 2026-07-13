"""Backward-compatible helpers for selection and Wyckoff output paths."""

from __future__ import annotations

from pathlib import Path

from utils.platform_paths import (
    DEFAULT_OUTPUT_FOLDER,
    OUTPUT_ROOT_ENV,
    runtime_paths as resolve_runtime_paths,
)


def runtime_output_root() -> Path:
    return resolve_runtime_paths().outputs_root


def selection_results_dir() -> Path:
    return runtime_output_root() / "选股结果"


def wyckoff_results_dir() -> Path:
    return runtime_output_root() / "威科夫分析结果"
