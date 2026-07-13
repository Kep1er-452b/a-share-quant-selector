"""Server-owned market capability records.

The frontend consumes these records instead of inferring exchange policy from
symbols.  That keeps A-share-only rules from leaking into Hong Kong workflows.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class MarketCapability:
    market_id: str
    display_name: str
    currency: str
    policy: str
    pages: tuple[str, ...]
    features: tuple[str, ...]
    grouping_modes: tuple[str, ...]
    strategy_support: tuple[str, ...]
    adjustment_support: tuple[str, ...]
    health: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("market_id")
        return payload


def market_capabilities(
    store_health: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, MarketCapability]:
    health = store_health or {}
    shared_pages = (
        "overview",
        "heatmap",
        "instruments",
        "selection",
        "strategies",
        "watchlist",
        "wyckoff",
    )
    return {
        "a_share": MarketCapability(
            market_id="a_share",
            display_name="A 股",
            currency="CNY",
            policy="a_share",
            pages=shared_pages,
            features=("price_limits", "st_filter", "northbound", "margin", "industry"),
            grouping_modes=("industry", "board", "performance_distribution"),
            strategy_support=("a_share", "market_neutral"),
            adjustment_support=("raw", "qfq", "hfq"),
            health=dict(health.get("a_share") or {"status": "ready"}),
        ),
        "hong_kong": MarketCapability(
            market_id="hong_kong",
            display_name="港股",
            currency="HKD",
            policy="hong_kong",
            pages=shared_pages,
            features=("market_segment", "performance_distribution"),
            grouping_modes=("market_segment", "performance_distribution"),
            strategy_support=("hong_kong", "market_neutral"),
            adjustment_support=("raw",),
            health=dict(health.get("hong_kong") or {"status": "empty"}),
        ),
    }


def capability_payload(
    store_health: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    records = market_capabilities(store_health)
    return {
        "default_market": "a_share",
        "markets": {market_id: record.to_dict() for market_id, record in records.items()},
    }
