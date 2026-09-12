"""
选股批处理 worker
"""
from __future__ import annotations

import contextlib
import io
import pandas as pd
from strategy.strategy_registry import StrategyRegistry
from strategy.formula_strategy import FORMULA_STRATEGY_NAME
from utils.csv_manager import CSVManager
from utils.market_watchlist import canonical_equity_symbol
from utils.technical import prepare_selection_features, prepare_strategy_shared_features
from market_data.equity_policy import equity_policy


_WORKER_CONTEXT = None


def merge_indicator_frames(base_df, frames):
    """把多个策略指标结果合并到同一份 DataFrame。"""
    merged = base_df.copy()
    for frame in frames:
        if frame is None or frame.empty:
            continue
        if len(frame) != len(merged) or not frame.index.equals(merged.index):
            raise ValueError("策略指标帧与行情数据的长度或索引不一致")
        for column in frame.columns:
            if column not in merged.columns:
                merged[column] = frame[column].values
    return merged


def build_worker_context(
    data_dir,
    strategy_names,
    params_file,
    runtime_strategy_params=None,
    *,
    market_id="a_share",
    reader=None,
    strategy_scopes=None,
):
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

    scopes = dict(strategy_scopes or {})
    for strategy_name, strategy in strategies.items():
        scopes.setdefault(
            strategy_name,
            str(getattr(strategy, "market_scope", "") or "a_share_only"),
        )
    policy = equity_policy(market_id)
    rejected = [
        name
        for name in strategies
        if not policy.is_strategy_allowed(name, declared_scope=scopes.get(name))
    ]
    if rejected:
        raise ValueError(
            f"strategy {rejected[0]} is not supported for {market_id}"
        )
    return {
        "market_id": market_id,
        "csv_manager": CSVManager(data_dir) if reader is None else None,
        "reader": reader,
        "strategies": strategies,
        "strategy_scopes": scopes,
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

    market_id = str(worker_context.get("market_id") or "a_share")
    csv_manager = worker_context.get("csv_manager")
    reader = worker_context.get("reader")
    strategies = worker_context["strategies"]
    strategy_scopes = dict(worker_context.get("strategy_scopes") or {})
    policy = equity_policy(market_id)
    for strategy_name, strategy in strategies.items():
        scope = strategy_scopes.get(strategy_name) or getattr(
            strategy, "market_scope", None
        )
        if not policy.is_strategy_allowed(strategy_name, declared_scope=scope):
            raise ValueError(
                f"strategy {strategy_name} is not supported for {market_id}"
            )
    if reader is None and csv_manager is None:
        raise RuntimeError("selection worker 缺少行情 reader")

    results_by_strategy = {strategy_name: [] for strategy_name in strategies}
    indicators_dict = {}
    category_count = {}
    error_counts = {strategy_name: 0 for strategy_name in strategies}
    strategy_valid_counts = {strategy_name: 0 for strategy_name in strategies}
    error_details = []
    processed_count = 0
    valid_count = 0
    skipped_count = 0
    last_processed_code = None
    last_processed_name = None

    for code, name in candidates:
        cancel_event = worker_context.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            break
        processed_count += 1
        canonical_symbol = canonical_equity_symbol(market_id, code)
        result_code = canonical_symbol if market_id == "hong_kong" else canonical_symbol.split(".", 1)[0]
        last_processed_code = result_code
        last_processed_name = name
        try:
            df = (
                reader.read_analysis_frame(canonical_symbol)
                if reader is not None
                else csv_manager.read_stock_for_analysis(result_code)
            )
            if df is None:
                df = pd.DataFrame()
            if not df.empty and len(df) >= 60:
                required_columns = {"date", "open", "high", "low", "close", "volume"}
                missing_columns = required_columns - set(df.columns)
                if missing_columns:
                    raise ValueError(
                        f"{market_id} analysis data missing required columns: "
                        f"{', '.join(sorted(missing_columns))}"
                    )
            prepared_df = prepare_selection_features(df) if not df.empty and len(df) >= 60 else df
        except Exception as exc:
            for strategy_name in strategies:
                error_counts[strategy_name] += 1
            if len(error_details) < 20:
                error_details.append({
                    "code": result_code,
                    "name": name,
                    "strategy": "market_data",
                    "error": str(exc),
                    "type": type(exc).__name__,
                })
            continue
        if df.empty or len(df) < 60:
            skipped_count += 1
            continue

        valid_count += 1
        try:
            prepared_df = prepare_strategy_shared_features(prepared_df, strategies)
        except Exception as exc:
            for strategy_name in strategies:
                error_counts[strategy_name] += 1
            if len(error_details) < 20:
                error_details.append({
                    "code": result_code,
                    "name": name,
                    "strategy": "shared_features",
                    "error": str(exc),
                    "type": type(exc).__name__,
                })
            continue
        indicator_frames = []

        for strategy_name, strategy in strategies.items():
            if cancel_event is not None and cancel_event.is_set():
                break
            minimum_history = max(
                60,
                int(getattr(strategy, "MIN_HISTORY_DAYS", 60)),
            )
            if len(prepared_df) < minimum_history:
                continue
            strategy_valid_counts[strategy_name] += 1
            try:
                df_with_indicators = strategy.calculate_indicators(prepared_df)
                signal_list = strategy.select_stocks(df_with_indicators, name)
                if return_data:
                    # Validate here so one malformed strategy is reported and
                    # skipped instead of crashing the complete worker chunk.
                    merge_indicator_frames(prepared_df, [df_with_indicators])
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
                    "market": market_id,
                    "symbol": canonical_symbol,
                    "code": result_code,
                    "name": name,
                    "signals": filtered_signals,
                })
                if return_data:
                    indicator_frames.append(df_with_indicators)

        if return_data and indicator_frames:
            indicators_dict[result_code] = merge_indicator_frames(prepared_df, indicator_frames)

    return {
        "processed_count": processed_count,
        "valid_count": valid_count,
        "skipped_count": skipped_count,
        "results_by_strategy": results_by_strategy,
        "indicators_dict": indicators_dict,
        "category_count": category_count,
        "error_counts": error_counts,
        "strategy_valid_counts": strategy_valid_counts,
        "error_details": error_details,
        "last_processed_code": last_processed_code,
        "last_processed_name": last_processed_name,
    }
