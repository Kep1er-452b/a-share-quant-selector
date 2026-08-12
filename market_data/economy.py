"""Registered macroeconomic series and bounded local comparison read models."""

from __future__ import annotations

from collections import defaultdict
import calendar
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Iterable

from market_data.catalog import (
    DatasetCatalog,
    calendar_window_planner,
    period_window_planner,
)
from market_data.models import DatasetSpec
from market_data.store import MAX_QUERY_LIMIT


DOMAIN = "macro"


@dataclass(frozen=True)
class MacroSeriesSpec:
    """One display series backed by one exact Tushare response field."""

    series_id: str
    dataset: str
    field: str
    label: str
    unit: str
    frequency: str
    family: str
    precision: int
    period_field: str
    reference_value: float | None = None
    value_semantics: str = "level"
    transform: str | None = None
    chart_type: str = "line"
    display_note: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _series(
    dataset: str,
    period_field: str,
    frequency: str,
    family: str,
    fields: Iterable[tuple[str, str, str, int]],
    *,
    aliases: dict[str, str] | None = None,
    reference_value: float | None = None,
) -> tuple[MacroSeriesSpec, ...]:
    aliases = aliases or {}
    return tuple(
        MacroSeriesSpec(
            series_id=f"{dataset}.{aliases.get(field, field)}",
            dataset=dataset,
            field=field,
            label=label,
            unit=unit,
            frequency=frequency,
            family=family,
            precision=precision,
            period_field=period_field,
            reference_value=reference_value,
            value_semantics="rate" if unit == "%" else "level",
        )
        for field, label, unit, precision in fields
    )


def _gdp_series() -> tuple[MacroSeriesSpec, ...]:
    """Expose both provider-exact YTD values and derived single-quarter flows."""

    items: list[MacroSeriesSpec] = []
    for field, label, yoy_field, yoy_label in (
        ("gdp", "国内生产总值", "gdp_yoy", "国内生产总值当季同比"),
        ("pi", "第一产业", "pi_yoy", "第一产业同比"),
        ("si", "第二产业", "si_yoy", "第二产业同比"),
        ("ti", "第三产业", "ti_yoy", "第三产业同比"),
    ):
        items.extend(
            (
                MacroSeriesSpec(
                    series_id=f"cn_gdp.{field}_quarterly",
                    dataset="cn_gdp",
                    field=field,
                    label=f"{label}单季度值",
                    unit="亿元",
                    frequency="quarterly",
                    family="growth",
                    precision=2,
                    period_field="quarter",
                    value_semantics="period_flow",
                    transform="ytd_to_quarter",
                    chart_type="bar",
                    display_note=f"由 {field} 年内累计值差分计算",
                ),
                MacroSeriesSpec(
                    series_id=f"cn_gdp.{field}",
                    dataset="cn_gdp",
                    field=field,
                    label=f"{label}累计值",
                    unit="亿元",
                    frequency="quarterly",
                    family="growth",
                    precision=2,
                    period_field="quarter",
                    value_semantics="ytd_flow",
                    chart_type="bar",
                    display_note="年内累计；柱形展示，不跨年度连线",
                ),
                MacroSeriesSpec(
                    series_id=f"cn_gdp.{yoy_field}",
                    dataset="cn_gdp",
                    field=yoy_field,
                    label=yoy_label,
                    unit="%",
                    frequency="quarterly",
                    family="growth",
                    precision=2,
                    period_field="quarter",
                    value_semantics="rate",
                    display_note="Tushare 官方当季同比口径",
                ),
            )
        )
    return tuple(items)


MACRO_SERIES: tuple[MacroSeriesSpec, ...] = (
    *_gdp_series(),
    *_series(
        "cn_cpi",
        "month",
        "monthly",
        "inflation",
        (
            ("nt_val", "全国 CPI 当月值", "指数点", 2),
            ("nt_yoy", "全国 CPI 同比", "%", 2),
            ("nt_mom", "全国 CPI 环比", "%", 2),
            ("nt_accu", "全国 CPI 累计值", "指数点", 2),
            ("town_val", "城市 CPI 当月值", "指数点", 2),
            ("town_yoy", "城市 CPI 同比", "%", 2),
            ("town_mom", "城市 CPI 环比", "%", 2),
            ("town_accu", "城市 CPI 累计值", "指数点", 2),
            ("cnt_val", "农村 CPI 当月值", "指数点", 2),
            ("cnt_yoy", "农村 CPI 同比", "%", 2),
            ("cnt_mom", "农村 CPI 环比", "%", 2),
            ("cnt_accu", "农村 CPI 累计值", "指数点", 2),
        ),
    ),
    *_series(
        "cn_ppi",
        "month",
        "monthly",
        "inflation",
        (
            ("ppi_yoy", "PPI 全部工业品当月同比", "%", 2),
            ("ppi_mp_yoy", "PPI 生产资料当月同比", "%", 2),
            ("ppi_mp_qm_yoy", "PPI 采掘业当月同比", "%", 2),
            ("ppi_mp_rm_yoy", "PPI 原料业当月同比", "%", 2),
            ("ppi_mp_p_yoy", "PPI 加工业当月同比", "%", 2),
            ("ppi_cg_yoy", "PPI 生活资料当月同比", "%", 2),
            ("ppi_cg_f_yoy", "PPI 食品类当月同比", "%", 2),
            ("ppi_cg_c_yoy", "PPI 衣着类当月同比", "%", 2),
            ("ppi_cg_adu_yoy", "PPI 一般日用品类当月同比", "%", 2),
            ("ppi_cg_dcg_yoy", "PPI 耐用消费品类当月同比", "%", 2),
            ("ppi_mom", "PPI 全部工业品环比", "%", 2),
            ("ppi_mp_mom", "PPI 生产资料环比", "%", 2),
            ("ppi_mp_qm_mom", "PPI 采掘业环比", "%", 2),
            ("ppi_mp_rm_mom", "PPI 原料业环比", "%", 2),
            ("ppi_mp_p_mom", "PPI 加工业环比", "%", 2),
            ("ppi_cg_mom", "PPI 生活资料环比", "%", 2),
            ("ppi_cg_f_mom", "PPI 食品类环比", "%", 2),
            ("ppi_cg_c_mom", "PPI 衣着类环比", "%", 2),
            ("ppi_cg_adu_mom", "PPI 一般日用品类环比", "%", 2),
            ("ppi_cg_dcg_mom", "PPI 耐用消费品类环比", "%", 2),
            ("ppi_accu", "PPI 全部工业品累计同比", "%", 2),
            ("ppi_mp_accu", "PPI 生产资料累计同比", "%", 2),
            ("ppi_mp_qm_accu", "PPI 采掘业累计同比", "%", 2),
            ("ppi_mp_rm_accu", "PPI 原料业累计同比", "%", 2),
            ("ppi_mp_p_accu", "PPI 加工业累计同比", "%", 2),
            ("ppi_cg_accu", "PPI 生活资料累计同比", "%", 2),
            ("ppi_cg_f_accu", "PPI 食品类累计同比", "%", 2),
            ("ppi_cg_c_accu", "PPI 衣着类累计同比", "%", 2),
            ("ppi_cg_adu_accu", "PPI 一般日用品类累计同比", "%", 2),
            ("ppi_cg_dcg_accu", "PPI 耐用消费品类累计同比", "%", 2),
        ),
    ),
    *_series(
        "cn_m",
        "month",
        "monthly",
        "money",
        (
            ("m0", "M0 货币供应量", "亿元", 2),
            ("m0_yoy", "M0 同比", "%", 2),
            ("m0_mom", "M0 环比", "%", 2),
            ("m1", "M1 货币供应量", "亿元", 2),
            ("m1_yoy", "M1 同比", "%", 2),
            ("m1_mom", "M1 环比", "%", 2),
            ("m2", "M2 货币供应量", "亿元", 2),
            ("m2_yoy", "M2 同比", "%", 2),
            ("m2_mom", "M2 环比", "%", 2),
        ),
    ),
    *_series(
        "cn_pmi",
        "month",
        "monthly",
        "cycle",
        (
            ("pmi010000", "制造业 PMI", "指数点", 1),
            ("pmi010100", "制造业 PMI 大型企业", "指数点", 1),
            ("pmi010200", "制造业 PMI 中型企业", "指数点", 1),
            ("pmi010300", "制造业 PMI 小型企业", "指数点", 1),
            ("pmi010400", "制造业 PMI 生产指数", "指数点", 1),
            ("pmi010500", "制造业 PMI 新订单指数", "指数点", 1),
            ("pmi010600", "制造业 PMI 供应商配送时间指数", "指数点", 1),
            ("pmi010700", "制造业 PMI 原材料库存指数", "指数点", 1),
            ("pmi010800", "制造业 PMI 从业人员指数", "指数点", 1),
            ("pmi010900", "制造业 PMI 新出口订单", "指数点", 1),
            ("pmi011000", "制造业 PMI 进口", "指数点", 1),
            ("pmi011100", "制造业 PMI 采购量", "指数点", 1),
            ("pmi011200", "制造业 PMI 原材料购进价格", "指数点", 1),
            ("pmi011300", "制造业 PMI 出厂价格", "指数点", 1),
            ("pmi011400", "制造业 PMI 产成品库存", "指数点", 1),
            ("pmi011500", "制造业 PMI 在手订单", "指数点", 1),
            ("pmi011600", "制造业 PMI 生产经营活动预期", "指数点", 1),
            ("pmi011700", "制造业 PMI 装备制造业", "指数点", 1),
            ("pmi011800", "制造业 PMI 高技术制造业", "指数点", 1),
            ("pmi011900", "制造业 PMI 基础原材料制造业", "指数点", 1),
            ("pmi012000", "制造业 PMI 消费品制造业", "指数点", 1),
            ("pmi020100", "非制造业 PMI 商务活动", "指数点", 1),
            ("pmi020200", "非制造业 PMI 新订单", "指数点", 1),
            ("pmi020300", "非制造业 PMI 投入品价格", "指数点", 1),
            ("pmi020400", "非制造业 PMI 销售价格", "指数点", 1),
            ("pmi020500", "非制造业 PMI 从业人员", "指数点", 1),
            ("pmi020600", "非制造业 PMI 业务活动预期", "指数点", 1),
            ("pmi020700", "非制造业 PMI 新出口订单", "指数点", 1),
            ("pmi020800", "非制造业 PMI 在手订单", "指数点", 1),
            ("pmi020900", "非制造业 PMI 存货", "指数点", 1),
            ("pmi021000", "非制造业 PMI 供应商配送时间", "指数点", 1),
            ("pmi030000", "中国综合 PMI 产出指数", "指数点", 1),
        ),
        aliases={"pmi010000": "headline"},
        reference_value=50.0,
    ),
    *_series(
        "shibor",
        "date",
        "daily",
        "rates",
        (
            ("on", "Shibor 隔夜", "%", 4),
            ("1w", "Shibor 1 周", "%", 4),
            ("2w", "Shibor 2 周", "%", 4),
            ("1m", "Shibor 1 个月", "%", 4),
            ("3m", "Shibor 3 个月", "%", 4),
            ("6m", "Shibor 6 个月", "%", 4),
            ("9m", "Shibor 9 个月", "%", 4),
            ("1y", "Shibor 1 年", "%", 4),
        ),
    ),
)


_SERIES_BY_ID = {spec.series_id: spec for spec in MACRO_SERIES}


def _release_cursor_builder(period_kind: str):
    if period_kind == "quarter":
        exact_key, start_key, end_key = "q", "start_q", "end_q"
    elif period_kind == "month":
        exact_key, start_key, end_key = "m", "start_m", "end_m"
    elif period_kind == "date":
        exact_key, start_key, end_key = "date", "start_date", "end_date"
    else:  # pragma: no cover - catalog definitions are static
        raise ValueError(f"unsupported macro period kind: {period_kind}")

    allowed = {exact_key, start_key, end_key, "fields"}

    def builder(request, state):
        params = {
            key: value
            for key, value in request.params.items()
            if key in allowed and value not in (None, "")
        }
        cursor = str((state or {}).get("cursor") or "").strip()
        if cursor and exact_key not in params and start_key not in params:
            # Deliberately re-fetch the newest stored release because macro
            # values are commonly revised after their first publication.
            params[start_key] = cursor
        return params

    return builder


def economy_catalog() -> DatasetCatalog:
    """Return bounded official Tushare macro dataset specifications."""

    return DatasetCatalog(
        (
            DatasetSpec(
                dataset_id="cn_gdp",
                domain=DOMAIN,
                method="cn_gdp",
                key_fields=("quarter",),
                date_field="quarter",
                freshness=timedelta(days=95),
                parameter_builder=_release_cursor_builder("quarter"),
                request_planner=period_window_planner(
                    "quarter", full_start="1992Q1", window_years=10
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="cn_cpi",
                domain=DOMAIN,
                method="cn_cpi",
                key_fields=("month",),
                date_field="month",
                freshness=timedelta(days=35),
                parameter_builder=_release_cursor_builder("month"),
                request_planner=period_window_planner(
                    "month", full_start="198601", window_years=10
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="cn_ppi",
                domain=DOMAIN,
                method="cn_ppi",
                key_fields=("month",),
                date_field="month",
                freshness=timedelta(days=35),
                parameter_builder=_release_cursor_builder("month"),
                request_planner=period_window_planner(
                    "month", full_start="199601", window_years=10
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="cn_m",
                domain=DOMAIN,
                method="cn_m",
                key_fields=("month",),
                date_field="month",
                freshness=timedelta(days=35),
                parameter_builder=_release_cursor_builder("month"),
                request_planner=period_window_planner(
                    "month", full_start="199001", window_years=10
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="cn_pmi",
                domain=DOMAIN,
                method="cn_pmi",
                key_fields=("month",),
                date_field="month",
                required=False,
                freshness=timedelta(days=35),
                parameter_builder=_release_cursor_builder("month"),
                request_planner=period_window_planner(
                    "month", full_start="200501", window_years=10
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="shibor",
                domain=DOMAIN,
                method="shibor",
                key_fields=("date",),
                date_field="date",
                freshness=timedelta(days=1),
                parameter_builder=_release_cursor_builder("date"),
                request_planner=calendar_window_planner(
                    full_start="20061008", years_per_window=5
                ),
                fetch_page_size=2000,
            ),
        )
    )


def _bound_for_frequency(value: object, frequency: str, *, end: bool) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if "Q" in text:
        year, _, quarter_text = text.partition("Q")
        if len(year) != 4 or quarter_text not in {"1", "2", "3", "4"}:
            raise ValueError("quarter bounds must use YYYYQ1 through YYYYQ4")
        quarter = int(quarter_text)
        if frequency == "quarterly":
            return text
        month = quarter * 3 if end else (quarter - 1) * 3 + 1
        if frequency == "daily":
            day = calendar.monthrange(int(year), month)[1] if end else 1
            return f"{year}{month:02d}{day:02d}"
        return f"{year}{month:02d}"

    digits = "".join(character for character in text if character.isdigit())
    if len(digits) < 4:
        raise ValueError("macro bounds must include a four-digit year")
    year = digits[:4]
    month = int(digits[4:6]) if len(digits) >= 6 else (12 if end else 1)
    if not 1 <= month <= 12:
        raise ValueError("macro month bounds must be between 01 and 12")
    if frequency == "quarterly":
        return f"{year}Q{(month - 1) // 3 + 1}"
    if frequency == "monthly":
        return f"{year}{month:02d}"
    if len(digits) >= 8:
        return digits[:8]
    day = calendar.monthrange(int(year), month)[1] if end else 1
    return f"{year}{month:02d}{day:02d}"


def _stable_bucket_sample(points: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Return deterministic evenly-spaced buckets while preserving endpoints."""

    if len(points) <= limit:
        return points
    if limit == 1:
        return [points[-1]]
    last = len(points) - 1
    indexes = [round(position * last / (limit - 1)) for position in range(limit)]
    return [points[index] for index in indexes]


def _series_points(
    spec: MacroSeriesSpec,
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build exact or explicitly derived observations for one catalog series."""

    ordered = sorted(
        (
            row
            for row in rows
            if spec.period_field in row and spec.field in row
        ),
        key=lambda row: str(row[spec.period_field]),
    )
    if spec.transform is None:
        return [
            {"period": str(row[spec.period_field]), "value": row.get(spec.field)}
            for row in ordered
        ]
    if spec.transform != "ytd_to_quarter":  # pragma: no cover - static catalog invariant
        raise ValueError(f"unsupported macro series transform: {spec.transform}")

    points: list[dict[str, Any]] = []
    cumulative_by_year: dict[str, dict[int, float]] = defaultdict(dict)
    for row in ordered:
        period = str(row[spec.period_field]).upper()
        year, separator, quarter_text = period.partition("Q")
        if separator != "Q" or len(year) != 4 or quarter_text not in {"1", "2", "3", "4"}:
            continue
        try:
            cumulative = float(row.get(spec.field))
        except (TypeError, ValueError):
            continue
        quarter = int(quarter_text)
        cumulative_by_year[year][quarter] = cumulative
        if quarter == 1:
            value = cumulative
        else:
            previous = cumulative_by_year[year].get(quarter - 1)
            if previous is None:
                continue
            value = cumulative - previous
        points.append({"period": period, "value": round(value, spec.precision)})
    return points


class EconomyService:
    """Expose registered macro series without mutating provider data."""

    MAX_SERIES = 12
    MAX_POINTS = MAX_QUERY_LIMIT

    def __init__(self, store) -> None:
        self.store = store

    def series_catalog(self, family: str | None = None) -> list[dict[str, Any]]:
        family_key = str(family or "").strip().casefold()
        return [
            spec.public()
            for spec in MACRO_SERIES
            if not family_key or spec.family.casefold() == family_key
        ]

    def _dataset_rows(
        self,
        dataset: str,
        *,
        start: str | None,
        end: str | None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.store.query_rows(
                dataset,
                start_date=start,
                end_date=end,
                limit=MAX_QUERY_LIMIT,
                offset=offset,
                descending=False,
            )
            rows.extend(page)
            if len(page) < MAX_QUERY_LIMIT:
                return rows
            offset += len(page)

    def series(
        self,
        series_ids: Iterable[str],
        start: str | None = None,
        end: str | None = None,
        max_points: int = 120,
    ) -> dict[str, Any]:
        requested = [str(series_id or "").strip() for series_id in series_ids]
        if not requested or any(not series_id for series_id in requested):
            raise ValueError("series_ids must not be empty")
        if len(requested) > self.MAX_SERIES:
            raise ValueError(f"at most {self.MAX_SERIES} macro series may be compared")
        try:
            point_limit = int(max_points)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_points must be an integer") from exc
        if point_limit < 1 or point_limit > self.MAX_POINTS:
            raise ValueError(f"max_points must be between 1 and {self.MAX_POINTS}")

        unknown = [series_id for series_id in requested if series_id not in _SERIES_BY_ID]
        if unknown:
            raise ValueError(f"unknown macro series: {', '.join(unknown)}")

        selected = [_SERIES_BY_ID[series_id] for series_id in requested]
        by_dataset: dict[str, list[MacroSeriesSpec]] = defaultdict(list)
        for spec in selected:
            by_dataset[spec.dataset].append(spec)

        raw_rows: dict[str, list[dict[str, Any]]] = {}
        for dataset, specs in by_dataset.items():
            frequency = specs[0].frequency
            raw_rows[dataset] = self._dataset_rows(
                dataset,
                start=_bound_for_frequency(start, frequency, end=False),
                end=_bound_for_frequency(end, frequency, end=True),
            )

        payload_series = []
        for spec in selected:
            points = _series_points(spec, raw_rows[spec.dataset])
            sampled = len(points) > point_limit
            shown = _stable_bucket_sample(points, point_limit)
            item = spec.public()
            item.update(
                {
                    "points": shown,
                    "source_count": len(points),
                    "sampled": sampled,
                }
            )
            payload_series.append(item)

        units = {spec.unit for spec in selected}
        return {
            "series": payload_series,
            "series_ids": requested,
            "start": start,
            "end": end,
            "max_points": point_limit,
            "axis_mode": "shared" if len(units) == 1 else "separate",
        }


__all__ = [
    "EconomyService",
    "MACRO_SERIES",
    "MacroSeriesSpec",
    "economy_catalog",
]
