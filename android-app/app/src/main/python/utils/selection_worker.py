"""
选股批处理 worker
"""
from __future__ import annotations

import contextlib
import io
from strategy.strategy_registry import StrategyRegistry
from strategy.formula_strategy import FORMULA_STRATEGY_NAME
from utils.csv_manager import CSVManager
from utils.technical import prepare_selection_features, prepare_strategy_shared_features


_WORKER_CONTEXT = None


def merge_indicator_frames(base_df, frames):
    """把多个策略指标结果合并到同一份 DataFrame。"""
    merged = base_df.copy()
    for frame in frames:
        if frame is None or frame.empty:
            continue
        for column in frame.columns:
            if column not in merged.columns:
                merged[column] = frame[column].values
    return merged


def build_worker_context(data_dir, strategy_names, params_file, runtime_strategy_params=None):
    """构建批处理上下文。"""
    runtime_strategy_params = runtime_strategy_params or {}
    registry = StrategyRegistry(params_file)
    with contextlib.redirect_stdout(io.StringIO()):
        registry.auto_register_from_directory("strategy")

    strategies = {}
    for strategy_name in strategy_names:
        if strategy_name == FORMULA_STRATEGY_NAME:
            from strategy.formula_strategy import FormulaStrategy

            formula_params = runtime_strategy_params.get(FORMULA_STRATEGY_NAME)
            if not formula_params:
                raise ValueError("条件公式策略缺少运行参数")
            strategy = FormulaStrategy(params=formula_params)
        else:
            strategy = registry.get_strategy(strategy_name)
            if strategy is None:
                raise ValueError(f"未找到策略类: {strategy_name}")
        strategies[strategy_name] = strategy

    return {
        "csv_manager": CSVManager(data_dir),
        "strategies": strategies,
    }


def initialize_selection_worker(data_dir, strategy_names, params_file, runtime_strategy_params=None):
    """进程池初始化。"""
    global _WORKER_CONTEXT
    with contextlib.redirect_stdout(io.StringIO()):
        _WORKER_CONTEXT = build_worker_context(data_dir, strategy_names, params_file, runtime_strategy_params)


def process_selection_chunk(candidates, category="all", return_data=False, context=None):
    """处理一批股票的筛选任务。"""
    worker_context = context or _WORKER_CONTEXT
    if worker_context is None:
        raise RuntimeError("selection worker 未初始化")

    csv_manager = worker_context["csv_manager"]
    strategies = worker_context["strategies"]

    results_by_strategy = {strategy_name: [] for strategy_name in strategies}
    indicators_dict = {}
    category_count = {}
    error_counts = {strategy_name: 0 for strategy_name in strategies}
    error_details = []
    processed_count = len(candidates)
    valid_count = 0
    skipped_count = 0
    last_processed_code = None
    last_processed_name = None

    for code, name in candidates:
        last_processed_code = code
        last_processed_name = name
        df = csv_manager.read_stock_for_analysis(code)
        if df.empty or len(df) < 60:
            skipped_count += 1
            continue

        valid_count += 1
        prepared_df = prepare_selection_features(df)
        prepared_df = prepare_strategy_shared_features(prepared_df, strategies.keys())
        indicator_frames = []

        for strategy_name, strategy in strategies.items():
            try:
                df_with_indicators = strategy.calculate_indicators(prepared_df)
                signal_list = strategy.select_stocks(df_with_indicators, name)
            except Exception as exc:
                error_counts[strategy_name] += 1
                if len(error_details) < 20:
                    error_details.append({
                        "code": code,
                        "name": name,
                        "strategy": strategy_name,
                        "error": str(exc),
                        "type": type(exc).__name__,
                    })
                continue

            filtered_signals = []
            for signal in signal_list or []:
                signal_category = signal.get("category", "unknown")
                if category == "all" or signal_category == category:
                    filtered_signals.append(signal)
                    category_count[signal_category] = category_count.get(signal_category, 0) + 1

            if filtered_signals:
                results_by_strategy[strategy_name].append({
                    "code": code,
                    "name": name,
                    "signals": filtered_signals,
                })
                if return_data:
                    indicator_frames.append(df_with_indicators)

        if return_data and indicator_frames:
            indicators_dict[code] = merge_indicator_frames(prepared_df, indicator_frames)

    return {
        "processed_count": processed_count,
        "valid_count": valid_count,
        "skipped_count": skipped_count,
        "results_by_strategy": results_by_strategy,
        "indicators_dict": indicators_dict,
        "category_count": category_count,
        "error_counts": error_counts,
        "error_details": error_details,
        "last_processed_code": last_processed_code,
        "last_processed_name": last_processed_name,
    }
