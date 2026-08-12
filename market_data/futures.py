"""Futures dataset registry, normalization, and bounded read models."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from market_data.catalog import DatasetCatalog, partition_planner
from market_data.models import DatasetSpec, FetchPage
from market_data.store import MAX_QUERY_LIMIT


_EXCHANGE_BY_SUFFIX = {
    "CFX": "CFFEX",
    "DCE": "DCE",
    "GFE": "GFEX",
    "INE": "INE",
    "SHF": "SHFE",
    "ZCE": "CZCE",
}


def futures_catalog() -> DatasetCatalog:
    """Return the verified Tushare futures datasets used by this domain."""

    return DatasetCatalog(
        (
            DatasetSpec(
                dataset_id="fut_basic",
                domain="futures",
                method="fut_basic",
                key_fields=("ts_code",),
                symbol_field="ts_code",
                date_field="list_date",
                batch_size=1000,
                request_planner=partition_planner(
                    "exchange",
                    ("CFFEX", "SHFE", "DCE", "CZCE", "INE", "GFEX"),
                    allowed=("fut_type",),
                ),
                fetch_page_size=2000,
            ),
            DatasetSpec(
                dataset_id="fut_daily",
                domain="futures",
                method="fut_daily",
                key_fields=("ts_code", "trade_date"),
                symbol_field="ts_code",
                date_field="trade_date",
                batch_size=1000,
                request_planner=_futures_daily_planner,
                fetch_page_size=2000,
                max_fetch_pages=100_000,
            ),
            DatasetSpec(
                dataset_id="fut_mapping",
                domain="futures",
                method="fut_mapping",
                key_fields=("ts_code", "trade_date"),
                symbol_field="ts_code",
                date_field="trade_date",
                required=False,
                batch_size=1000,
                request_planner=_futures_mapping_planner,
                fetch_page_size=2000,
            ),
        )
    )


def _valid_day(value: Any) -> str:
    text = str(value or "").strip()
    return text if len(text) == 8 and text.isdigit() else ""


def _all_store_rows(store, dataset: str) -> list[dict[str, Any]]:
    if hasattr(store, "query_all_rows"):
        return store.query_all_rows(dataset, max_rows=100_000)
    rows: list[dict[str, Any]] = []
    offset = 0
    while offset < 100_000:
        page = store.query_rows(dataset, limit=MAX_QUERY_LIMIT, offset=offset)
        rows.extend(page)
        if len(page) < MAX_QUERY_LIMIT:
            return rows
        offset += len(page)
    raise ValueError(f"{dataset} exceeds the bounded futures metadata limit")


def _futures_daily_planner(request, state, store):
    """Use each contract's real lifetime instead of empty 1990-era windows."""

    exact_date = _valid_day(request.params.get("trade_date"))
    requested_symbol = _text(request.params.get("ts_code"))
    if exact_date:
        params = {"trade_date": exact_date}
        if requested_symbol:
            params["ts_code"] = requested_symbol
        return (FetchPage(cursor=f"trade_date={exact_date}", params=params),)

    rows = normalize_fut_basic(_all_store_rows(store, "fut_basic"))
    if requested_symbol:
        rows = [row for row in rows if row["ts_code"] == requested_symbol]
        if not rows:
            rows = [{"ts_code": requested_symbol, "active_from": "", "active_to": ""}]
    if not rows:
        raise ValueError("sync planning requires populated fut_basic metadata")

    today = date.today().strftime("%Y%m%d")
    supplied_start = _valid_day(request.params.get("start_date"))
    supplied_end = _valid_day(request.params.get("end_date"))
    resume_cursor = _valid_day((state or {}).get("cursor"))
    pages = []
    for row in sorted(rows, key=lambda item: item["ts_code"]):
        symbol = row["ts_code"]
        list_date = _valid_day(row.get("active_from") or row.get("list_date"))
        delist_date = _valid_day(row.get("active_to") or row.get("delist_date"))
        symbol_cursor = (
            _valid_day(store.max_data_date("fut_daily", symbol=symbol))
            if hasattr(store, "max_data_date")
            else ""
        )
        start_date = supplied_start or max(
            (
                value
                for value in (symbol_cursor, resume_cursor, list_date, "19900101")
                if value
            ),
            default="19900101",
        )
        end_date = supplied_end or min(
            (value for value in (delist_date, today) if value),
            default=today,
        )
        if start_date > end_date:
            continue
        pages.append(
            FetchPage(
                cursor=f"{symbol}:{start_date}-{end_date}",
                params={
                    "ts_code": symbol,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )
        )
    if not pages:
        raise ValueError("no futures contracts overlap the requested date range")
    return tuple(pages)


def _futures_mapping_planner(request, state, store):
    """Build continuous-symbol slices from synchronized contract metadata."""

    requested = str(request.params.get("ts_code") or "").strip().upper()
    if requested:
        symbols = (requested,)
    else:
        rows = _all_store_rows(store, "fut_basic")
        symbols = tuple(
            sorted(
                {
                    f"{str(row.get('fut_code') or '').strip().upper()}.{str(row.get('ts_code') or '').rsplit('.', 1)[-1].upper()}"
                    for row in rows
                    if str(row.get("fut_code") or "").strip()
                    and "." in str(row.get("ts_code") or "")
                }
            )
        )
    if not symbols:
        raise ValueError("sync planning requires populated fut_basic metadata")
    end_date = str(request.params.get("end_date") or date.today().strftime("%Y%m%d"))
    return tuple(
        FetchPage(
            cursor=f"{symbol}:{start}-{end_date}",
            params={"ts_code": symbol, "start_date": start, "end_date": end_date},
        )
        for symbol in symbols
        for start in (
            str(
                request.params.get("start_date")
                or (
                    store.max_data_date("fut_mapping", symbol=symbol)
                    if hasattr(store, "max_data_date")
                    else None
                )
                or "19900101"
            ),
        )
    )


def _records(rows: Any) -> list[dict[str, Any]]:
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    if isinstance(rows, Mapping):
        rows = [rows]
    if not isinstance(rows, Iterable) or isinstance(rows, (str, bytes)):
        raise TypeError("rows must be a DataFrame or iterable of mappings")
    result = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("each futures row must be a mapping")
        result.append({key: _clean(value) for key, value in row.items()})
    return result


def _clean(value: Any) -> Any:
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _text(value: Any) -> str:
    return str(value or "").strip().upper()


def _exchange(symbol: str, value: Any = None) -> str:
    explicit = _text(value)
    if explicit:
        return _EXCHANGE_BY_SUFFIX.get(explicit, explicit)
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    return _EXCHANGE_BY_SUFFIX.get(suffix, suffix)


def _product(symbol: str, value: Any = None) -> str:
    explicit = _text(value)
    if explicit:
        return explicit
    match = re.match(r"([A-Z]+)", symbol)
    return match.group(1) if match else ""


def _contract_month(symbol: str, reference_date: Any = None) -> str | None:
    base = symbol.split(".", 1)[0]
    match = re.search(r"(\d{3,6})$", base)
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 6:
        return digits
    if len(digits) == 4:
        return f"20{digits}"
    if len(digits) == 3:
        year_digit, month = int(digits[0]), digits[1:]
        reference_year = int(str(reference_date)[:4]) if str(reference_date or "")[:4].isdigit() else 2025
        year = min((2010 + year_digit, 2020 + year_digit, 2030 + year_digit), key=lambda item: abs(item - reference_year))
        return f"{year:04d}{month}"
    return None


def normalize_fut_basic(rows: Any) -> list[dict[str, Any]]:
    normalized = []
    for source in _records(rows):
        symbol = _text(source.get("ts_code") or source.get("symbol"))
        if not symbol:
            continue
        list_date = _text(source.get("list_date"))
        delist_date = _text(source.get("delist_date"))
        row = dict(source)
        row.update(
            {
                "ts_code": symbol,
                "symbol": symbol,
                "exchange": _exchange(symbol, source.get("exchange")),
                "product": _product(symbol, source.get("fut_code")),
                "contract_month": _contract_month(symbol, delist_date or list_date),
                "active_from": list_date,
                "active_to": delist_date,
            }
        )
        normalized.append(row)
    return normalized


def normalize_fut_daily(rows: Any) -> list[dict[str, Any]]:
    normalized = []
    for source in _records(rows):
        symbol = _text(source.get("ts_code") or source.get("symbol"))
        trade_date = _text(source.get("trade_date"))
        if not symbol or not trade_date:
            continue
        row = dict(source)
        row.update(
            {
                "ts_code": symbol,
                "symbol": symbol,
                "trade_date": trade_date,
                "exchange": _exchange(symbol, source.get("exchange")),
                "product": _product(symbol, source.get("fut_code")),
                "volume": source.get("volume", source.get("vol")),
            }
        )
        normalized.append(row)
    return normalized


def normalize_fut_mapping(rows: Any) -> list[dict[str, Any]]:
    normalized = []
    for source in _records(rows):
        continuous = _text(source.get("ts_code") or source.get("continuous_symbol"))
        contract = _text(source.get("mapping_ts_code") or source.get("contract_symbol"))
        trade_date = _text(source.get("trade_date"))
        if not continuous or not contract or not trade_date:
            continue
        row = dict(source)
        row.update(
            {
                "ts_code": continuous,
                "continuous_symbol": continuous,
                "mapping_ts_code": contract,
                "contract_symbol": contract,
                "trade_date": trade_date,
                "exchange": _exchange(continuous),
                "product": _product(continuous),
            }
        )
        normalized.append(row)
    return normalized


class FuturesService:
    """Bounded local futures search, mapping, and daily chart access."""

    def __init__(self, store) -> None:
        self.store = store

    @staticmethod
    def _page(limit: int, offset: int = 0) -> tuple[int, int]:
        try:
            limit, offset = int(limit), int(offset)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit and offset must be integers") from exc
        if limit < 1 or limit > MAX_QUERY_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_QUERY_LIMIT}")
        if offset < 0:
            raise ValueError("offset must be greater than or equal to 0")
        return limit, offset

    def contracts(
        self,
        *,
        query: str | None = None,
        exchange: str | None = None,
        product: str | None = None,
        active_on: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit, offset = self._page(limit, offset)
        needle = str(query or "").strip().casefold()
        exchange_key = _exchange("", exchange) if exchange else ""
        product_key = _text(product)
        active_date = _text(active_on)
        rows = normalize_fut_basic(_all_store_rows(self.store, "fut_basic"))
        items = []
        for row in rows:
            searchable = " ".join(
                str(row.get(key) or "") for key in ("symbol", "name", "product")
            ).casefold()
            if needle and needle not in searchable:
                continue
            if exchange_key and row["exchange"] != exchange_key:
                continue
            if product_key and row["product"] != product_key:
                continue
            if active_date and not (
                (not row["active_from"] or row["active_from"] <= active_date)
                and (not row["active_to"] or row["active_to"] >= active_date)
            ):
                continue
            items.append(row)
        items.sort(key=lambda row: (row.get("exchange") or "", row.get("product") or "", row.get("contract_month") or "", row["symbol"]))
        return {"items": items[offset : offset + limit], "total": len(items), "limit": limit, "offset": offset}

    def continuous_symbols(
        self,
        *,
        exchange: str | None = None,
        active_on: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit, offset = self._page(limit, offset)
        exchange_key = _exchange("", exchange) if exchange else ""
        active_date = _text(active_on)
        latest: dict[str, dict[str, Any]] = {}
        symbol_offset = 0
        while True:
            page = self.store.query_latest_rows(
                "fut_mapping",
                rows_per_symbol=1,
                end_date=active_date or None,
                limit=MAX_QUERY_LIMIT,
                offset=symbol_offset,
            )
            if not page:
                break
            for row in normalize_fut_mapping(page):
                if exchange_key and row["exchange"] != exchange_key:
                    continue
                latest[row["continuous_symbol"]] = row
            if len(page) < MAX_QUERY_LIMIT:
                break
            symbol_offset += len(page)
        items = [
            {
                "continuous_symbol": row["continuous_symbol"],
                "trade_date": row["trade_date"],
                "contract_symbol": row["contract_symbol"],
                "exchange": row["exchange"],
                "product": row["product"],
            }
            for row in sorted(latest.values(), key=lambda item: item["continuous_symbol"])
        ]
        return {"items": items[offset : offset + limit], "total": len(items), "limit": limit, "offset": offset}

    def kline(self, symbol: str, *, limit: int = 260) -> dict[str, Any]:
        limit, _ = self._page(limit)
        canonical = _text(symbol)
        if not canonical:
            raise ValueError("symbol is required")
        rows = normalize_fut_daily(
            self.store.query_rows("fut_daily", symbol=canonical, limit=limit)
        )
        rows.reverse()
        candles = [
            {
                key: row.get(key)
                for key in (
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "pre_close",
                    "pre_settle",
                    "settle",
                    "volume",
                    "amount",
                    "oi",
                    "oi_chg",
                )
            }
            for row in rows
        ]
        total = (
            self.store.count_rows("fut_daily", symbol=canonical)
            if hasattr(self.store, "count_rows")
            else len(candles)
        )
        return {"symbol": canonical, "candles": candles, "limit": limit, "total": total}


__all__ = [
    "FuturesService",
    "futures_catalog",
    "normalize_fut_basic",
    "normalize_fut_daily",
    "normalize_fut_mapping",
]
