"""Market-aware watchlist persistence with legacy A-share migration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from market_data.hong_kong import canonical_hk_symbol
from utils.atomic_io import atomic_write_json


WATCHLIST_VERSION = 2
SUPPORTED_MARKETS = frozenset({"a_share", "hong_kong"})


def canonical_equity_symbol(market: str, symbol: object) -> str:
    market_id = str(market or "").strip()
    if market_id not in SUPPORTED_MARKETS:
        raise ValueError(f"unsupported equity market: {market_id}")
    if market_id == "hong_kong":
        return canonical_hk_symbol(symbol)

    text = str(symbol or "").strip().upper()
    code = text.split(".", 1)[0]
    if len(code) != 6 or not code.isdigit():
        raise ValueError("A-share symbol must contain six digits")
    suffix = "BJ" if code.startswith(("4", "8")) else ("SH" if code.startswith("6") else "SZ")
    return f"{code}.{suffix}"


def watchlist_identity(market: str, symbol: object) -> str:
    market_id = str(market or "").strip()
    return f"{market_id}:{canonical_equity_symbol(market_id, symbol)}"


class MarketWatchlistStore:
    """Persist watchlist entries under the composite identity ``(market, symbol)``."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = RLock()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _normalized_payload(self) -> tuple[dict[str, Any], bool]:
        if not self.path.exists():
            return {"version": WATCHLIST_VERSION, "items": {}}, False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            return {"version": WATCHLIST_VERSION, "items": {}}, False

        source_items = raw.get("items") if isinstance(raw, dict) else {}
        if not isinstance(source_items, dict):
            source_items = {}
        normalized: dict[str, dict[str, Any]] = {}
        changed = raw.get("version") != WATCHLIST_VERSION
        for source_key, source_meta in source_items.items():
            meta = dict(source_meta) if isinstance(source_meta, dict) else {}
            market = str(meta.get("market") or "a_share").strip()
            candidate = meta.get("symbol") or meta.get("code") or source_key
            if ":" in str(source_key) and not meta.get("market"):
                possible_market, possible_symbol = str(source_key).split(":", 1)
                if possible_market in SUPPORTED_MARKETS:
                    market, candidate = possible_market, possible_symbol
            try:
                symbol = canonical_equity_symbol(market, candidate)
            except ValueError:
                changed = True
                continue
            identity = watchlist_identity(market, symbol)
            item = {
                **meta,
                "market": market,
                "symbol": symbol,
                "code": symbol.split(".", 1)[0] if market == "a_share" else symbol,
            }
            normalized[identity] = item
            changed = changed or identity != source_key or item != meta
        return {"version": WATCHLIST_VERSION, "items": normalized}, changed

    def _save(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.path, payload)

    def list_all(self, market: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            payload, changed = self._normalized_payload()
            if changed:
                self._save(payload)
            rows = list(payload["items"].values())
            if market is not None:
                market_id = str(market or "").strip()
                if market_id not in SUPPORTED_MARKETS:
                    raise ValueError(f"unsupported equity market: {market_id}")
                rows = [row for row in rows if row["market"] == market_id]
            return sorted(
                (dict(row) for row in rows),
                key=lambda row: (str(row.get("created_at") or ""), row["market"], row["symbol"]),
            )

    def add(
        self,
        market: str,
        symbol: object,
        *,
        name: str = "",
        note: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            market_id = str(market or "").strip()
            canonical = canonical_equity_symbol(market_id, symbol)
            identity = watchlist_identity(market_id, canonical)
            payload, _changed = self._normalized_payload()
            existing = dict(payload["items"].get(identity) or {})
            now = self._now()
            item = {
                **existing,
                **dict(metadata or {}),
                "market": market_id,
                "symbol": canonical,
                "code": canonical.split(".", 1)[0] if market_id == "a_share" else canonical,
                "name": str(name or existing.get("name") or "").strip(),
                "note": str(note if note != "" else existing.get("note") or ""),
                "created_at": existing.get("created_at") or now,
                "updated_at": now,
            }
            payload["items"][identity] = item
            self._save(payload)
            return dict(item)

    def remove(self, market: str, symbol: object) -> bool:
        with self._lock:
            identity = watchlist_identity(market, symbol)
            payload, _changed = self._normalized_payload()
            removed = payload["items"].pop(identity, None) is not None
            self._save(payload)
            return removed


__all__ = [
    "MarketWatchlistStore",
    "WATCHLIST_VERSION",
    "canonical_equity_symbol",
    "watchlist_identity",
]
