"""SW industry classification and industrial-cycle local read models."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from market_data.catalog import (
    DatasetCatalog,
    partition_planner,
    period_window_planner,
    source_partition_planner,
    symbol_calendar_planner,
)
from market_data.models import DatasetSpec
from market_data.store import MAX_QUERY_LIMIT


_CYCLE_SERIES = {
    "cn_pmi.headline": {
        "dataset": "cn_pmi",
        "field": "pmi010000",
        "date_field": "month",
        "name": "制造业 PMI",
        "unit": "index",
    },
    "cn_ppi.ppi_yoy": {
        "dataset": "cn_ppi",
        "field": "ppi_yoy",
        "date_field": "month",
        "name": "PPI 同比",
        "unit": "%",
    },
}


def industry_catalog() -> DatasetCatalog:
    """Return classification, SW index, and cycle-component datasets."""

    return DatasetCatalog(
        (
            DatasetSpec(
                dataset_id="index_classify",
                domain="industry",
                method="index_classify",
                key_fields=("index_code",),
                symbol_field="index_code",
                request_planner=partition_planner(
                    "level",
                    ("L1", "L2", "L3"),
                    fixed={"src": "SW2021"},
                    allowed=("index_code",),
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="index_member_all",
                domain="industry",
                method="index_member_all",
                key_fields=("ts_code", "in_date"),
                symbol_field="ts_code",
                date_field="in_date",
                request_planner=source_partition_planner(
                    source_dataset="index_classify",
                    source_field="index_code",
                    provider_field="l1_code",
                    source_filter=lambda row: str(row.get("level") or "").upper() == "L1",
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="sw_daily",
                domain="industry",
                method="sw_daily",
                key_fields=("ts_code", "trade_date"),
                symbol_field="ts_code",
                date_field="trade_date",
                required=False,
                request_planner=symbol_calendar_planner(
                    source_dataset="index_classify",
                    source_field="index_code",
                    full_start="19900101",
                    years_per_window=10,
                    source_filter=lambda row: str(row.get("src") or "").upper() == "SW2021",
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="cn_pmi",
                domain="industry",
                method="cn_pmi",
                key_fields=("month",),
                date_field="month",
                required=False,
                request_planner=period_window_planner(
                    "month", full_start="200501", window_years=10
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="cn_ppi",
                domain="industry",
                method="cn_ppi",
                key_fields=("month",),
                date_field="month",
                required=False,
                request_planner=period_window_planner(
                    "month", full_start="199601", window_years=10
                ),
                fetch_page_size=2000,
            ),
        )
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _current_members(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        if _text(row.get("is_new")).upper() == "N":
            continue
        if _text(row.get("out_date")):
            continue
        symbol = _text(row.get("ts_code"))
        if symbol:
            result.append(dict(row))
    return result


class IndustryService:
    """Bounded queries across SW hierarchy, constituents, and cycle series."""

    def __init__(self, store) -> None:
        self.store = store

    def _all_rows(self, dataset: str, *, symbol: str | None = None) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while offset < 20_000:
            page = self.store.query_rows(
                dataset,
                symbol=symbol,
                limit=MAX_QUERY_LIMIT,
                offset=offset,
            )
            rows.extend(page)
            if len(page) < MAX_QUERY_LIMIT:
                break
            offset += MAX_QUERY_LIMIT
        return rows

    def _coverage(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        classifications = self._all_rows("index_classify")
        known = {_text(row.get("index_code")) for row in classifications}
        members = _current_members(self._all_rows("index_member_all"))
        by_symbol = {row["ts_code"]: row for row in members}
        mapped = []
        unclassified = []
        for symbol, row in sorted(by_symbol.items()):
            codes = {
                _text(row.get(field))
                for field in ("index_code", "l1_code", "l2_code", "l3_code")
            }
            item = {
                "symbol": symbol,
                "name": _text(row.get("name")),
                "route": f"#/equities/a_share/instrument/{symbol}",
            }
            if any(code and code in known for code in codes):
                mapped.append(item)
            else:
                unclassified.append(item)
        total = len(by_symbol)
        coverage = {
            "mapped": len(mapped),
            "total": total,
            "ratio": round(len(mapped) / total, 6) if total else 0.0,
        }
        return coverage, unclassified

    def classifications(self, level: str | None = None) -> dict[str, Any]:
        level_key = _text(level).upper()
        rows = self._all_rows("index_classify")
        items = []
        for row in rows:
            row_level = _text(row.get("level")).upper()
            if level_key and row_level != level_key:
                continue
            items.append(
                {
                    "industry_id": _text(row.get("index_code")),
                    "name": _text(row.get("industry_name") or row.get("name")),
                    "level": row_level,
                    "source": _text(row.get("src")),
                }
            )
        items.sort(key=lambda item: item["industry_id"])
        coverage, unclassified = self._coverage()
        return {
            "items": items,
            "level": level_key or None,
            "coverage": coverage,
            "unclassified": unclassified,
        }

    def detail(self, industry_id: str, *, candle_limit: int = 520) -> dict[str, Any]:
        industry_key = _text(industry_id).upper()
        if not industry_key:
            raise ValueError("industry_id is required")
        try:
            candle_limit = int(candle_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("candle_limit must be an integer") from exc
        if candle_limit < 1 or candle_limit > MAX_QUERY_LIMIT:
            raise ValueError(f"candle_limit must be between 1 and {MAX_QUERY_LIMIT}")

        classifications = self._all_rows("index_classify")
        classification = next(
            (
                row
                for row in classifications
                if _text(row.get("index_code")).upper() == industry_key
            ),
            None,
        )
        if classification is None:
            raise KeyError(f"unknown industry: {industry_key}")

        rows = _current_members(self._all_rows("index_member_all"))
        members = []
        for row in rows:
            codes = {
                _text(row.get(field)).upper()
                for field in ("index_code", "l1_code", "l2_code", "l3_code")
            }
            if industry_key not in codes:
                continue
            symbol = _text(row.get("ts_code")).upper()
            members.append(
                {
                    "symbol": symbol,
                    "name": _text(row.get("name")),
                    "in_date": _text(row.get("in_date")) or None,
                    "route": f"#/equities/a_share/instrument/{symbol}",
                }
            )
        members.sort(key=lambda item: item["symbol"])

        candles = self.store.query_rows(
            "sw_daily", symbol=industry_key, limit=candle_limit
        )
        candles.reverse()
        candle_state = self._index_candle_state(candles)
        coverage, unclassified = self._coverage()
        return {
            "industry": {
                "industry_id": industry_key,
                "name": _text(
                    classification.get("industry_name") or classification.get("name")
                ),
                "level": _text(classification.get("level")).upper(),
            },
            "members": members,
            "index_candles": candles,
            "index_candles_state": candle_state,
            "coverage": coverage,
            "unclassified": unclassified,
        }

    def _index_candle_state(self, candles: list[dict[str, Any]]) -> dict[str, Any]:
        if candles:
            return {"status": "ready", "message": ""}
        state = self.store.get_sync_state("sw_daily") or {}
        message = _text(state.get("warning") or state.get("error"))
        permission_denied = (
            "没有接口" in message and "访问权限" in message
        ) or "PERMISSION_DENIED" in message
        if permission_denied:
            return {
                "status": "permission_denied",
                "message": (
                    "当前订阅不含申万指数日线（sw_daily，通常需更高积分）；"
                    "行业分类、成分股和个股 K 线仍可使用。"
                ),
            }
        return {
            "status": "empty",
            "message": "尚未同步该行业的指数日线；行业分类和成分股仍可使用。",
        }

    def cycle_series(
        self,
        series_ids: Iterable[str],
        *,
        start: str | None = None,
        end: str | None = None,
        normalize: bool = False,
    ) -> dict[str, Any]:
        requested = [_text(series_id) for series_id in series_ids]
        unknown = [series_id for series_id in requested if series_id not in _CYCLE_SERIES]
        if unknown:
            raise ValueError(f"unknown cycle series: {', '.join(unknown)}")

        result = []
        for series_id in requested:
            spec = _CYCLE_SERIES[series_id]
            rows = self.store.query_rows(
                spec["dataset"],
                start_date=start,
                end_date=end,
                limit=MAX_QUERY_LIMIT,
                descending=False,
            )
            points = []
            for row in rows:
                period = _text(row.get(spec["date_field"]))
                value = row.get(spec["field"])
                if not period or value is None:
                    continue
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(numeric):
                    continue
                points.append([period, numeric])
            unit = spec["unit"]
            if normalize:
                values = [point[1] for point in points]
                mean = sum(values) / len(values) if values else 0.0
                variance = (
                    sum((value - mean) ** 2 for value in values) / len(values)
                    if values
                    else 0.0
                )
                deviation = math.sqrt(variance)
                points = [
                    [period, round((value - mean) / deviation, 6) if deviation else 0.0]
                    for period, value in points
                ]
                unit = "z-score"
            result.append(
                {
                    "series_id": series_id,
                    "name": spec["name"],
                    "unit": unit,
                    "points": points,
                }
            )
        return {
            "axis_mode": "normalized" if normalize else "separate",
            "series": result,
        }


__all__ = ["IndustryService", "industry_catalog"]
