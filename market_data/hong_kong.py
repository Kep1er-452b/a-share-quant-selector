"""Hong Kong dataset registry and provider-row normalization."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from market_data.catalog import (
    DatasetCatalog,
    calendar_window_planner,
    partition_planner,
    symbol_calendar_planner,
)
from market_data.models import DatasetSpec


DOMAIN = "hong_kong"
CURRENCY = "HKD"


def canonical_hk_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    if text.endswith(".HK"):
        text = text[:-3]
    if not text.isdigit() or len(text) > 5:
        raise ValueError("Hong Kong symbol must contain up to five digits")
    return f"{text.zfill(5)}.HK"


def canonical_date(value: object) -> tuple[str, str]:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 8:
        raise ValueError("date must use YYYYMMDD format")
    parsed = datetime.strptime(digits, "%Y%m%d")
    return digits, parsed.strftime("%Y-%m-%d")


def _records(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if hasattr(payload, "to_dict"):
        return [dict(row) for row in payload.to_dict("records")]
    if isinstance(payload, Mapping):
        return [dict(payload)]
    return [dict(row) for row in payload]


def normalize_hk_daily(payload: Any) -> list[dict[str, Any]]:
    normalized = []
    for source in _records(payload):
        row = dict(source)
        symbol = canonical_hk_symbol(row.get("ts_code") or row.get("symbol"))
        trade_date, display_date = canonical_date(
            row.get("trade_date") or row.get("date")
        )
        row.update(
            {
                "ts_code": symbol,
                "trade_date": trade_date,
                "symbol": symbol,
                "date": display_date,
                "currency": CURRENCY,
            }
        )
        normalized.append(row)
    return normalized


def _filtered_params(*allowed: str):
    allowed_fields = frozenset(allowed)

    def builder(request, _state):
        return {
            key: value
            for key, value in request.params.items()
            if key in allowed_fields and value not in (None, "")
        }

    return builder


def hong_kong_catalog() -> DatasetCatalog:
    """Return the verified Tushare Hong Kong dataset specifications."""

    price_params = _filtered_params("ts_code", "trade_date", "start_date", "end_date")
    finance_params = _filtered_params(
        "ts_code", "period", "ind_name", "report_type", "start_date", "end_date"
    )
    return DatasetCatalog(
        (
            DatasetSpec(
                dataset_id="hk_basic",
                domain=DOMAIN,
                method="hk_basic",
                key_fields=("ts_code",),
                symbol_field="ts_code",
                parameter_builder=_filtered_params("ts_code", "list_status"),
                request_planner=partition_planner(
                    "list_status",
                    ("L", "D", "P"),
                    allowed=("ts_code",),
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="hk_tradecal",
                domain=DOMAIN,
                method="hk_tradecal",
                key_fields=("cal_date",),
                date_field="cal_date",
                required=False,
                parameter_builder=_filtered_params("start_date", "end_date", "is_open"),
                request_planner=calendar_window_planner(
                    full_start="19860101",
                    years_per_window=5,
                    allowed=("is_open",),
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="hk_daily",
                domain=DOMAIN,
                method="hk_daily",
                key_fields=("ts_code", "trade_date"),
                symbol_field="ts_code",
                date_field="trade_date",
                parameter_builder=price_params,
                request_planner=symbol_calendar_planner(
                    source_dataset="hk_basic",
                    source_field="ts_code",
                    full_start="19860101",
                    years_per_window=10,
                    allowed=("trade_date",),
                ),
                fetch_page_size=2000,
                max_fetch_pages=100_000,
            ),
            DatasetSpec(
                dataset_id="hk_daily_adj",
                domain=DOMAIN,
                method="hk_daily_adj",
                key_fields=("ts_code", "trade_date"),
                symbol_field="ts_code",
                date_field="trade_date",
                required=False,
                parameter_builder=price_params,
                request_planner=symbol_calendar_planner(
                    source_dataset="hk_basic",
                    source_field="ts_code",
                    full_start="19860101",
                    years_per_window=10,
                    allowed=("trade_date",),
                ),
                fetch_page_size=2000,
                max_fetch_pages=100_000,
            ),
            DatasetSpec(
                dataset_id="hk_adjfactor",
                domain=DOMAIN,
                method="hk_adjfactor",
                key_fields=("ts_code", "trade_date"),
                symbol_field="ts_code",
                date_field="trade_date",
                required=False,
                parameter_builder=price_params,
                request_planner=symbol_calendar_planner(
                    source_dataset="hk_basic",
                    source_field="ts_code",
                    full_start="19860101",
                    years_per_window=10,
                    allowed=("trade_date",),
                ),
                fetch_page_size=2000,
                max_fetch_pages=100_000,
            ),
            DatasetSpec(
                dataset_id="hk_income",
                domain=DOMAIN,
                method="hk_income",
                key_fields=("ts_code", "end_date", "ind_name"),
                symbol_field="ts_code",
                date_field="end_date",
                required=False,
                parameter_builder=finance_params,
                request_planner=symbol_calendar_planner(
                    source_dataset="hk_basic",
                    source_field="ts_code",
                    full_start="19900101",
                    years_per_window=15,
                    allowed=("period", "ind_name", "report_type"),
                ),
                fetch_page_size=2000,
                max_fetch_pages=100_000,
            ),
            DatasetSpec(
                dataset_id="hk_balancesheet",
                domain=DOMAIN,
                method="hk_balancesheet",
                key_fields=("ts_code", "end_date", "ind_name"),
                symbol_field="ts_code",
                date_field="end_date",
                required=False,
                parameter_builder=finance_params,
                request_planner=symbol_calendar_planner(
                    source_dataset="hk_basic",
                    source_field="ts_code",
                    full_start="19900101",
                    years_per_window=15,
                    allowed=("period", "ind_name", "report_type"),
                ),
                fetch_page_size=2000,
                max_fetch_pages=100_000,
            ),
            DatasetSpec(
                dataset_id="hk_cashflow",
                domain=DOMAIN,
                method="hk_cashflow",
                key_fields=("ts_code", "end_date", "ind_name"),
                symbol_field="ts_code",
                date_field="end_date",
                required=False,
                parameter_builder=finance_params,
                request_planner=symbol_calendar_planner(
                    source_dataset="hk_basic",
                    source_field="ts_code",
                    full_start="19900101",
                    years_per_window=15,
                    allowed=("period", "ind_name", "report_type"),
                ),
                fetch_page_size=2000,
                max_fetch_pages=100_000,
            ),
            DatasetSpec(
                dataset_id="hk_fina_indicator",
                domain=DOMAIN,
                method="hk_fina_indicator",
                key_fields=("ts_code", "end_date", "report_type"),
                symbol_field="ts_code",
                date_field="end_date",
                required=False,
                parameter_builder=finance_params,
                request_planner=symbol_calendar_planner(
                    source_dataset="hk_basic",
                    source_field="ts_code",
                    full_start="19900101",
                    years_per_window=15,
                    allowed=("period", "ind_name", "report_type"),
                ),
                fetch_page_size=2000,
                max_fetch_pages=100_000,
            ),
        )
    )


__all__ = [
    "CURRENCY",
    "canonical_date",
    "canonical_hk_symbol",
    "hong_kong_catalog",
    "normalize_hk_daily",
]
