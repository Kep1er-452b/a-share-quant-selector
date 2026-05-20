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


class WyckoffSchemaError(ValueError):
    """Raised when model output is not safe or coherent enough to render."""


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


def _normalize_date(value: Any, available_dates: set[str], field_name: str) -> str:
    try:
        date = pd.to_datetime(value, errors="raise").strftime("%Y-%m-%d")
    except Exception as exc:
        raise WyckoffSchemaError(f"{field_name} 日期无法解析: {value}") from exc
    if date not in available_dates:
        raise WyckoffSchemaError(f"{field_name} 日期不在 CSV 真实交易日中: {date}")
    return date


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
        date = _normalize_date(item.get("date"), available_dates, f"event[{index}]")
        price = _validate_price(date, item.get("price"), prices_by_date, f"event[{index}]")
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


def validate_analysis(payload: dict[str, Any], df: pd.DataFrame) -> dict[str, Any]:
    """Validate model output against the actual CSV dates and prices."""
    if not isinstance(payload, dict):
        raise WyckoffSchemaError("AI 输出必须是 JSON 对象")
    prices_by_date = _date_index(df)
    available_dates = set(prices_by_date)
    mode = str(payload.get("mode") or "unclear").strip().lower()
    if mode not in VALID_MODES:
        raise WyckoffSchemaError(f"不支持的威科夫结构: {mode}")

    normalized = deepcopy(payload)
    normalized["mode"] = mode
    normalized["current_phase"] = str(payload.get("current_phase") or "unclear").strip()[:32]
    normalized["summary_text"] = _require_text(payload, "summary_text")
    normalized["risk_note"] = str(
        payload.get("risk_note") or "本分析仅为基于历史量价结构的技术分析，不构成投资建议。"
    ).strip()
    normalized["events"] = _validate_events(payload.get("events"), prices_by_date)
    normalized["ranges"] = _validate_ranges(payload.get("ranges"), available_dates)
    normalized["phases"] = _validate_phases(payload.get("phases"), available_dates)
    scenarios = payload.get("scenarios") or []
    normalized["scenarios"] = scenarios if isinstance(scenarios, list) else [str(scenarios)]
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
