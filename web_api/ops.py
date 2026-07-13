"""Dependency-injected operations APIs, independent of the legacy Web module."""

from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import re

from flask import Blueprint, jsonify, request


_EVENT_FILTERS = {
    "severity", "domain", "market", "module", "job_id", "dataset", "symbol",
    "error_code", "event_type", "since", "until",
}
_TASK_PART = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class OpsServiceContainer:
    tasks: object
    events: object
    health: object
    performance: object
    diagnostics: object
    task_cancellers: dict[str, object] = field(default_factory=dict)


def create_ops_blueprint(
    services: OpsServiceContainer,
    *,
    session_token: str | None = None,
    token_validator=None,
) -> Blueprint:
    blueprint = Blueprint("ops_api", __name__, url_prefix="/api/ops")

    def integer(name, default, *, minimum=0, maximum=500):
        try:
            value = int(request.args.get(name, default))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} is outside the supported range")
        return value

    def call(callback):
        try:
            return jsonify(callback())
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc), "code": "INVALID_REQUEST"}), 400

    def authorized():
        supplied = request.headers.get("X-Quant-Session", "")
        if token_validator is not None:
            try:
                return bool(token_validator(supplied))
            except Exception:
                return False
        return bool(session_token) and hmac.compare_digest(supplied, str(session_token))

    @blueprint.get("/tasks")
    def tasks():
        allowed = {"limit", "offset", "status", "task_type"}
        unknown = set(request.args) - allowed
        if unknown:
            return jsonify({"error": f"unknown task filters: {sorted(unknown)}"}), 400
        return call(lambda: services.tasks.snapshot(
            limit=integer("limit", 200, minimum=1),
            offset=integer("offset", 0),
            status=request.args.get("status"),
            task_type=request.args.get("task_type"),
        ))

    @blueprint.get("/events")
    def events():
        unknown = set(request.args) - (_EVENT_FILTERS | {"limit", "offset"})
        if unknown:
            return jsonify({"error": f"unknown event filters: {sorted(unknown)}"}), 400

        def payload():
            filters = {key: request.args[key] for key in _EVENT_FILTERS if key in request.args}
            return services.events.query(
                limit=integer("limit", 100, minimum=1),
                offset=integer("offset", 0),
                **filters,
            )

        return call(payload)

    @blueprint.get("/health")
    def health():
        return call(services.health.snapshot)

    @blueprint.get("/performance")
    def performance():
        return call(services.performance.summary)

    @blueprint.post("/tasks/<task_type>/<job_id>/cancel")
    def task_cancel(task_type, job_id):
        if not authorized():
            return jsonify({"error": "invalid local session token"}), 403
        if not _TASK_PART.fullmatch(task_type) or not _TASK_PART.fullmatch(job_id):
            return jsonify({"error": "invalid task type or job ID", "code": "INVALID_REQUEST"}), 400
        canceller = services.task_cancellers.get(task_type)
        if not callable(canceller):
            return jsonify({"error": "task type does not support cancellation", "code": "UNSUPPORTED_TASK_ACTION"}), 400
        try:
            result = canceller(job_id)
        except KeyError:
            return jsonify({"error": "unknown job", "code": "UNKNOWN_JOB"}), 404
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc), "code": "INVALID_REQUEST"}), 400
        if isinstance(result, (dict, list)):
            return jsonify(result)
        return result

    @blueprint.post("/diagnostics/export")
    def diagnostics_export():
        if not authorized():
            return jsonify({"error": "invalid local session token"}), 403
        raw_payload = request.get_json(silent=True)
        payload = {} if raw_payload is None else raw_payload
        if not isinstance(payload, dict):
            return jsonify({"error": "JSON object required"}), 400

        def export_payload():
            path = services.diagnostics.export(
                job_ids=payload.get("job_ids") or [],
                event_filters=payload.get("event_filters") or {},
                limit=payload.get("limit", 200),
            )
            return {
                "export_id": path.stem,
                "filename": path.name,
                "size_bytes": path.stat().st_size,
            }

        return call(export_payload)

    return blueprint
