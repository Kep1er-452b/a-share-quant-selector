"""Market-aware watchlist persistence with legacy A-share migration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from market_data.hong_kong import canonical_hk_symbol
from market_data.equity_symbols import canonical_a_share_symbol
from utils.atomic_io import atomic_write_json


WATCHLIST_VERSION = 2
SUPPORTED_MARKETS = frozenset({"a_share", "hong_kong"})


class WatchlistStorageError(RuntimeError):
    """The watchlist cannot be read or does not satisfy its storage schema."""


def canonical_equity_symbol(market: str, symbol: object) -> str:
    market_id = str(market or "").strip()
    if market_id not in SUPPORTED_MARKETS:
        raise ValueError(f"unsupported equity market: {market_id}")
    if market_id == "hong_kong":
        return canonical_hk_symbol(symbol)
    return canonical_a_share_symbol(symbol)


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
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WatchlistStorageError(
                f"自选列表读取失败，未修改原文件: {self.path}"
            ) from exc

        if not isinstance(raw, dict):
            raise WatchlistStorageError(
                f"自选列表格式损坏（根节点必须是对象），未修改原文件: {self.path}"
            )

        source_items = raw.get("items")
        if not isinstance(source_items, dict):
            raise WatchlistStorageError(
                f"自选列表格式损坏（items 必须是对象），未修改原文件: {self.path}"
            )
        normalized: dict[str, dict[str, Any]] = {}
        changed = raw.get("version") != WATCHLIST_VERSION
        for source_key, source_meta in source_items.items():
            if not isinstance(source_meta, dict):
                raise WatchlistStorageError(
                    f"自选列表格式损坏（条目 {source_key!r} 必须是对象），未修改原文件: {self.path}"
                )
            meta = dict(source_meta)
            market = str(meta.get("market") or "a_share").strip()
            candidate = meta.get("symbol") or meta.get("code") or source_key
            if ":" in str(source_key) and not meta.get("market"):
                possible_market, possible_symbol = str(source_key).split(":", 1)
                if possible_market in SUPPORTED_MARKETS:
                    market, candidate = possible_market, possible_symbol
            try:
                symbol = canonical_equity_symbol(market, candidate)
            except ValueError as exc:
                raise WatchlistStorageError(
                    f"自选列表格式损坏（条目 {source_key!r} 身份无效），未修改原文件: {self.path}"
                ) from exc
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
    "WatchlistStorageError",
    "canonical_equity_symbol",
    "watchlist_identity",
]
