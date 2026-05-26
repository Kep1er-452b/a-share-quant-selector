"""Validation and normalization for AI-generated Wyckoff analysis."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pandas as pd


VALID_MODES = {
    "accumulation",
    "distribution",
    "markup",
    "markdown",
    "reaccumulation",
    "redistribution",
    "unclear",
}

MODE_ALIASES = {
    "acc": "accumulation",
    "accumulate": "accumulation",
    "accumulating": "accumulation",
    "reacc": "reaccumulation",
    "reaccumulate": "reaccumulation",
    "redist": "redistribution",
    "redistribute": "redistribution",
    "dist": "distribution",
    "distribute": "distribution",
    "distributing": "distribution",
    "distriction": "distribution",
    "distribuition": "distribution",
    "distributon": "distribution",
    "distribtion": "distribution",
    "mark-up": "markup",
    "uptrend": "markup",
    "mark-down": "markdown",
    "downtrend": "markdown",
    "decline": "markdown",
    "unknown": "unclear",
    "neutral": "unclear",
}

MAX_DATE_SNAP_DAYS = 10


class WyckoffSchemaError(ValueError):
    """Raised when model output is not safe or coherent enough to render."""


def _normalize_mode(value: Any) -> str:
    raw = str(value or "unclear").strip().lower()
    compact = raw.replace("_", "").replace("-", "").replace(" ", "")
    if raw in VALID_MODES:
        return raw
    if compact in VALID_MODES:
        return compact
    if raw in MODE_ALIASES:
        return MODE_ALIASES[raw]
    if compact in MODE_ALIASES:
        return MODE_ALIASES[compact]
    if "redis" in compact:
        return "redistribution"
    if "reacc" in compact:
        return "reaccumulation"
    if compact.startswith("distri") or compact.startswith("distrib") or "distribution" in compact:
        return "distribution"
    if "accumul" in compact:
        return "accumulation"
    if "markdown" in compact or "downtrend" in compact:
        return "markdown"
    if "markup" in compact or "uptrend" in compact:
        return "markup"
    if "unclear" in compact or "unknown" in compact:
        return "unclear"
    return compact


def _require_text(payload: dict[str, Any], key: str, default: str = "") -> str:
    value = str(payload.get(key) or default).strip()
    if not value:
        raise WyckoffSchemaError(f"AI 输出缺少必要文本字段: {key}")
    return value


def _date_index(df: pd.DataFrame) -> dict[str, dict[str, float]]:
    rows = {}
    for row in df.to_dict("records"):
        date = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
        rows[date] = {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
    return rows


def _parse_date(value: Any, field_name: str) -> str:
    try:
        return pd.to_datetime(value, errors="raise").strftime("%Y-%m-%d")
    except Exception as exc:
        raise WyckoffSchemaError(f"{field_name} 日期无法解析: {value}") from exc


def _candidate_dates(value: Any, available_dates: set[str], field_name: str) -> list[str]:
    date = _parse_date(value, field_name)
    if date not in available_dates:
        target = pd.Timestamp(date)
        candidates = sorted(
            available_dates,
            key=lambda item: (
                abs((pd.Timestamp(item) - target).days),
                0 if pd.Timestamp(item) <= target else 1,
            ),
        )
        if not candidates:
            raise WyckoffSchemaError("CSV 中没有可用交易日")
        nearest = candidates[0]
        if abs((pd.Timestamp(nearest) - target).days) > MAX_DATE_SNAP_DAYS:
            raise WyckoffSchemaError(f"{field_name} 日期不在 CSV 真实交易日中: {date}")
        return candidates[:5]

    target = pd.Timestamp(date)
    nearby = sorted(
        available_dates,
        key=lambda item: (
            abs((pd.Timestamp(item) - target).days),
            0 if item == date else 1,
            0 if pd.Timestamp(item) <= target else 1,
        ),
    )
    return [item for item in nearby if abs((pd.Timestamp(item) - target).days) <= MAX_DATE_SNAP_DAYS][:5]


def _normalize_date(value: Any, available_dates: set[str], field_name: str) -> str:
    return _candidate_dates(value, available_dates, field_name)[0]


def _validate_price(date: str, price: Any, prices_by_date: dict[str, dict[str, float]], field_name: str) -> float:
    try:
        numeric = float(price)
    except Exception as exc:
        raise WyckoffSchemaError(f"{field_name} 价格无法解析: {price}") from exc
    row = prices_by_date[date]
    low = min(row["open"], row["high"], row["low"], row["close"])
    high = max(row["open"], row["high"], row["low"], row["close"])
    tolerance = max((high - low) * 1.8, high * 0.08, 0.01)
    if numeric < low - tolerance or numeric > high + tolerance:
        raise WyckoffSchemaError(f"{field_name} 价格 {numeric:.4f} 与 {date} 当日 OHLC 不匹配")
    return round(numeric, 4)


def _validate_events(events: Any, prices_by_date: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    if events is None:
        return []
    if not isinstance(events, list):
        raise WyckoffSchemaError("events 必须是数组")
    available_dates = set(prices_by_date)
    normalized = []
    for index, item in enumerate(events[:16], 1):
        if not isinstance(item, dict):
            raise WyckoffSchemaError("events 中每一项都必须是对象")
        term = str(item.get("term") or item.get("name") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not term or not reason:
            raise WyckoffSchemaError(f"第 {index} 个事件缺少 term 或 reason")
        price_error = None
        date = None
        price = None
        for candidate_date in _candidate_dates(item.get("date"), available_dates, f"event[{index}]"):
            try:
                candidate_price = _validate_price(candidate_date, item.get("price"), prices_by_date, f"event[{index}]")
                date = candidate_date
                price = candidate_price
                break
            except WyckoffSchemaError as exc:
                price_error = exc
        if date is None or price is None:
            raise price_error or WyckoffSchemaError(f"event[{index}] 日期或价格无法校验")
        normalized.append({
            "term": term[:24],
            "date": date,
            "price": price,
            "reason": reason[:90],
            "kind": str(item.get("kind") or "event").strip()[:24],
            "confidence": str(item.get("confidence") or "medium").strip()[:16],
        })
    return normalized


def _validate_ranges(ranges: Any, available_dates: set[str]) -> list[dict[str, Any]]:
    if ranges is None:
        return []
    if not isinstance(ranges, list):
        raise WyckoffSchemaError("ranges 必须是数组")
    normalized = []
    for index, item in enumerate(ranges[:6], 1):
        if not isinstance(item, dict):
            raise WyckoffSchemaError("ranges 中每一项都必须是对象")
        start = _normalize_date(item.get("start"), available_dates, f"range[{index}].start")
        end = _normalize_date(item.get("end"), available_dates, f"range[{index}].end")
        try:
            low = float(item.get("low"))
            high = float(item.get("high"))
        except Exception as exc:
            raise WyckoffSchemaError(f"range[{index}] 上下沿无法解析") from exc
        if low >= high:
            raise WyckoffSchemaError(f"range[{index}] low 必须小于 high")
        kind = str(item.get("kind") or "accumulation").strip().lower()
        if kind not in {"accumulation", "distribution"}:
            kind = "accumulation" if "acc" in kind else "distribution"
        normalized.append({
            "kind": kind,
            "start": start,
            "end": end,
            "low": round(low, 4),
            "high": round(high, 4),
            "label": str(item.get("label") or ("吸筹区" if kind == "accumulation" else "派发区")).strip()[:24],
        })
    return normalized


def _validate_phases(phases: Any, available_dates: set[str]) -> list[dict[str, Any]]:
    if phases is None:
        return []
    if not isinstance(phases, list):
        raise WyckoffSchemaError("phases 必须是数组")
    normalized = []
    for index, item in enumerate(phases[:8], 1):
        if not isinstance(item, dict):
            raise WyckoffSchemaError("phases 中每一项都必须是对象")
        label = str(item.get("label") or "").strip()
        if not label:
            raise WyckoffSchemaError(f"phase[{index}] 缺少 label")
        normalized.append({
            "label": label[:24],
            "start": _normalize_date(item.get("start"), available_dates, f"phase[{index}].start"),
            "end": _normalize_date(item.get("end"), available_dates, f"phase[{index}].end"),
        })
    return normalized


def _validate_key_levels(levels: Any, df: pd.DataFrame) -> list[dict[str, Any]]:
    if levels is None:
        return []
    if not isinstance(levels, list):
        raise WyckoffSchemaError("key_levels 必须是数组")
    low_limit = float(df["low"].min()) * 0.88
    high_limit = float(df["high"].max()) * 1.12
    normalized = []
    for index, item in enumerate(levels[:10], 1):
        if not isinstance(item, dict):
            raise WyckoffSchemaError("key_levels 中每一项都必须是对象")
        try:
            price = float(item.get("price"))
        except Exception as exc:
            raise WyckoffSchemaError(f"key_level[{index}] 价格无法解析") from exc
        if price < low_limit or price > high_limit:
            raise WyckoffSchemaError(f"key_level[{index}] 价格明显超出行情范围: {price:.4f}")
        normalized.append({
            "price": round(price, 4),
            "label": str(item.get("label") or "关键价位").strip()[:32],
            "meaning": str(item.get("meaning") or "").strip()[:120],
        })
    return normalized


def validate_analysis(payload: dict[str, Any], df: pd.DataFrame) -> dict[str, Any]:
    """Validate model output against the actual CSV dates and prices."""
    if not isinstance(payload, dict):
        raise WyckoffSchemaError("AI 输出必须是 JSON 对象")
    prices_by_date = _date_index(df)
    available_dates = set(prices_by_date)
    mode = _normalize_mode(payload.get("mode"))
    if mode not in VALID_MODES:
        raise WyckoffSchemaError(f"不支持的威科夫结构: {mode}")

    normalized = deepcopy(payload)
    normalized["mode"] = mode
    normalized["current_phase"] = str(payload.get("current_phase") or "unclear").strip()[:32]
    normalized["summary_text"] = _require_text(payload, "summary_text")
    normalized["background_text"] = str(payload.get("background_text") or "").strip()[:240]
    normalized["risk_note"] = str(
        payload.get("risk_note") or "本分析仅为基于历史量价结构的技术分析，不构成投资建议。"
    ).strip()
    normalized["events"] = _validate_events(payload.get("events"), prices_by_date)
    normalized["ranges"] = _validate_ranges(payload.get("ranges"), available_dates)
    normalized["phases"] = _validate_phases(payload.get("phases"), available_dates)
    normalized["key_levels"] = _validate_key_levels(payload.get("key_levels"), df)
    scenarios = payload.get("scenarios") or []
    normalized["scenarios"] = scenarios if isinstance(scenarios, list) else [str(scenarios)]
    normalized["conclusion_text"] = str(payload.get("conclusion_text") or "").strip()[:180]
    return normalized


def to_chart_annotations(analysis: dict[str, Any]) -> dict[str, Any]:
    """Convert validated analysis into the renderer's annotation schema."""
    def shorten(text: Any, limit: int) -> str:
        value = str(text or "").strip().replace("\n", " ")
        return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"

    events = []
    for item in (analysis.get("events") or [])[:5]:
        copied = dict(item)
        copied["reason"] = shorten(copied.get("reason"), 24)
        events.append(copied)

    return {
        "mode": analysis.get("mode", "unclear"),
        "summary": shorten(analysis.get("summary_text", ""), 72),
        "events": events,
        "ranges": analysis.get("ranges", []),
        "phases": (analysis.get("phases") or [])[:6],
    }
