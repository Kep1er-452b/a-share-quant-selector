"""Market-isolated equity policy and reader adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

import pandas as pd

from market_data.hong_kong import canonical_hk_symbol
from utils.strategy_labels import is_invalid_stock_name


@runtime_checkable
class EquityReader(Protocol):
    def list_instruments(self, query: str = "", limit: int = 50, offset: int = 0) -> dict: ...
    def read_analysis_frame(self, symbol: str): ...
    def instrument_metadata(self, symbol: str) -> dict: ...


@dataclass(frozen=True)
class EquityDisplayRules:
    currency: str
    price_limit_model: str | None
    st_filter: bool
    board_model: str | None
    listing_limit_model: str | None


@dataclass(frozen=True)
class StrategyCapabilities:
    supported_scopes: tuple[str, ...]
    parameter_overrides: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameter_overrides",
            MappingProxyType({key: MappingProxyType(dict(value)) for key, value in self.parameter_overrides.items()}),
        )


class _UnconfiguredReader:
    def list_instruments(self, query="", limit=50, offset=0):
        raise RuntimeError("equity reader is not configured")

    def read_analysis_frame(self, symbol):
        raise RuntimeError("equity reader is not configured")

    def instrument_metadata(self, symbol):
        raise RuntimeError("equity reader is not configured")


@dataclass(frozen=True)
class EquityPolicy:
    market_id: str
    reader: EquityReader
    strategy_capabilities: StrategyCapabilities
    display_rules: EquityDisplayRules

    def is_instrument_allowed(self, instrument: Mapping[str, Any]) -> bool:
        symbol = str(instrument.get("symbol") or instrument.get("ts_code") or "").strip()
        if not symbol:
            return False
        if self.market_id == "a_share":
            return not is_invalid_stock_name(instrument.get("name"), missing_name_is_invalid=False)
        return True

    @staticmethod
    def strategy_scope(strategy: str) -> str:
        compact = "".join(character for character in str(strategy or "").upper() if character.isalnum())
        if compact.startswith(("B1", "B2", "BOWL")):
            return "a_share_only"
        return "a_share_only"

    def is_strategy_allowed(self, strategy: str, *, declared_scope: str | None = None) -> bool:
        scope = str(declared_scope or self.strategy_scope(strategy)).strip()
        return scope in self.strategy_capabilities.supported_scopes


def _canonical_a_share_symbol(value: object) -> tuple[str, str]:
    text = str(value or "").strip().upper()
    code = text.split(".", 1)[0]
    if len(code) != 6 or not code.isdigit():
        raise ValueError("A-share symbol must contain six digits")
    suffix = "BJ" if code.startswith(("4", "8")) else ("SH" if code.startswith("6") else "SZ")
    return code, f"{code}.{suffix}"


class AShareEquityReader:
    def __init__(self, csv_manager, metadata: Mapping[str, Mapping[str, Any]] | Any = None):
        self.csv_manager = csv_manager
        self.metadata_source = metadata or {}

    def _metadata(self, code: str) -> dict:
        source = self.metadata_source(code) if callable(self.metadata_source) else self.metadata_source.get(code, {})
        return dict(source or {})

    def list_instruments(self, query="", limit=50, offset=0) -> dict:
        needle = str(query or "").strip().casefold()
        items = []
        for code in self.csv_manager.list_all_stocks():
            code, symbol = _canonical_a_share_symbol(code)
            item = {"symbol": symbol, "code": code, **self._metadata(code)}
            if not needle or needle in code.casefold() or needle in str(item.get("name") or "").casefold():
                items.append(item)
        total = len(items)
        return {"items": items[offset : offset + limit], "total": total, "limit": limit, "offset": offset}

    def read_analysis_frame(self, symbol: str):
        code, _canonical = _canonical_a_share_symbol(symbol)
        return self.csv_manager.read_stock_for_analysis(code)

    def instrument_metadata(self, symbol: str) -> dict:
        code, canonical = _canonical_a_share_symbol(symbol)
        return {"symbol": canonical, "code": code, "currency": "CNY", **self._metadata(code)}


class HongKongEquityReader:
    def __init__(self, service):
        self.service = service

    def list_instruments(self, query="", limit=50, offset=0) -> dict:
        return self.service.search(query, limit, offset)

    def read_analysis_frame(self, symbol: str):
        payload = self.service.kline(canonical_hk_symbol(symbol), limit=1000, adjustment="raw")
        return pd.DataFrame(payload.get("items") or [])

    def instrument_metadata(self, symbol: str) -> dict:
        canonical = canonical_hk_symbol(symbol)
        page = self.service.search(canonical, 1, 0)
        metadata = dict((page.get("items") or [{}])[0])
        return {**metadata, "symbol": canonical, "currency": "HKD"}


def equity_policy(market_id: str, *, reader: EquityReader | None = None) -> EquityPolicy:
    market = str(market_id or "").strip()
    if market == "a_share":
        return EquityPolicy(
            market_id=market,
            reader=reader or _UnconfiguredReader(),
            strategy_capabilities=StrategyCapabilities(("a_share_only", "market_neutral")),
            display_rules=EquityDisplayRules(
                currency="CNY",
                price_limit_model="a_share_board_rules",
                st_filter=True,
                board_model="a_share_boards",
                listing_limit_model="a_share_ipo_window",
            ),
        )
    if market == "hong_kong":
        return EquityPolicy(
            market_id=market,
            reader=reader or _UnconfiguredReader(),
            strategy_capabilities=StrategyCapabilities(("hong_kong", "market_neutral")),
            display_rules=EquityDisplayRules(
                currency="HKD",
                price_limit_model=None,
                st_filter=False,
                board_model=None,
                listing_limit_model=None,
            ),
        )
    raise KeyError(f"unknown equity market: {market}")


__all__ = [
    "AShareEquityReader",
    "EquityDisplayRules",
    "EquityPolicy",
    "EquityReader",
    "HongKongEquityReader",
    "StrategyCapabilities",
    "equity_policy",
]
