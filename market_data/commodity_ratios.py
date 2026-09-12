"""Explicit unit conversions and date-aligned commodity ratio calculations.

The public commodity feed is deliberately modelled as an observation source,
not as an exchange-contract replacement.  This module contains no network or
storage code so the unit and missing-data rules can be tested independently.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
import math
from typing import Any


TROY_OUNCE_KG = 0.0311034768
AVOIRDUPOIS_POUND_KG = 0.45359237


_UNIT_ALIASES = {
    "usd/barrel": "USD/barrel",
    "usd/bbl": "USD/barrel",
    "usd_per_barrel": "USD/barrel",
    "usd/troy_oz": "USD/troy_oz",
    "usd/troy-ounce": "USD/troy_oz",
    "usd/oz": "USD/troy_oz",
    "usd/ounce": "USD/troy_oz",
    "usd_per_troy_oz": "USD/troy_oz",
    "usd/lb": "USD/lb",
    "usd/lbs": "USD/lb",
    "usd_per_lb": "USD/lb",
    "cents/lb": "cents/lb",
    "cent/lb": "cents/lb",
    "cents_per_lb": "cents/lb",
    "usd/tonne": "USD/tonne",
    "usd_per_tonne": "USD/tonne",
    "usd/kg": "USD/kg",
    "usd_per_kg": "USD/kg",
}


def canonical_unit(unit: object) -> str:
    """Return one of the supported display units or reject an unknown unit."""

    text = str(unit or "").strip().casefold().replace(" ", "")
    try:
        return _UNIT_ALIASES[text]
    except KeyError as exc:
        raise ValueError(f"unsupported commodity price unit: {unit}") from exc


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_usd_per_kg(value: float, unit: str) -> float:
    if unit == "USD/troy_oz":
        return value / TROY_OUNCE_KG
    if unit == "USD/lb":
        return value / AVOIRDUPOIS_POUND_KG
    if unit == "cents/lb":
        return (value / 100.0) / AVOIRDUPOIS_POUND_KG
    if unit == "USD/tonne":
        return value / 1000.0
    if unit == "USD/kg":
        return value
    raise ValueError(f"unit {unit} cannot be converted through mass")


def _from_usd_per_kg(value: float, unit: str) -> float:
    if unit == "USD/troy_oz":
        return value * TROY_OUNCE_KG
    if unit == "USD/lb":
        return value * AVOIRDUPOIS_POUND_KG
    if unit == "cents/lb":
        return value * AVOIRDUPOIS_POUND_KG * 100.0
    if unit == "USD/tonne":
        return value * 1000.0
    if unit == "USD/kg":
        return value
    raise ValueError(f"unit {unit} cannot be converted through mass")


def convert_price(value: object, from_unit: object, to_unit: object) -> float | None:
    """Convert a finite USD-denominated quote using an explicit unit pair.

    No magnitude-based inference is performed.  In particular, copper is
    converted from cents/lb only when the source explicitly declares that
    unit; a value that merely looks like cents/lb remains ambiguous.
    """

    number = _finite_number(value)
    if number is None:
        return None
    source = canonical_unit(from_unit)
    target = canonical_unit(to_unit)
    if source == target:
        return number
    if source == "USD/barrel" or target == "USD/barrel":
        raise ValueError(f"cannot convert {source} to {target}")
    return _from_usd_per_kg(_to_usd_per_kg(number, source), target)


@dataclass(frozen=True)
class RatioSpec:
    ratio_id: str
    label: str
    numerator: str
    denominator: str
    numerator_unit: str
    denominator_unit: str
    result_unit: str
    formula: str
    definition_version: str = "commodity-ratio-v1"

    def public(self) -> dict[str, Any]:
        return asdict(self)


RATIO_DEFINITIONS: tuple[RatioSpec, ...] = (
    RatioSpec(
        "gold_brent",
        "金油比（Brent）",
        "gold.sina_cfd",
        "brent.sina_cfd",
        "USD/troy_oz",
        "USD/barrel",
        "barrel/troy_oz",
        "Gold USD/troy_oz ÷ Brent USD/barrel",
    ),
    RatioSpec(
        "gold_silver",
        "金银比",
        "gold.sina_cfd",
        "silver.sina_cfd",
        "USD/troy_oz",
        "USD/troy_oz",
        "ratio",
        "Gold USD/troy_oz ÷ Silver USD/troy_oz",
    ),
    RatioSpec(
        "gold_copper_quote",
        "金铜报价比",
        "gold.sina_cfd",
        "copper.sina_cfd",
        "USD/troy_oz",
        "USD/lb",
        "lb/troy_oz",
        "Gold USD/troy_oz ÷ Copper USD/lb",
    ),
    RatioSpec(
        "gold_copper_same_mass",
        "金铜同质量比",
        "gold.sina_cfd",
        "copper.sina_cfd",
        "USD/kg",
        "USD/kg",
        "ratio",
        "Gold USD/kg ÷ Copper USD/kg",
    ),
)

_RATIO_BY_ID = {item.ratio_id: item for item in RATIO_DEFINITIONS}
_RATIO_ALIASES = {
    "gold_oil": "gold_brent",
    "gold_copper": "gold_copper_quote",
}


def ratio_spec(ratio_id: str) -> RatioSpec:
    key = str(ratio_id or "").strip().casefold()
    key = _RATIO_ALIASES.get(key, key)
    try:
        return _RATIO_BY_ID[key]
    except KeyError as exc:
        raise ValueError(f"unknown commodity ratio: {ratio_id}") from exc


def _row_price(row: Mapping[str, Any] | None, target_unit: str) -> float | None:
    if not isinstance(row, Mapping):
        return None
    value = row.get("normalized_price")
    unit = row.get("normalized_unit") or row.get("price_unit")
    if value is None:
        value = row.get("close")
        unit = unit or row.get("raw_unit") or row.get("unit")
    number = _finite_number(value)
    if number is None:
        return None
    if unit is None:
        return number if target_unit == "ratio" else None
    try:
        return convert_price(number, unit, target_unit)
    except ValueError:
        # A malformed source unit is unusable for a ratio, but one bad row
        # should be represented as an unavailable point rather than making a
        # whole date-aligned series impossible to read.
        return None


def calculate_ratio(
    numerator: Mapping[str, Any] | float | int | None,
    denominator: Mapping[str, Any] | float | int | None,
    ratio_id: str,
) -> dict[str, Any]:
    """Calculate one configured ratio and return null reasons explicitly."""

    spec = ratio_spec(ratio_id)
    if isinstance(numerator, Mapping):
        numerator_value = _row_price(numerator, spec.numerator_unit)
    else:
        numerator_value = _finite_number(numerator)
    if isinstance(denominator, Mapping):
        denominator_value = _row_price(denominator, spec.denominator_unit)
    else:
        denominator_value = _finite_number(denominator)

    reason = None
    if numerator_value is None:
        reason = "missing_numerator"
    elif denominator_value is None:
        reason = "missing_denominator"
    elif not math.isfinite(numerator_value):
        reason = "invalid_numerator"
    elif not math.isfinite(denominator_value):
        reason = "invalid_denominator"
    elif numerator_value <= 0:
        reason = "non_positive_numerator"
    elif denominator_value <= 0:
        reason = "non_positive_denominator"

    value = None if reason else round(numerator_value / denominator_value, 8)
    return {
        "value": value,
        "unit": spec.result_unit,
        "reason": reason,
        "formula": spec.formula,
        "definition_version": spec.definition_version,
        "numerator_value": numerator_value,
        "denominator_value": denominator_value,
    }


def _day_key(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return ""


def _display_day(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else value


def _quality_reason(row: Mapping[str, Any] | None) -> str | None:
    if not row:
        return "missing_leg"
    status = str(row.get("quality_status") or "ok").strip().casefold()
    if status not in {"", "ok", "valid", "passed"}:
        return f"quality_{status}"
    if str(row.get("finality") or "final").strip().casefold() == "provisional":
        return "provisional"
    return None


def _index_rows(rows: Iterable[Mapping[str, Any]] | None) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for source in rows or ():
        if not isinstance(source, Mapping):
            continue
        key = _day_key(source.get("session_date") or source.get("date") or source.get("trade_date"))
        if not key:
            continue
        candidate = dict(source)
        existing = indexed.get(key)
        # A later fetched_at is the only deterministic replacement rule.  A
        # provider duplicate is otherwise retained as the first observation
        # and surfaced by the source quality field rather than silently summed.
        if existing is None or str(candidate.get("fetched_at") or "") >= str(existing.get("fetched_at") or ""):
            indexed[key] = candidate
    return indexed


def build_ratio_series(
    rows_by_series: Mapping[str, Iterable[Mapping[str, Any]]],
    ratio_id: str,
    *,
    alignment: str = "intersection",
    start_date: object = None,
    end_date: object = None,
) -> list[dict[str, Any]]:
    """Build a date-aligned line series without forward filling missing legs."""

    spec = ratio_spec(ratio_id)
    alignment = str(alignment or "intersection").strip().casefold()
    if alignment not in {"intersection", "union"}:
        raise ValueError("alignment must be intersection or union")
    numerator_rows = _index_rows(rows_by_series.get(spec.numerator))
    denominator_rows = _index_rows(rows_by_series.get(spec.denominator))
    numerator_dates = set(numerator_rows)
    denominator_dates = set(denominator_rows)
    dates = numerator_dates & denominator_dates if alignment == "intersection" else numerator_dates | denominator_dates
    start_key = _day_key(start_date) if start_date is not None else ""
    end_key = _day_key(end_date) if end_date is not None else ""
    if start_key:
        dates = {item for item in dates if item >= start_key}
    if end_key:
        dates = {item for item in dates if item <= end_key}

    points = []
    for day in sorted(dates):
        numerator_row = numerator_rows.get(day)
        denominator_row = denominator_rows.get(day)
        quality_reason = _quality_reason(numerator_row) or _quality_reason(denominator_row)
        calculated = calculate_ratio(numerator_row, denominator_row, spec.ratio_id)
        reason = quality_reason or calculated["reason"]
        points.append(
            {
                "date": _display_day(day),
                "date_key": day,
                "value": None if reason else calculated["value"],
                "unit": spec.result_unit,
                "reason": reason,
                "formula": spec.formula,
                "definition_version": spec.definition_version,
                "numerator": calculated["numerator_value"] if not quality_reason else None,
                "denominator": calculated["denominator_value"] if not quality_reason else None,
                "numerator_unit": spec.numerator_unit,
                "denominator_unit": spec.denominator_unit,
                "numerator_series": spec.numerator,
                "denominator_series": spec.denominator,
                "numerator_revision": (numerator_row or {}).get("revision") or (numerator_row or {}).get("payload_hash"),
                "denominator_revision": (denominator_row or {}).get("revision") or (denominator_row or {}).get("payload_hash"),
                "finality": "provisional" if any(
                    str(row.get("finality") or "").casefold() == "provisional"
                    for row in (numerator_row, denominator_row)
                    if isinstance(row, Mapping)
                ) else "final",
            }
        )
    return points


__all__ = [
    "AVOIRDUPOIS_POUND_KG",
    "RATIO_DEFINITIONS",
    "RatioSpec",
    "TROY_OUNCE_KG",
    "build_ratio_series",
    "calculate_ratio",
    "canonical_unit",
    "convert_price",
    "ratio_spec",
]
