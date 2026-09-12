"""Catalog and local read services for public global-commodity observations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import math
from typing import Any

from market_data.catalog import DatasetCatalog
from market_data.commodity_ratios import RATIO_DEFINITIONS, build_ratio_series, ratio_spec
from market_data.models import DatasetSpec, FetchPage
from market_data.store import MAX_QUERY_LIMIT


DOMAIN = "global_commodities"
DATASET = "commodity_daily"
DEFINITION_VERSION = "sina-cfd-v1"
CONVERSION_VERSION = "commodity-units-v1"
SOURCE_PROVIDER = "sina_cfd"
SOURCE_URL = "https://finance.sina.com.cn/futures/quotes/{symbol}.shtml"
FULL_START_DATE = "20160907"
MAX_SYNC_DAYS = 4_500


@dataclass(frozen=True)
class CommoditySeriesSpec:
    series_id: str
    asset: str
    label: str
    provider: str
    provider_symbol: str
    venue: str
    instrument_type: str
    currency: str
    raw_unit: str
    normalized_unit: str
    price_basis: str = "close"
    source_timezone: str = "source_label"
    roll_method: str = "vendor_unspecified"
    unit_status: str = "source_profiled"
    conversion_version: str = CONVERSION_VERSION
    definition_version: str = DEFINITION_VERSION

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "source_url": SOURCE_URL.format(symbol=self.provider_symbol),
                "identity": f"{self.provider}:{self.provider_symbol}",
            }
        )
        return payload


# These IDs intentionally identify the public CFD observation source.  They
# must not be renamed to brent.ice or gold.comex without a separately verified
# real-contract adapter and definition version.
COMMODITY_SERIES: tuple[CommoditySeriesSpec, ...] = (
    CommoditySeriesSpec(
        series_id="brent.sina_cfd",
        asset="brent",
        label="布伦特原油 CFD",
        provider=SOURCE_PROVIDER,
        provider_symbol="OIL",
        venue="SINA",
        instrument_type="cfd",
        currency="USD",
        raw_unit="USD/barrel",
        normalized_unit="USD/barrel",
    ),
    CommoditySeriesSpec(
        series_id="gold.sina_cfd",
        asset="gold",
        label="纽约黄金 CFD",
        provider=SOURCE_PROVIDER,
        provider_symbol="GC",
        venue="SINA",
        instrument_type="cfd",
        currency="USD",
        raw_unit="USD/troy_oz",
        normalized_unit="USD/troy_oz",
    ),
    CommoditySeriesSpec(
        series_id="copper.sina_cfd",
        asset="copper",
        label="美铜 CFD",
        provider=SOURCE_PROVIDER,
        provider_symbol="HG",
        venue="SINA",
        instrument_type="cfd",
        currency="USD",
        raw_unit="USD/lb",
        normalized_unit="USD/lb",
    ),
    CommoditySeriesSpec(
        series_id="silver.sina_cfd",
        asset="silver",
        label="纽约白银 CFD",
        provider=SOURCE_PROVIDER,
        provider_symbol="SI",
        venue="SINA",
        instrument_type="cfd",
        currency="USD",
        raw_unit="USD/troy_oz",
        normalized_unit="USD/troy_oz",
    ),
)
_SERIES_BY_ID = {item.series_id: item for item in COMMODITY_SERIES}


def series_spec(series_id: str) -> CommoditySeriesSpec:
    key = str(series_id or "").strip().casefold()
    try:
        return _SERIES_BY_ID[key]
    except KeyError as exc:
        raise ValueError(f"unknown commodity series: {series_id}") from exc


def _day(value: object, *, default: str | None = None) -> str:
    if value is None or str(value).strip() == "":
        return default or ""
    text = str(value).strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 8:
        candidate = digits[:8]
        try:
            date.fromisoformat(f"{candidate[:4]}-{candidate[4:6]}-{candidate[6:]}")
        except ValueError as exc:
            raise ValueError("commodity date must use YYYYMMDD") from exc
        return candidate
    raise ValueError("commodity date must use YYYYMMDD")


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _records(rows: Any) -> list[dict[str, Any]]:
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    if isinstance(rows, Mapping):
        rows = [rows]
    if not isinstance(rows, Iterable) or isinstance(rows, (str, bytes)):
        raise ValueError("commodity provider result must be rows or a DataFrame")
    output = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("commodity provider rows must be mappings")
        output.append({str(key).strip().casefold(): value for key, value in row.items()})
    return output


def _date_from_source(value: object) -> str:
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 8:
        candidate = digits[:8]
        try:
            date.fromisoformat(f"{candidate[:4]}-{candidate[4:6]}-{candidate[6:]}")
        except ValueError:
            return ""
        return candidate
    return ""


def _normalized_price(value: float, spec: CommoditySeriesSpec) -> float:
    # The Sina profile declares HG as USD/lb.  If a future source declares
    # cents/lb it must create a distinct provider profile or pass that explicit
    # unit into the adapter; no value-magnitude guessing belongs here.
    if spec.raw_unit == spec.normalized_unit:
        return value
    from market_data.commodity_ratios import convert_price

    converted = convert_price(value, spec.raw_unit, spec.normalized_unit)
    if converted is None:
        raise ValueError("commodity price is not finite")
    return converted


def normalize_commodity_rows(
    rows: Any,
    spec: CommoditySeriesSpec | str,
    *,
    start_date: object = None,
    end_date: object = None,
    fetched_at: str | None = None,
    request_fingerprint: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize one provider response while retaining source/unit evidence."""

    resolved = series_spec(spec) if isinstance(spec, str) else spec
    start = _day(start_date) if start_date not in (None, "") else ""
    end = _day(end_date) if end_date not in (None, "") else ""
    fetched_at = fetched_at or date.today().isoformat()
    normalized: dict[str, dict[str, Any]] = {}
    duplicate_dates: set[str] = set()
    for source in _records(rows):
        session_date = _date_from_source(source.get("date") or source.get("trade_date") or source.get("session_date"))
        if not session_date or (start and session_date < start) or (end and session_date > end):
            continue
        raw_values = {
            key: _number(source.get(key))
            for key in ("open", "high", "low", "close", "settlement", "volume", "position")
        }
        close = raw_values["close"]
        if close is None or close <= 0:
            continue
        high = raw_values["high"]
        low = raw_values["low"]
        open_value = raw_values["open"]
        if high is not None and low is not None and high < low:
            continue
        if high is not None and any(value is not None and high < value for value in (open_value, close)):
            continue
        if low is not None and any(value is not None and low > value for value in (open_value, close)):
            continue
        normalized_values = {
            key: (_normalized_price(value, resolved) if key in {"open", "high", "low", "close", "settlement"} and value is not None else value)
            for key, value in raw_values.items()
        }
        if session_date in normalized:
            duplicate_dates.add(session_date)
            # Keep the later source row as the current observation.  The
            # quality flag remains visible to ratio readers.
        quality_flags = ["duplicate_session_date"] if session_date in duplicate_dates else []
        row = {
            "series_id": resolved.series_id,
            "asset": resolved.asset,
            "label": resolved.label,
            "provider": resolved.provider,
            "provider_symbol": resolved.provider_symbol,
            "venue": resolved.venue,
            "instrument_type": resolved.instrument_type,
            "currency": resolved.currency,
            "session_date": session_date,
            "price_basis": resolved.price_basis,
            "raw_open": raw_values["open"],
            "raw_high": raw_values["high"],
            "raw_low": raw_values["low"],
            "raw_close": raw_values["close"],
            "raw_settlement": raw_values["settlement"],
            "raw_unit": resolved.raw_unit,
            "open": normalized_values["open"],
            "high": normalized_values["high"],
            "low": normalized_values["low"],
            "close": normalized_values["close"],
            "settlement": normalized_values["settlement"],
            "raw_price": raw_values["close"],
            "normalized_price": normalized_values["close"],
            "normalized_unit": resolved.normalized_unit,
            "unit_status": resolved.unit_status,
            "conversion_version": resolved.conversion_version,
            "source_timezone": resolved.source_timezone,
            "roll_method": resolved.roll_method,
            "definition_version": resolved.definition_version,
            "source_url": SOURCE_URL.format(symbol=resolved.provider_symbol),
            "fetched_at": fetched_at,
            "available_at": fetched_at,
            "finality": "provisional" if session_date >= date.today().strftime("%Y%m%d") else "final",
            "quality_status": "warning" if quality_flags else "ok",
            "quality_flags": quality_flags,
            "volume": raw_values["volume"],
            "open_interest": raw_values["position"],
            "request_fingerprint": request_fingerprint,
        }
        normalized[session_date] = row
    return [normalized[key] for key in sorted(normalized)]


def _commodity_planner(request, _state, _store):
    selected = request.params.get("series_id")
    if selected is None or selected == "":
        series_ids = tuple(item.series_id for item in COMMODITY_SERIES)
    elif isinstance(selected, str):
        series_ids = (selected.strip().casefold(),)
    else:
        raise ValueError("commodity series_id must be a string")
    for item in series_ids:
        series_spec(item)
    start = _day(request.params.get("start_date"), default=FULL_START_DATE)
    end = _day(request.params.get("end_date"), default=date.today().strftime("%Y%m%d"))
    if start > end:
        raise ValueError("commodity start_date must not be after end_date")
    if (
        date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:]}")
        - date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:]}")
    ).days > MAX_SYNC_DAYS:
        raise ValueError(f"commodity sync range exceeds {MAX_SYNC_DAYS} days")
    return tuple(
        FetchPage(
            cursor=f"{series_id}:{start}-{end}",
            params={"series_id": series_id, "start_date": start, "end_date": end},
        )
        for series_id in series_ids
    )


def commodity_catalog() -> DatasetCatalog:
    return DatasetCatalog(
        (
            DatasetSpec(
                dataset_id=DATASET,
                domain=DOMAIN,
                method="fetch_daily",
                key_fields=("series_id", "session_date", "price_basis", "definition_version"),
                symbol_field="series_id",
                date_field="session_date",
                batch_size=1000,
                freshness=timedelta(days=1),
                request_planner=_commodity_planner,
                max_fetch_pages=8,
            ),
        )
    )


def _day_bound(value: object) -> str | None:
    return _day(value) if value not in (None, "") else None


def _display_day(value: object) -> str:
    text = str(value or "")
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 and text.isdigit() else text


class CommodityService:
    """Read-only catalog, daily series, and ratio views over local data."""

    MAX_SERIES_LIMIT = 2_000
    MAX_RATIO_LIMIT = 2_000
    MAX_HISTORY_ROWS = 20_000

    def __init__(self, store) -> None:
        self.store = store

    def catalog(self) -> dict[str, Any]:
        items = []
        for spec in COMMODITY_SERIES:
            latest = self.store.query_rows(DATASET, symbol=spec.series_id, limit=1, descending=True)
            item = spec.public()
            item.update(
                {
                    "latest": latest[0] if latest else None,
                    "row_count": self.store.count_rows(DATASET, symbol=spec.series_id)
                    if hasattr(self.store, "count_rows")
                    else None,
                }
            )
            items.append(item)
        return {
            "domain": DOMAIN,
            "source": SOURCE_PROVIDER,
            "items": items,
            "ratios": [spec.public() for spec in RATIO_DEFINITIONS],
            "alignment_default": "intersection",
            "offline_readable": True,
        }

    def _rows(self, series_id: str, start: object = None, end: object = None) -> list[dict[str, Any]]:
        start_key = _day_bound(start)
        end_key = _day_bound(end)
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.store.query_rows(
                DATASET,
                symbol=series_id,
                start_date=start_key,
                end_date=end_key,
                limit=MAX_QUERY_LIMIT,
                offset=offset,
                descending=False,
            )
            rows.extend(page)
            if len(page) < MAX_QUERY_LIMIT:
                return rows
            offset += len(page)
            if offset >= self.MAX_HISTORY_ROWS:
                raise ValueError(
                    f"commodity history exceeds the bounded limit {self.MAX_HISTORY_ROWS}"
                )

    def series(
        self,
        series_id: str,
        *,
        start: object = None,
        end: object = None,
        limit: int = 520,
    ) -> dict[str, Any]:
        spec = series_spec(series_id)
        try:
            point_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("commodity series limit must be an integer") from exc
        if point_limit < 1 or point_limit > self.MAX_SERIES_LIMIT:
            raise ValueError(f"commodity series limit must be between 1 and {self.MAX_SERIES_LIMIT}")
        rows = self._rows(spec.series_id, start, end)
        shown = rows[-point_limit:]
        return {
            "series": spec.public(),
            "points": [
                {
                    **row,
                    "date": _display_day(row.get("session_date")),
                    "date_key": row.get("session_date"),
                }
                for row in shown
            ],
            "total": len(rows),
            "limit": point_limit,
            "start": start,
            "end": end,
            "quality": {
                "provisional_count": sum(row.get("finality") == "provisional" for row in rows),
                "warning_count": sum(row.get("quality_status") not in (None, "ok") for row in rows),
            },
        }

    def ratios(
        self,
        ratio_id: str,
        *,
        start: object = None,
        end: object = None,
        limit: int = 520,
        alignment: str = "intersection",
    ) -> dict[str, Any]:
        spec = ratio_spec(ratio_id)
        try:
            point_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("commodity ratio limit must be an integer") from exc
        if point_limit < 1 or point_limit > self.MAX_RATIO_LIMIT:
            raise ValueError(f"commodity ratio limit must be between 1 and {self.MAX_RATIO_LIMIT}")
        rows_by_series = {
            spec.numerator: self._rows(spec.numerator, start, end),
            spec.denominator: self._rows(spec.denominator, start, end),
        }
        points = build_ratio_series(
            rows_by_series,
            spec.ratio_id,
            alignment=alignment,
            start_date=start,
            end_date=end,
        )
        return {
            "ratio": spec.public(),
            "points": points[-point_limit:],
            "total": len(points),
            "limit": point_limit,
            "alignment": str(alignment or "intersection").strip().casefold(),
            "start": start,
            "end": end,
            "quality": {
                "valid_count": sum(point.get("value") is not None for point in points),
                "null_count": sum(point.get("value") is None for point in points),
            },
        }

    def overview(self) -> dict[str, Any]:
        catalog = self.catalog()
        cards = []
        for item in catalog["items"]:
            latest = item.get("latest") or {}
            cards.append(
                {
                    **{key: item.get(key) for key in ("series_id", "asset", "label", "instrument_type", "provider", "normalized_unit")},
                    "value": latest.get("normalized_price"),
                    "date": _display_day(latest.get("session_date")),
                    "finality": latest.get("finality"),
                    "quality_status": latest.get("quality_status"),
                }
            )
        return {"domain": DOMAIN, "cards": cards, "ratios": catalog["ratios"], "source": SOURCE_PROVIDER}


__all__ = [
    "COMMODITY_SERIES",
    "CONVERSION_VERSION",
    "DATASET",
    "DOMAIN",
    "CommoditySeriesSpec",
    "CommodityService",
    "commodity_catalog",
    "normalize_commodity_rows",
    "series_spec",
]
