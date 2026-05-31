#!/usr/bin/env python3
"""Benchmark selection throughput for the active local data provider."""

from __future__ import annotations

import argparse
import cProfile
import contextlib
import io
import json
import os
import pstats
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategy.strategy_registry import StrategyRegistry
from utils.csv_manager import CSVManager
from utils.selection_worker import build_worker_context, process_selection_chunk


def active_data_dir() -> Path:
    state_path = ROOT / "data" / "active_provider.json"
    if not state_path.exists():
        return ROOT / "data"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    provider_path = state.get("provider_state", {}).get("path")
    if provider_path:
        return ROOT / provider_path
    provider = state.get("active_provider")
    if provider:
        return ROOT / "data" / "providers" / provider
    return ROOT / "data"


def load_strategy_names(params_file: Path) -> list[str]:
    registry = StrategyRegistry(params_file)
    with contextlib.redirect_stdout(io.StringIO()):
        registry.auto_register_from_directory("strategy")
    return registry.list_strategies()


def run_once(data_dir: Path, params_file: Path, sample_size: int | None, profile_lines: int) -> dict:
    manager = CSVManager(data_dir)
    codes = manager.list_all_stocks()
    if sample_size is not None:
        codes = codes[:sample_size]
    candidates = [(code, code) for code in codes]
    strategy_names = load_strategy_names(params_file)
    context = build_worker_context(str(data_dir), strategy_names, str(params_file))

    profiler = cProfile.Profile()
    start = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        result = profiler.runcall(process_selection_chunk, candidates, "all", False, context)
    elapsed = time.perf_counter() - start

    stats_output = io.StringIO()
    pstats.Stats(profiler, stream=stats_output).sort_stats("cumtime").print_stats(profile_lines)
    selected_count = sum(len(items) for items in result["results_by_strategy"].values())
    return {
        "data_dir": str(data_dir),
        "sample_size": sample_size,
        "candidate_count": len(candidates),
        "strategy_count": len(strategy_names),
        "quant_core_disabled": os.environ.get("QUANT_CORE_DISABLE") == "1",
        "elapsed_seconds": round(elapsed, 4),
        "valid_count": result["valid_count"],
        "skipped_count": result["skipped_count"],
        "selected_count": selected_count,
        "error_counts": result["error_counts"],
        "profile": stats_output.getvalue(),
    }


def print_run(label: str, payload: dict) -> None:
    print(f"## {label}")
    print(json.dumps({k: v for k, v in payload.items() if k != "profile"}, ensure_ascii=False, indent=2))
    print(payload["profile"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local stock selection.")
    parser.add_argument("--sample-size", type=int, default=300, help="Number of stocks for the sample run.")
    parser.add_argument("--full", action="store_true", help="Also run all available stocks.")
    parser.add_argument("--profile-lines", type=int, default=35, help="Number of cProfile rows to print.")
    parser.add_argument("--data-dir", type=Path, default=None, help="Override the active provider data directory.")
    parser.add_argument("--params-file", type=Path, default=ROOT / "config" / "strategy_params.yaml")
    args = parser.parse_args()

    data_dir = args.data_dir or active_data_dir()
    sample = run_once(data_dir, args.params_file, args.sample_size, args.profile_lines)
    print_run(f"sample-{args.sample_size}", sample)
    if args.full:
        full = run_once(data_dir, args.params_file, None, args.profile_lines)
        print_run("full", full)


if __name__ == "__main__":
    main()
