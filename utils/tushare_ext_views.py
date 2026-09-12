"""Read-model helpers for the local Tushare extension warehouse."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from typing import Iterable

import pandas as pd

from market_data.equity_symbols import canonical_a_share_symbol
from utils.tushare_ext_store import TushareExtStore


INDEX_SYMBOLS = {
    "sh000001": {"ts_code": "000001.SH", "name": "上证指数"},
    "sz399001": {"ts_code": "399001.SZ", "name": "深证成指"},
    "sz399006": {"ts_code": "399006.SZ", "name": "创业板指"},
    "sh000688": {"ts_code": "000688.SH", "name": "科创50"},
    "sh000300": {"ts_code": "000300.SH", "name": "沪深300"},
}

PERIOD_DATASETS = {
    "daily": "index_daily",
    "weekly": "index_daily",
    "monthly": "index_daily",
}
INDEX_RESAMPLE_RULES = {
    "weekly": "W-FRI",
    "monthly": "ME",
}
TRADING_SUMMARY_SOURCE_DATASETS = (
    "daily",
    "daily_basic",
    "moneyflow",
    "top_list",
    "block_trade",
    "margin",
    "moneyflow_hsgt",
)
TRADING_SUMMARY_CACHE_DATASET = "market_trading_summary"


def _date_text(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return text
    return pd.to_datetime(value).strftime("%Y%m%d")


def _display_date(value) -> str:
    text = _date_text(value)
    if len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _to_number(value):
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    if pd.isna(numeric):
        return None
    return numeric


def _round_or_none(value, digits: int = 4):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _normalize_stock_code(code: str) -> str:
    return canonical_a_share_symbol(code)


def _normalize_index_symbol(symbol: str) -> dict:
    key = str(symbol or "").strip().lower()
    if key in INDEX_SYMBOLS:
        return INDEX_SYMBOLS[key]
    compact = key.replace(".", "")
    for item in INDEX_SYMBOLS.values():
        if compact in {item["ts_code"].lower().replace(".", ""), item["ts_code"].split(".")[0]}:
            return item
    return {"ts_code": str(symbol or "").upper(), "name": str(symbol or "").upper()}


def calculate_moving_average(values: Iterable[float | int | None], window: int) -> list[float | None]:
    """Return a same-length simple moving average for ascending price values."""

    window = int(window or 0)
    series = [_to_number(value) for value in values]
    if window <= 0:
        return [None for _ in series]

    result: list[float | None] = []
    rolling: list[float] = []
    for value in series:
        if value is None:
            rolling.append(float("nan"))
        else:
            rolling.append(float(value))
        if len(rolling) > window:
            rolling.pop(0)
        if len(rolling) < window or any(pd.isna(item) for item in rolling):
            result.append(None)
        else:
            result.append(round(sum(rolling) / window, 4))
    return result


def _ema(values: list[float], span: int) -> list[float]:
    alpha = 2 / (int(span) + 1)
    output: list[float] = []
    current: float | None = None
    for value in values:
        current = value if current is None else (value * alpha + current * (1 - alpha))
        output.append(current)
    return output


def calculate_macd(
    values: Iterable[float | int | None],
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, list[float | None]]:
    """Return MACD lines over ascending prices with standard 12/26/9 defaults."""

    prices = [_to_number(value) for value in values]
    numeric = [float(value) if value is not None else None for value in prices]
    if not numeric:
        return {"dif": [], "dea": [], "macd": []}

    filled: list[float] = []
    last = next((value for value in numeric if value is not None), 0.0)
    for value in numeric:
        if value is not None:
            last = value
        filled.append(float(last))

    ema_fast = _ema(filled, fast)
    ema_slow = _ema(filled, slow)
    dif = [fast_value - slow_value for fast_value, slow_value in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    macd = [(dif_value - dea_value) * 2 for dif_value, dea_value in zip(dif, dea)]
    return {
        "dif": [round(value, 4) for value in dif],
        "dea": [round(value, 4) for value in dea],
        "macd": [round(value, 4) for value in macd],
    }


def _resample_index_rows(rows: list[dict], period: str) -> list[dict]:
    if period == "daily" or not rows:
        return rows
    rule = INDEX_RESAMPLE_RULES.get(period)
    if not rule:
        return rows

    frame = pd.DataFrame(rows)
    if frame.empty or "trade_date" not in frame.columns:
        return []
    frame = frame.copy()
    frame["_trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce")
    frame = frame.dropna(subset=["_trade_date"]).sort_values("_trade_date")
    if frame.empty:
        return []

    for column in ("open", "high", "low", "close", "vol", "volume", "amount"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    output: list[dict] = []
    for _, group in frame.groupby(pd.Grouper(key="_trade_date", freq=rule)):
        if group.empty:
            continue
        group = group.sort_values("_trade_date")
        first = group.iloc[0]
        last = group.iloc[-1]
        high = group["high"].max() if "high" in group.columns else None
        low = group["low"].min() if "low" in group.columns else None
        volume_field = "vol" if "vol" in group.columns else "volume" if "volume" in group.columns else None
        record = {
            "ts_code": last.get("ts_code"),
            "trade_date": last["_trade_date"].strftime("%Y%m%d"),
            "open": first.get("open"),
            "high": high,
            "low": low,
            "close": last.get("close"),
            "amount": group["amount"].sum() if "amount" in group.columns else None,
        }
        if volume_field:
            record["vol"] = group[volume_field].sum()
        output.append(record)
    return output


def build_index_kline_payload(
    store: TushareExtStore,
    symbol: str,
    *,
    months: int = 3,
    today: date | None = None,
    period: str = "daily",
    limit: int | str | None = None,
    max_limit: int = 2500,
    indicator_lookback: int = 300,
) -> dict:
    months = min(max(int(months or 3), 3), 6)
    today = today or datetime.now().date()
    period = period if period in PERIOD_DATASETS else "daily"
    dataset = PERIOD_DATASETS[period]
    index_info = _normalize_index_symbol(symbol)
    ts_code = index_info["ts_code"]
    start_date = (pd.Timestamp(today) - pd.DateOffset(months=months)).strftime("%Y%m%d")
    end_date = _date_text(today)

    all_rows = store.query_rows(dataset, ts_code=ts_code, end_date=end_date, descending=False)
    all_rows = _resample_index_rows(all_rows, period)
    closes = [_to_number(row.get("close")) for row in all_rows]
    ma50 = calculate_moving_average(closes, 50)
    ma200 = calculate_moving_average(closes, 200)

    all_candles = []
    for row, ma50_value, ma200_value in zip(all_rows, ma50, ma200):
        trade_date = _date_text(row.get("trade_date"))
        all_candles.append(
            {
                "date": _display_date(trade_date),
                "trade_date": trade_date,
                "open": _round_or_none(row.get("open")),
                "high": _round_or_none(row.get("high")),
                "low": _round_or_none(row.get("low")),
                "close": _round_or_none(row.get("close")),
                "volume": _round_or_none(row.get("vol") if row.get("vol") is not None else row.get("volume")),
                # Tushare daily.amount is thousand yuan; stock detail
                # payloads expose amount in ten-thousand yuan, matching the
                # CSV-backed chart.  Keep the conversion explicit so a
                # reference candle cannot silently change tooltip units.
                "amount": _round_or_none(
                    (_to_number(row.get("amount")) * 1000 / 10000)
                    if _to_number(row.get("amount")) is not None
                    else None
                ),
                "amount_unit": "wan_yuan",
                "MA50": ma50_value,
                "MA200": ma200_value,
            }
        )
    if limit is None:
        candles = [item for item in all_candles if item.get("trade_date", "") >= start_date]
        resolved_limit = None
    else:
        if isinstance(limit, str) and limit.strip().lower() == "all":
            resolved_limit = min(len(all_candles), int(max_limit))
        else:
            try:
                resolved_limit = int(limit)
            except (TypeError, ValueError):
                resolved_limit = 260
            resolved_limit = min(max(resolved_limit, 1), int(max_limit))
        candles = all_candles[-resolved_limit:]

    if resolved_limit is None:
        calculation_candles = candles
    else:
        safe_lookback = min(max(int(indicator_lookback or 1), 1), 600)
        context_count = min(len(all_candles), resolved_limit + safe_lookback - 1)
        calculation_candles = all_candles[-context_count:]

    return {
        "symbol": symbol,
        "ts_code": ts_code,
        "name": index_info["name"],
        "period": period,
        "months": months,
        "limit": resolved_limit,
        "total_bars": len(all_candles),
        "max_limit": int(max_limit),
        "source": f"tushare:{dataset}" if period == "daily" else f"tushare:{dataset}:{period}",
        "cache_status": "ready" if candles else "empty",
        "candles": candles,
        "calculation_candles": calculation_candles,
        "sync_warnings": store.list_warnings(),
    }


def _rows_for_date(store: TushareExtStore, dataset: str, trade_date: str) -> list[dict]:
    return store.query_rows(dataset, start_date=trade_date, end_date=trade_date, descending=False)


def _unique_top_list_rows(rows: Iterable[dict]) -> list[dict]:
    """Choose one representative ranking row per security for market totals.

    ``top_list`` is an evidence table, not a cash-flow ledger: a security can
    be listed for several reasons/windows on the same date and those rows may
    overlap.  Keep the raw rows in SQLite, but make the dashboard's net and
    count metrics security-level.  The largest absolute reported net amount
    is a deterministic representative and avoids adding overlapping windows.
    """

    selected: dict[str, dict] = {}
    ranks: dict[str, tuple[float, str]] = {}
    for row in rows:
        code = str(row.get("ts_code") or row.get("symbol") or "").strip().upper()
        if not code:
            # Malformed rows should not make the entire summary fail, but two
            # anonymous records must not be collapsed into one security.
            code = f"__row__{len(selected)}"
        candidate_value = _to_number(row.get("net_amount"))
        candidate_rank = (
            abs(float(candidate_value)) if isinstance(candidate_value, (int, float)) else -1.0,
            str(row.get("reason") or ""),
        )
        existing = selected.get(code)
        if existing is None:
            selected[code] = dict(row)
            ranks[code] = candidate_rank
        elif candidate_rank > ranks[code]:
            selected[code] = dict(row)
            ranks[code] = candidate_rank
    return list(selected.values())


def _sum_field(rows: Iterable[dict], field: str) -> float:
    total = 0.0
    for row in rows:
        value = _to_number(row.get(field))
        if isinstance(value, (int, float)):
            total += float(value)
    return round(total, 4)


def _sum_field_or_none(rows: Iterable[dict], field: str) -> float | None:
    total = 0.0
    found = False
    for row in rows:
        value = _to_number(row.get(field))
        if isinstance(value, (int, float)):
            total += float(value)
            found = True
    return round(total, 4) if found else None


def _scaled_value(value: float | None, divisor: float) -> float | None:
    if value is None:
        return None
    return round(float(value) / divisor, 4)


def _scaled_sum(rows: Iterable[dict], field: str, divisor: float) -> float | None:
    return _scaled_value(_sum_field_or_none(rows, field), divisor)


def _margin_balance_yi(rows: Iterable[dict]) -> float | None:
    rows = list(rows)
    total = _sum_field_or_none(rows, "rzrqye")
    if total is None:
        rzye = _sum_field_or_none(rows, "rzye")
        rqye = _sum_field_or_none(rows, "rqye")
        if rzye is None and rqye is None:
            return None
        total = float(rzye or 0) + float(rqye or 0)
    # Tushare margin balance fields are yuan-denominated.
    return _scaled_value(total, 100_000_000)


def _previous_trade_date(store: TushareExtStore, latest_date: str, datasets: Iterable[str]) -> str | None:
    if hasattr(store, "latest_trade_date_before"):
        previous = store.latest_trade_date_before(list(datasets), latest_date)
        if previous:
            return previous
    candidates: set[str] = set()
    for dataset in datasets:
        for row in store.query_rows(dataset, end_date=latest_date, descending=True):
            trade_date = _date_text(row.get("trade_date"))
            if trade_date and trade_date < latest_date:
                candidates.add(trade_date)
                break
    return max(candidates) if candidates else None


def _metric(
    label: str,
    value: float | None,
    previous: float | None,
    unit: str = "",
    *,
    as_of_date: str | None = None,
    previous_as_of_date: str | None = None,
) -> dict:
    return {
        "label": label,
        "value": value,
        "previous_value": previous,
        "delta": None if value is None or previous is None else round(value - previous, 4),
        "unit": unit,
        "as_of_date": _display_date(as_of_date) if as_of_date else None,
        "previous_as_of_date": _display_date(previous_as_of_date) if previous_as_of_date else None,
    }


def _latest_dataset_date(store: TushareExtStore, dataset: str, cutoff: str) -> str | None:
    rows = store.query_rows(dataset, end_date=cutoff, limit=1, descending=True)
    return _date_text(rows[0].get("trade_date")) if rows else None


def _dataset_slice_is_incomplete(store, dataset: str, trade_date: str | None) -> bool:
    """Do not expose totals sourced from a slice that was not proven complete."""

    if not trade_date or not hasattr(store, "get_sync_state"):
        return False
    try:
        state = store.get_sync_state(dataset, trade_date)
    except Exception:
        return False
    return bool(state and state.get("status") in {"warning", "failed", "cancelled"})


def _market_summary_signature(
    store: TushareExtStore,
    *,
    latest: str,
    previous: str | None,
    market_amount_yi: float | None,
    previous_market_amount_yi: float | None,
) -> dict:
    dates = [latest]
    if previous:
        dates.append(previous)
    if hasattr(store, "rows_signature"):
        source_signature = store.rows_signature(TRADING_SUMMARY_SOURCE_DATASETS, dates)
    else:
        source_signature = ""
    completeness = []
    if hasattr(store, "get_sync_state"):
        for dataset in TRADING_SUMMARY_SOURCE_DATASETS:
            for trade_date in dates:
                try:
                    state = store.get_sync_state(dataset, trade_date)
                except Exception:
                    state = None
                completeness.append(
                    (dataset, trade_date, (state or {}).get("status"), (state or {}).get("warning"))
                )
    return {
        "version": 3,
        "latest": latest,
        "previous": previous,
        "market_amount_yi": market_amount_yi,
        "previous_market_amount_yi": previous_market_amount_yi,
        "source_signature": source_signature,
        "completeness": completeness,
    }


def _same_json_value(left, right) -> bool:
    """Compare cache metadata before/after JSON persistence.

    SQLite stores the summary payload as JSON, so tuples in the in-memory
    completeness list come back as lists.  Comparing the Python containers
    directly would turn every subsequent read into an unnecessary refresh.
    """

    try:
        return json.dumps(left, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(
            right, ensure_ascii=False, sort_keys=True, default=str
        )
    except (TypeError, ValueError):
        return left == right


def build_market_trading_summary(
    store: TushareExtStore,
    latest_date: str,
    *,
    market_amount_yi: float | None = None,
    previous_market_amount_yi: float | None = None,
) -> dict:
    latest = _date_text(latest_date)
    previous = _previous_trade_date(store, latest, TRADING_SUMMARY_SOURCE_DATASETS)
    cache_signature = _market_summary_signature(
        store,
        latest=latest,
        previous=previous,
        market_amount_yi=market_amount_yi,
        previous_market_amount_yi=previous_market_amount_yi,
    )
    cached = store.get_row(TRADING_SUMMARY_CACHE_DATASET, latest) if hasattr(store, "get_row") else None
    if cached and _same_json_value(cached.get("cache_signature"), cache_signature):
        cached["cache_status"] = "hit"
        cached["sync_warnings"] = store.list_warnings()
        return cached

    current_daily = _rows_for_date(store, "daily", latest)
    previous_daily = _rows_for_date(store, "daily", previous) if previous else []
    current_top_raw = _rows_for_date(store, "top_list", latest)
    previous_top_raw = _rows_for_date(store, "top_list", previous) if previous else []
    current_top = _unique_top_list_rows(current_top_raw)
    previous_top = _unique_top_list_rows(previous_top_raw)
    current_blocks = _rows_for_date(store, "block_trade", latest)
    previous_blocks = _rows_for_date(store, "block_trade", previous) if previous else []
    current_hsgt = _rows_for_date(store, "moneyflow_hsgt", latest)
    previous_hsgt = _rows_for_date(store, "moneyflow_hsgt", previous) if previous else []
    current_moneyflow = _rows_for_date(store, "moneyflow", latest)
    previous_moneyflow = _rows_for_date(store, "moneyflow", previous) if previous else []
    incomplete_current = {
        dataset for dataset in ("top_list", "block_trade", "moneyflow", "moneyflow_hsgt")
        if _dataset_slice_is_incomplete(store, dataset, latest)
    }
    incomplete_previous = {
        dataset for dataset in ("top_list", "block_trade", "moneyflow", "moneyflow_hsgt")
        if _dataset_slice_is_incomplete(store, dataset, previous)
    } if previous else set()
    margin_date = _latest_dataset_date(store, "margin", latest)
    margin_cutoff = (
        (pd.to_datetime(margin_date) - timedelta(days=1)).strftime("%Y%m%d")
        if margin_date else None
    )
    previous_margin_date = _latest_dataset_date(store, "margin", margin_cutoff) if margin_cutoff else None
    current_margin = _rows_for_date(store, "margin", margin_date) if margin_date else []
    previous_margin = _rows_for_date(store, "margin", previous_margin_date) if previous_margin_date else []

    # Tushare daily amount is thousand yuan; top_list amount/net_amount are yuan;
    # moneyflow and block_trade amount fields are ten-thousand yuan.
    market_amount = market_amount_yi
    if market_amount is None:
        market_amount = _scaled_sum(current_daily, "amount", 100_000)
    previous_market_amount = previous_market_amount_yi
    if previous_market_amount is None and previous:
        previous_market_amount = _scaled_sum(previous_daily, "amount", 100_000)
    dragon_tiger_net = (
        None if "top_list" in incomplete_current
        else _scaled_sum(current_top, "net_amount", 100_000_000)
    )
    previous_dragon_tiger_net = (
        None if previous and "top_list" in incomplete_previous
        else _scaled_sum(previous_top, "net_amount", 100_000_000) if previous else None
    )
    block_trade_amount = (
        None if "block_trade" in incomplete_current
        else _scaled_sum(current_blocks, "amount", 10_000)
    )
    previous_block_trade_amount = (
        None if previous and "block_trade" in incomplete_previous
        else _scaled_sum(previous_blocks, "amount", 10_000) if previous else None
    )
    northbound_money = (
        None if "moneyflow_hsgt" in incomplete_current
        else _sum_field_or_none(current_hsgt, "north_money")
    )
    previous_northbound_money = (
        None if previous and "moneyflow_hsgt" in incomplete_previous
        else _sum_field_or_none(previous_hsgt, "north_money") if previous else None
    )
    main_money_flow = (
        None if "moneyflow" in incomplete_current
        else _scaled_sum(current_moneyflow, "net_mf_amount", 10_000)
    )
    previous_main_money_flow = (
        None if previous and "moneyflow" in incomplete_previous
        else _scaled_sum(previous_moneyflow, "net_mf_amount", 10_000) if previous else None
    )
    margin_balance = _margin_balance_yi(current_margin)
    previous_margin_balance = _margin_balance_yi(previous_margin) if previous else None

    metrics = {
        "market_amount": _metric("市场成交额", market_amount, previous_market_amount, "亿元"),
        "main_money_flow": _metric("主力资金流", main_money_flow, previous_main_money_flow, "亿元"),
        "dragon_tiger_net": _metric("龙虎榜净额", dragon_tiger_net, previous_dragon_tiger_net, "亿元"),
        "dragon_tiger_count": _metric(
            "龙虎榜数量",
            None if "top_list" in incomplete_current else float(len(current_top)),
            None if previous and "top_list" in incomplete_previous else float(len(previous_top)) if previous else None,
            "家",
        ),
        "block_trade_amount": _metric("大宗交易金额", block_trade_amount, previous_block_trade_amount, "亿元"),
        "margin_balance": _metric(
            "两融余额",
            margin_balance,
            previous_margin_balance,
            "亿元",
            as_of_date=margin_date,
            previous_as_of_date=previous_margin_date,
        ),
        "northbound_money": _metric("北向资金", northbound_money, previous_northbound_money, "亿元"),
    }
    summary = {
        "trade_date": _display_date(latest),
        "trade_date_key": latest,
        "previous_trade_date": _display_date(previous) if previous else None,
        "previous_trade_date_key": previous,
        "dragon_tiger_source_row_count": len(current_top_raw),
        "dragon_tiger_unique_stock_count": len(current_top),
        "incomplete_datasets": sorted(incomplete_current),
        "metrics": metrics,
        "sync_warnings": store.list_warnings(),
        "cache_signature": cache_signature,
        "cache_status": "refreshed",
    }
    try:
        store.upsert_rows(
            TRADING_SUMMARY_CACHE_DATASET,
            [summary],
            key_fields=("trade_date_key",),
            trade_date_field="trade_date_key",
        )
    except Exception as exc:
        summary["cache_status"] = "refresh_uncached"
        summary["cache_warning"] = str(exc)
    return summary


def _latest_row(store: TushareExtStore, dataset: str, ts_code: str) -> dict:
    rows = store.query_rows(dataset, ts_code=ts_code, limit=1, descending=True)
    return rows[0] if rows else {}


def _pick(row: dict, fields: Iterable[str]) -> dict:
    output = {}
    for field in fields:
        if field in row and row.get(field) not in (None, ""):
            value = row.get(field)
            output[field] = _date_text(value) if field.endswith("date") else _to_number(value)
    return output


def build_stock_extension_payload(store: TushareExtStore, code: str) -> dict:
    ts_code = _normalize_stock_code(code)
    meta = _latest_row(store, "stock_basic", ts_code)
    valuation_row = _latest_row(store, "daily_basic", ts_code)
    financial_row = _latest_row(store, "fina_indicator", ts_code)

    valuation = _pick(
        valuation_row,
        (
            "trade_date",
            "pe",
            "pe_ttm",
            "pb",
            "ps",
            "ps_ttm",
            "dv_ratio",
            "dv_ttm",
            "turnover_rate",
            "turnover_rate_f",
            "volume_ratio",
            "total_mv",
            "circ_mv",
        ),
    )
    financial = _pick(
        financial_row,
        (
            "end_date",
            "roe",
            "roa",
            "grossprofit_margin",
            "netprofit_margin",
            "eps",
            "or_yoy",
            "netprofit_yoy",
            "dt_netprofit_yoy",
            "debt_to_assets",
            "assets_yoy",
            "bps",
        ),
    )
    company = {
        "name": meta.get("name"),
        "industry": meta.get("industry"),
        "area": meta.get("area"),
        "market": meta.get("market"),
        "exchange": meta.get("exchange"),
        "list_date": meta.get("list_date"),
    }
    return {
        "meta": {key: value for key, value in meta.items() if value not in (None, "")},
        "valuation": valuation,
        "financial": financial,
        "company": {key: value for key, value in company.items() if value not in (None, "")},
        "trading": {
            "top_list": _latest_row(store, "top_list", ts_code),
            "moneyflow": _latest_row(store, "moneyflow", ts_code),
            "margin_detail": _latest_row(store, "margin_detail", ts_code),
            "block_trade": _latest_row(store, "block_trade", ts_code),
        },
        "sync_warnings": store.list_warnings(),
    }


def build_adjusted_candles(
    store: TushareExtStore,
    code: str,
    *,
    limit: int | None = None,
    required_trade_dates: Iterable[str] | None = None,
) -> list[dict]:
    """Build qfq chart candles from raw daily prices plus Tushare adj_factor."""

    ts_code = _normalize_stock_code(code)
    price_rows = store.query_rows("daily", ts_code=ts_code, limit=limit, descending=True)
    factor_rows = store.query_rows("adj_factor", ts_code=ts_code, descending=True)
    factors = {
        _date_text(row.get("trade_date")): _to_number(row.get("adj_factor"))
        for row in factor_rows
        if _to_number(row.get("adj_factor")) not in (None, 0)
    }
    if not price_rows or not factors:
        return []

    latest_factor_date = max(factors)
    latest_factor = factors.get(latest_factor_date)
    if not isinstance(latest_factor, (int, float)) or latest_factor == 0:
        return []

    candles = []
    for row in price_rows:
        trade_date = _date_text(row.get("trade_date"))
        factor = factors.get(trade_date)
        if not isinstance(factor, (int, float)) or factor <= 0:
            continue
        ratio = factor / latest_factor
        candles.append(
            {
                "date": _display_date(trade_date),
                "trade_date": trade_date,
                "open": _round_or_none((_to_number(row.get("open")) or 0) * ratio, 4),
                "high": _round_or_none((_to_number(row.get("high")) or 0) * ratio, 4),
                "low": _round_or_none((_to_number(row.get("low")) or 0) * ratio, 4),
                "close": _round_or_none((_to_number(row.get("close")) or 0) * ratio, 4),
                "volume": _round_or_none(row.get("vol") if row.get("vol") is not None else row.get("volume")),
                # Tushare daily.amount is thousand yuan; the stock-detail
                # CSV contract exposes ten-thousand yuan.
                "amount": _round_or_none(
                    (_to_number(row.get("amount")) * 1000 / 10000)
                    if _to_number(row.get("amount")) is not None
                    else None
                ),
                "amount_unit": "wan_yuan",
                "adj_factor": _round_or_none(factor, 8),
                "adjustment": "qfq",
            }
        )
    if required_trade_dates:
        required = {_date_text(value) for value in required_trade_dates if _date_text(value)}
        available = {item.get("trade_date") for item in candles}
        if not required.issubset(available):
            return []

    return candles
