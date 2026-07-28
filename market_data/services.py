"""Bounded read services for the independent market-domain stores."""

from __future__ import annotations

from collections import Counter
import math
from typing import Any

from market_data.hong_kong import (
    CURRENCY,
    DOMAIN,
    canonical_date,
    canonical_hk_symbol,
    normalize_hk_daily,
)


class HongKongService:
    """Build Hong Kong read models without applying A-share policy rules."""

    MAX_PAGE_LIMIT = 200
    MAX_KLINE_LIMIT = 1000
    STORE_PAGE_SIZE = 2000

    def __init__(self, store) -> None:
        self.store = store

    def _all_rows(self, dataset: str, *, symbol: str | None = None) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            page = self.store.query_rows(
                dataset,
                symbol=symbol,
                limit=self.STORE_PAGE_SIZE,
                offset=offset,
            )
            rows.extend(page)
            if len(page) < self.STORE_PAGE_SIZE:
                return rows
            offset += len(page)

    def _latest_rows(
        self, dataset: str, *, rows_per_symbol: int = 1
    ) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            page = self.store.query_latest_rows(
                dataset,
                rows_per_symbol=rows_per_symbol,
                limit=self.STORE_PAGE_SIZE,
                offset=offset,
            )
            rows.extend(page)
            symbol_count = len(
                {
                    str(row.get("ts_code") or row.get("symbol") or "")
                    for row in page
                    if row.get("ts_code") or row.get("symbol")
                }
            )
            if symbol_count < self.STORE_PAGE_SIZE:
                return rows
            offset += symbol_count

    @classmethod
    def _page_args(cls, limit: object, offset: object) -> tuple[int, int]:
        try:
            page_limit = int(limit)
            page_offset = int(offset)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit and offset must be integers") from exc
        if page_limit < 1 or page_limit > cls.MAX_PAGE_LIMIT:
            raise ValueError(f"limit must be between 1 and {cls.MAX_PAGE_LIMIT}")
        if page_offset < 0:
            raise ValueError("offset must be greater than or equal to 0")
        return page_limit, page_offset

    @staticmethod
    def _basic_row(source: dict[str, Any]) -> dict[str, Any]:
        row = dict(source)
        symbol = canonical_hk_symbol(row.get("ts_code") or row.get("symbol"))
        row.update({"ts_code": symbol, "symbol": symbol, "currency": CURRENCY})
        return row

    def _basic_rows(self):
        for source in self._all_rows("hk_basic"):
            try:
                yield self._basic_row(source)
            except (TypeError, ValueError):
                continue

    def search(self, query: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        page_limit, page_offset = self._page_args(limit, offset)
        needle = str(query or "").strip().casefold()
        compact_needle = needle.replace(".hk", "").lstrip("0") or needle
        items = []
        for row in self._basic_rows():
            values = (
                row.get("symbol"),
                str(row.get("symbol") or "").replace(".HK", "").lstrip("0"),
                row.get("name"),
                row.get("fullname"),
                row.get("enname"),
                row.get("cn_spell"),
            )
            if not needle or any(
                needle in str(value or "").casefold()
                or compact_needle in str(value or "").casefold()
                for value in values
            ):
                items.append(row)
        items.sort(key=lambda row: row["symbol"])
        total = len(items)
        return {
            "items": items[page_offset : page_offset + page_limit],
            "total": total,
            "limit": page_limit,
            "offset": page_offset,
        }

    def kline(
        self, symbol: str, limit: int = 260, adjustment: str = "raw"
    ) -> dict[str, Any]:
        canonical_symbol = canonical_hk_symbol(symbol)
        try:
            chart_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer") from exc
        if chart_limit < 1 or chart_limit > self.MAX_KLINE_LIMIT:
            raise ValueError(f"limit must be between 1 and {self.MAX_KLINE_LIMIT}")
        requested = str(adjustment or "raw").strip().lower()
        if requested not in {"raw", "qfq", "hfq"}:
            raise ValueError("adjustment must be raw, qfq, or hfq")

        raw_desc = self.store.query_rows(
            "hk_daily", symbol=canonical_symbol, limit=chart_limit + 1
        )
        context = raw_desc[chart_limit] if len(raw_desc) > chart_limit else None
        raw_items = list(reversed(raw_desc[:chart_limit]))
        normalized = normalize_hk_daily(raw_items)

        resolved = requested
        fallback = False
        if requested != "raw" and normalized:
            factors = self.store.query_rows(
                "hk_adjfactor", symbol=canonical_symbol, limit=chart_limit
            )
            factor_map = {
                str(row.get("trade_date")): row.get("cum_adjfactor")
                for row in factors
                if row.get("trade_date") and row.get("cum_adjfactor") is not None
            }
            dates = [row["trade_date"] for row in normalized]
            if len(factor_map) < len(dates) or any(date not in factor_map for date in dates):
                resolved = "raw"
                fallback = True
            else:
                try:
                    latest_factor = float(factor_map[dates[-1]])
                    resolved_factors = [
                        float(factor_map[row["trade_date"]]) for row in normalized
                    ]
                except (TypeError, ValueError):
                    latest_factor = math.nan
                    resolved_factors = []
                if (
                    not math.isfinite(latest_factor)
                    or latest_factor <= 0
                    or any(not math.isfinite(value) or value <= 0 for value in resolved_factors)
                ):
                    resolved = "raw"
                    fallback = True
                else:
                    for row, factor in zip(normalized, resolved_factors):
                        multiplier = factor / latest_factor if requested == "qfq" else factor
                        for field in ("open", "high", "low", "close", "pre_close"):
                            if row.get(field) is not None:
                                row[field] = float(row[field]) * multiplier

        previous_close = float(context["close"]) if context and context.get("close") else None
        for row in normalized:
            reference = row.get("pre_close")
            if reference is None:
                reference = previous_close
            close = row.get("close")
            row["change_pct"] = (
                (float(close) - float(reference)) / float(reference) * 100.0
                if close is not None and reference not in (None, 0, 0.0)
                else None
            )
            previous_close = float(close) if close is not None else previous_close

        return {
            "symbol": canonical_symbol,
            "currency": CURRENCY,
            "requested_adjustment": requested,
            "adjustment": resolved,
            "adjustment_fallback": fallback,
            "items": normalized,
        }

    def finance(self, symbol: str) -> dict[str, Any]:
        canonical_symbol = canonical_hk_symbol(symbol)
        datasets = {
            "income": "hk_income",
            "balancesheet": "hk_balancesheet",
            "cashflow": "hk_cashflow",
            "indicators": "hk_fina_indicator",
        }
        payload: dict[str, Any] = {"symbol": canonical_symbol, "currency": CURRENCY}
        for output_name, dataset in datasets.items():
            normalized = []
            for source in self._all_rows(dataset, symbol=canonical_symbol):
                row = dict(source)
                row.update({"symbol": canonical_symbol, "currency": CURRENCY})
                if row.get("end_date"):
                    _, row["period"] = canonical_date(row["end_date"])
                normalized.append(row)
            payload[output_name] = normalized
        return payload

    def overview(self) -> dict[str, Any]:
        instruments = list(self._basic_rows())
        segment_counts = Counter(
            str(row.get("market") or "").strip()
            for row in instruments
            if str(row.get("market") or "").strip()
        )

        histories: dict[str, list[dict]] = {}
        for row in self._latest_rows("hk_daily", rows_per_symbol=2):
            symbol = canonical_hk_symbol(row.get("ts_code"))
            histories.setdefault(symbol, []).append(row)
        advancers = decliners = unchanged = 0
        for rows in histories.values():
            rows.sort(key=lambda row: str(row.get("trade_date") or ""), reverse=True)
            row = rows[0]
            close = row.get("close")
            pre_close = row.get("pre_close")
            if close is None:
                continue
            if pre_close in (None, 0, 0.0) and len(rows) > 1:
                pre_close = rows[1].get("close")
            if pre_close in (None, 0, 0.0):
                continue
            change = float(close) - float(pre_close)
            if change > 0:
                advancers += 1
            elif change < 0:
                decliners += 1
            else:
                unchanged += 1

        return {
            "currency": CURRENCY,
            "instrument_count": len(instruments),
            "grouping_mode": (
                "market_segment" if segment_counts else "performance_distribution"
            ),
            "industry_coverage": 0,
            "industry_mapped": 0,
            "market_segments": [
                {"name": name, "count": count}
                for name, count in sorted(segment_counts.items())
            ],
            "performance": {
                "advancers": advancers,
                "decliners": decliners,
                "unchanged": unchanged,
            },
        }

    def heatmap(self) -> dict[str, Any]:
        """Return real segment/performance groups without claiming HK industry data."""

        basics = {
            row["symbol"]: row
            for row in self._basic_rows()
        }
        latest: dict[str, dict[str, Any]] = {}
        for source in self._latest_rows("hk_daily", rows_per_symbol=1):
            symbol = canonical_hk_symbol(source.get("ts_code") or source.get("symbol"))
            latest[symbol] = dict(source)

        items = []
        for symbol, source in latest.items():
            basic = basics.get(symbol, {})
            close = source.get("close")
            pre_close = source.get("pre_close")
            change_pct = None
            if close is not None and pre_close not in (None, 0, 0.0):
                change_pct = (float(close) - float(pre_close)) / float(pre_close) * 100.0
            bucket = (
                "上涨" if change_pct is not None and change_pct > 0
                else "下跌" if change_pct is not None and change_pct < 0
                else "平盘"
            )
            items.append(
                {
                    "market": DOMAIN,
                    "symbol": symbol,
                    "name": str(basic.get("name") or symbol),
                    "currency": CURRENCY,
                    "segment": str(basic.get("market") or "").strip(),
                    "trade_date": source.get("trade_date"),
                    "close": float(close) if close is not None else None,
                    "change_pct": change_pct,
                    "performance_bucket": bucket,
                    "volume": source.get("vol") if source.get("vol") is not None else source.get("volume"),
                }
            )

        use_segments = any(item["segment"] for item in items)
        grouping_mode = "market_segment" if use_segments else "performance_distribution"
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            group = item["segment"] if use_segments and item["segment"] else item["performance_bucket"]
            grouped.setdefault(group or "未标注板块", []).append(item)
        groups = []
        for name, members in grouped.items():
            members.sort(
                key=lambda item: (
                    item["change_pct"] is None,
                    -(item["change_pct"] or 0.0),
                    item["symbol"],
                )
            )
            groups.append(
                {
                    "name": name,
                    "grouping": grouping_mode,
                    "count": len(members),
                    "items": members,
                }
            )
        groups.sort(key=lambda group: (-group["count"], group["name"]))
        return {
            "market": DOMAIN,
            "currency": CURRENCY,
            "grouping_mode": grouping_mode,
            "industry_coverage": 0,
            "groups": groups,
            "total": len(items),
        }


from market_data.futures import FuturesService
from market_data.industry import IndustryService
from market_data.economy import EconomyService


__all__ = ["EconomyService", "FuturesService", "HongKongService", "IndustryService"]
