"""Shared Tushare extension update workflow for CLI and Web jobs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from utils.data_provider import get_config_value
from utils.provider_router import provider_data_dir
from utils.tushare_ext_store import TushareExtStore
from utils.tushare_ext_sync import TushareExtSync


PRICE_SKIP_REASON = (
    "默认更新跳过全市场价格扩展回填；设置 AQS_TUSHARE_EXTENSION_FULL_BACKFILL=1 后手动运行完整回填"
)
FINANCIAL_SKIP_REASON = (
    "默认更新跳过全市场财务历史回填；设置 AQS_TUSHARE_EXTENSION_FULL_BACKFILL=1 后手动运行完整回填"
)


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "full"}


def extension_full_backfill_enabled(config=None) -> bool:
    env_value = os.getenv("AQS_TUSHARE_EXTENSION_FULL_BACKFILL") or os.getenv("TUSHARE_EXTENSION_FULL_BACKFILL")
    if env_value is not None:
        return truthy(env_value)
    config = config or {}
    return truthy(
        get_config_value(config, "data_source", "tushare", "extension_full_backfill")
        or get_config_value(config, "tushare_extension", "full_backfill")
    )


def extension_store_for_data_root(data_root=None) -> TushareExtStore:
    root = Path(data_root or "data")
    return TushareExtStore(provider_data_dir(root, "tushare") / "extended")


def recent_tushare_extension_trade_dates(store, latest_text, count=2) -> list[str]:
    count = max(int(count or 0), 0)
    if count == 0:
        return []
    dates = []
    try:
        for row in store.query_rows("trade_cal", end_date=latest_text, limit=max(count * 4, 10), descending=True):
            cal_date = str(row.get("cal_date") or row.get("trade_date") or "").strip()
            if not cal_date or cal_date > latest_text:
                continue
            if str(row.get("is_open")).strip() not in {"1", "1.0", "True", "true"}:
                continue
            if cal_date not in dates:
                dates.append(cal_date)
            if len(dates) >= count:
                break
    except Exception:
        dates = []

    # The fallback cursor must advance independently of the number of dates
    # accepted.  If the local calendar contains Friday only and ``latest`` is
    # Monday, using ``BDay(len(dates))`` repeatedly points at the same Friday
    # after the duplicate is rejected and never makes progress.
    current = pd.to_datetime(latest_text)
    seen = set(dates)
    scan_limit = min(max(count * 4 + 20, 32), 10_000)
    for _ in range(scan_limit):
        if len(dates) >= count:
            break
        current = current - pd.offsets.BDay(1)
        fallback = current.strftime("%Y%m%d")
        if fallback in seen:
            continue
        seen.add(fallback)
        dates.append(fallback)
    return dates[:count]


def skipped_extension_stage(reason):
    return {"status": "skipped", "reason": reason}


def refresh_tushare_extension_data(
    provider,
    target_universe: Iterable[dict],
    latest_trade_date,
    *,
    store: TushareExtStore | None = None,
    data_root=None,
    config=None,
    full_backfill=None,
    price_datasets=None,
    financial_datasets=None,
    trading_datasets=None,
    halt_checker: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
    log_callback: Callable[[str], None] | None = None,
    warning_callback: Callable[[str, str], None] | None = None,
    sync_factory=TushareExtSync,
) -> dict:
    """Run optional Tushare extension stages after the core CSV sync succeeds."""

    store = store or extension_store_for_data_root(data_root)
    sync = sync_factory(
        store,
        provider.pro,
        pro_bar=getattr(getattr(provider, "ts", None), "pro_bar", None),
    )
    latest_text = pd.to_datetime(latest_trade_date).strftime("%Y%m%d")
    full_backfill_enabled = (
        truthy(full_backfill)
        if full_backfill is not None
        else extension_full_backfill_enabled(config)
    )
    target_universe = list(target_universe or [])
    results = {}
    warnings = []

    def emit_log(message: str) -> None:
        if log_callback:
            log_callback(message)

    def extension_halted() -> bool:
        if halt_checker is None:
            return False
        try:
            return bool(halt_checker())
        except InterruptedError:
            return True

    def ensure_extension_continues() -> None:
        if extension_halted():
            raise InterruptedError("用户已停止此次更新")

    def emit_progress(payload: dict) -> None:
        ensure_extension_continues()
        if progress_callback:
            progress_callback(payload)
        if payload.get("status") == "warning":
            emit_log(
                payload.get("warning")
                or f"{payload.get('dataset') or 'extension'} 同步存在权限/数据警告"
            )

    def run_stage(name: str, func) -> None:
        try:
            ensure_extension_continues()
            emit_log(f"开始同步 Tushare 扩展数据: {name}。")
            stage_result = func()
            ensure_extension_continues()
            results[name] = stage_result
            emit_log(f"Tushare 扩展数据 {name} 同步完成。")
        except InterruptedError:
            raise
        except Exception as exc:
            warning = f"Tushare 扩展数据 {name} 同步失败，已保留主行情更新结果: {exc}"
            warnings.append(warning)
            results[name] = {"status": "warning", "warning": warning}
            emit_log(warning)
            if warning_callback:
                warning_callback(name, warning)

    run_stage(
        "basics",
        lambda: sync.sync_basics(
            progress_callback=emit_progress,
            halt_checker=extension_halted,
        ),
    )
    run_stage(
        "index",
        lambda: sync.ensure_index_cache(
            progress_callback=emit_progress,
            halt_checker=extension_halted,
        ),
    )
    if full_backfill_enabled or price_datasets is not None:
        price_start = (pd.to_datetime(latest_text) - pd.DateOffset(years=6)).strftime("%Y%m%d")
        run_stage(
            "prices",
            lambda: sync.sync_price_tracks(
                target_universe,
                start_date=price_start,
                end_date=latest_text,
                datasets=price_datasets,
                progress_callback=emit_progress,
                halt_checker=extension_halted,
            ),
        )
    else:
        ensure_extension_continues()
        results["prices"] = skipped_extension_stage(PRICE_SKIP_REASON)
        emit_log(f"Tushare 扩展数据 prices 已跳过: {PRICE_SKIP_REASON}。")

    existing_valuation = store.latest_trade_date("daily_basic")
    valuation_count = 1
    if existing_valuation and existing_valuation < latest_text:
        gap_days = max((pd.to_datetime(latest_text) - pd.to_datetime(existing_valuation)).days, 1)
        valuation_count = min(max(gap_days + 2, 3), 20)
    valuation_dates = recent_tushare_extension_trade_dates(store, latest_text, count=valuation_count)
    run_stage(
        "valuation",
        lambda: sync.sync_valuation_snapshot(
            valuation_dates,
            progress_callback=emit_progress,
            halt_checker=extension_halted,
        ),
    )
    ensure_extension_continues()
    trading_dates = recent_tushare_extension_trade_dates(store, latest_text, count=2)
    run_stage(
        "trading",
        lambda: sync.sync_trading_snapshot(
            trading_dates,
            datasets=trading_datasets,
            progress_callback=emit_progress,
            halt_checker=extension_halted,
        ),
    )
    if full_backfill_enabled or financial_datasets is not None:
        run_stage(
            "financial",
            lambda: sync.sync_financials_for_universe(
                target_universe,
                end_date=latest_text,
                datasets=financial_datasets,
                progress_callback=emit_progress,
                halt_checker=extension_halted,
            ),
        )
    else:
        ensure_extension_continues()
        results["financial"] = skipped_extension_stage(FINANCIAL_SKIP_REASON)
        emit_log(f"Tushare 扩展数据 financial 已跳过: {FINANCIAL_SKIP_REASON}。")

    stored_warnings = store.list_warnings()
    status = "completed_with_warnings" if warnings or stored_warnings else "completed"
    return {"status": status, "datasets": results, "warnings": warnings + stored_warnings}
