"""Market-explicit equity routes with injected service ownership."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Blueprint, jsonify, request

from market_data.capabilities import market_capabilities
from market_data.equity_policy import equity_policy


def _value(record, name, default=None):
    return record.get(name, default) if isinstance(record, Mapping) else getattr(record, name, default)


def create_equities_blueprint(*, services: Mapping[str, Any], capabilities=None) -> Blueprint:
    service_map = dict(services or {})
    capability_map = dict(capabilities or market_capabilities())
    blueprint = Blueprint("equities_api", __name__, url_prefix="/api/equities")

    def error(code, message, status, *, market=None, capability=None):
        payload = {"code": code, "error": message}
        if market is not None:
            payload["market"] = market
        if capability is not None:
            payload["capability"] = capability
        return jsonify(payload), status

    def context(market, payload):
        capability = capability_map[market]
        base = dict(payload) if isinstance(payload, Mapping) else {"data": payload}
        base["market"] = market
        base["currency"] = _value(capability, "currency")
        return base

    def resolve(market, page, method, *, capability_name=None):
        capability = capability_map.get(market)
        if capability is None:
            return None, error("UNKNOWN_MARKET", "unknown equity market", 404, market=market)
        if page not in tuple(_value(capability, "pages", ())):
            return None, error(
                "CAPABILITY_UNAVAILABLE", "market capability is unavailable", 501,
                market=market, capability=page,
            )
        service = service_map.get(market)
        callback = getattr(service, method, None) if service is not None else None
        if not callable(callback):
            return None, error(
                "CAPABILITY_UNAVAILABLE", "market adapter is not implemented", 501,
                market=market, capability=capability_name or method,
            )
        return callback, None

    def integer(name, default, maximum):
        try:
            value = int(request.args.get(name, default))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if value < (1 if name == "limit" else 0) or value > maximum:
            raise ValueError(f"{name} is outside the supported range")
        return value

    def invoke(
        market,
        page,
        method,
        *args,
        status=200,
        capability_name=None,
        **kwargs,
    ):
        callback, failure = resolve(
            market, page, method, capability_name=capability_name
        )
        if failure is not None:
            return failure
        try:
            payload = callback(*args, **kwargs)
        except (KeyError, TypeError, ValueError) as exc:
            return error("INVALID_REQUEST", str(exc), 400, market=market)
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code is None:
                raise
            payload = {
                "code": getattr(exc, "error_code", "TASK_CONFLICT"),
                "error": str(exc),
                "market": market,
            }
            job = getattr(exc, "job", None)
            if job:
                payload["job_id"] = job.get("job_id")
                payload["data"] = job
            return jsonify(payload), int(status_code)
        return jsonify(context(market, payload)), status

    @blueprint.get("/<market>/overview")
    def overview(market):
        return invoke(market, "overview", "overview")

    @blueprint.get("/<market>/instruments")
    def instruments(market):
        try:
            limit, offset = integer("limit", 50, 200), integer("offset", 0, 100_000)
        except ValueError as exc:
            return error("INVALID_REQUEST", str(exc), 400, market=market)
        return invoke(market, "instruments", "list_instruments", request.args.get("q", ""), limit, offset)

    @blueprint.get("/<market>/instrument/<path:symbol>")
    def instrument_detail(market, symbol):
        try:
            limit = integer("limit", 260, 2_000)
        except ValueError as exc:
            return error("INVALID_REQUEST", str(exc), 400, market=market)
        return invoke(
            market, "instruments", "instrument_detail", symbol,
            limit=limit, adjustment=request.args.get("adjustment", "raw"),
        )

    @blueprint.get("/<market>/heatmap")
    def heatmap(market):
        return invoke(market, "heatmap", "heatmap")

    @blueprint.get("/<market>/selection/options")
    def selection_options(market):
        callback, failure = resolve(market, "selection", "selection_options")
        if failure is not None:
            return failure
        try:
            payload = dict(callback())
            policy = equity_policy(market)
            payload["strategies"] = [
                item for item in payload.get("strategies", [])
                if policy.is_strategy_allowed(item.get("name", ""), declared_scope=item.get("scope"))
            ]
        except (KeyError, TypeError, ValueError) as exc:
            return error("INVALID_REQUEST", str(exc), 400, market=market)
        return jsonify(context(market, payload))

    @blueprint.post("/<market>/selection/start")
    def selection_start(market):
        service = service_map.get(market)
        policy = None
        try:
            policy = equity_policy(market)
        except KeyError:
            return error("UNKNOWN_MARKET", "unknown equity market", 404, market=market)
        payload = request.get_json(silent=True) or {}
        strategies = payload.get("strategies") or []
        if not isinstance(strategies, list) or any(not isinstance(item, str) for item in strategies):
            return error("INVALID_REQUEST", "strategies must be a string list", 400, market=market)
        scope_resolver = getattr(service, "strategy_scope", None)
        rejected = [
            item for item in strategies
            if not policy.is_strategy_allowed(
                item, declared_scope=scope_resolver(item) if callable(scope_resolver) else None
            )
        ]
        if rejected:
            return error(
                "STRATEGY_NOT_SUPPORTED", "strategy is not supported for this market", 400,
                market=market, capability=rejected[0],
            )
        return invoke(market, "selection", "start_selection", payload, status=202)

    @blueprint.get("/<market>/selection/status/<job_id>")
    def selection_status(market, job_id):
        return invoke(market, "selection", "selection_status", job_id)

    @blueprint.get("/<market>/watchlist")
    def watchlist(market):
        return invoke(market, "watchlist", "watchlist")

    @blueprint.post("/<market>/watchlist")
    def watchlist_update(market):
        return invoke(
            market,
            "watchlist",
            "update_watchlist",
            request.get_json(silent=True) or {},
            capability_name="watchlist_update",
        )

    @blueprint.post("/<market>/wyckoff/start")
    def wyckoff_start(market):
        return invoke(
            market,
            "wyckoff",
            "start_wyckoff",
            request.get_json(silent=True) or {},
            status=202,
            capability_name="wyckoff_start",
        )

    @blueprint.get("/<market>/wyckoff/status/<job_id>")
    def wyckoff_status(market, job_id):
        return invoke(market, "wyckoff", "wyckoff_status", job_id)

    return blueprint


__all__ = ["create_equities_blueprint"]
