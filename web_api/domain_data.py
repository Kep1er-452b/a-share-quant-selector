"""Bounded read routes for independent local market-domain stores."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from functools import lru_cache
import json
import os
from threading import Event, Lock
import uuid

from flask import Blueprint, jsonify, request

from market_data.services import EconomyService, FuturesService, HongKongService, IndustryService
from market_data.economy import economy_catalog
from market_data.futures import futures_catalog
from market_data.hong_kong import hong_kong_catalog
from market_data.industry import industry_catalog
from market_data.models import SyncRequest
from market_data.store import DomainStore
from market_data.sync_engine import SyncEngine
from market_data.tushare_client import TushareClientFactory
from market_data.tushare_client import classify_provider_error
from utils.local_config import load_config_file
from utils.platform_paths import runtime_paths


domain_data_blueprint = Blueprint("domain_data_api", __name__, url_prefix="/api")
_DOMAIN_FILES = {
    "hong_kong": "hong_kong.sqlite",
    "futures": "futures.sqlite",
    "macro": "economy.sqlite",
    "industry": "industry.sqlite",
}
_CATALOGS = {
    "hong_kong": hong_kong_catalog,
    "futures": futures_catalog,
    "macro": economy_catalog,
    "industry": industry_catalog,
}
_SYNC_JOBS: dict[str, dict] = {}
_SYNC_CANCEL: dict[str, Event] = {}
_SYNC_LOCK = Lock()
_MAX_ACTIVE_SYNC_JOBS = 2
_SYNC_EXECUTOR = ThreadPoolExecutor(
    max_workers=_MAX_ACTIVE_SYNC_JOBS,
    thread_name_prefix="aqs-domain-sync",
)
_ACTIVE_SYNC_DOMAINS: dict[str, str] = {}
_MAX_TERMINAL_SYNC_JOBS = 50
_TERMINAL_SYNC_STATES = {"completed", "completed_with_warnings", "failed", "error", "cancelled"}
_SYNC_EVENT_OBSERVER = None


def configure_sync_event_observer(observer=None) -> None:
    """Install one best-effort operations observer without coupling this API to ops."""
    global _SYNC_EVENT_OBSERVER
    if observer is not None and not callable(observer):
        raise TypeError("sync event observer must be callable")
    _SYNC_EVENT_OBSERVER = observer


def _observe_sync_event(job_id: str, domain: str, event: dict) -> None:
    observer = _SYNC_EVENT_OBSERVER
    if observer is None:
        return
    try:
        observer(job_id, domain, dict(event))
    except Exception:
        pass


def _prune_sync_jobs_locked() -> None:
    terminal_ids = [
        job_id
        for job_id, job in _SYNC_JOBS.items()
        if job.get("status") in _TERMINAL_SYNC_STATES
    ]
    for job_id in terminal_ids[:-_MAX_TERMINAL_SYNC_JOBS]:
        _SYNC_JOBS.pop(job_id, None)
        _SYNC_CANCEL.pop(job_id, None)


@lru_cache(maxsize=4)
def _store(domain: str) -> DomainStore:
    if domain not in _DOMAIN_FILES:
        raise KeyError(domain)
    return DomainStore(runtime_paths().domain_store_path(domain))


def _service(domain: str):
    store = _store(domain)
    return {
        "hong_kong": HongKongService,
        "futures": FuturesService,
        "macro": EconomyService,
        "industry": IndustryService,
    }[domain](store)


def domain_service(domain: str):
    """Public lazy service accessor used by the application composition root."""
    if domain not in _DOMAIN_FILES:
        raise KeyError(domain)
    return _service(domain)


def sync_jobs_snapshot() -> dict[str, dict]:
    with _SYNC_LOCK:
        return {job_id: dict(job) for job_id, job in _SYNC_JOBS.items()}


def _integer(name: str, default: int, maximum: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0 or value > maximum or (name == "limit" and value == 0):
        raise ValueError(f"{name} is outside the supported range")
    return value


def _call(callback):
    try:
        return jsonify(callback())
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc), "code": "INVALID_REQUEST"}), 400


@domain_data_blueprint.get("/domain-status/<domain>")
def domain_status(domain: str):
    if domain not in _DOMAIN_FILES:
        return jsonify({"error": "unknown domain"}), 404
    store = _store(domain)
    health = store.health()
    catalog = _CATALOGS[domain]()
    with store.connect() as conn:
        row_stats = {
            row["dataset"]: dict(row)
            for row in conn.execute(
                """
                SELECT dataset,
                       COUNT(*) AS row_count,
                       MAX(data_date) AS max_data_date,
                       MAX(updated_at) AS max_updated_at
                FROM dataset_rows
                GROUP BY dataset
                """
            ).fetchall()
        }
        sync_states = {}
        for row in conn.execute(
            """
            SELECT dataset, scope, status, cursor, start_date, end_date,
                   warning, error, row_count, details_json, updated_at
            FROM sync_state
            ORDER BY dataset ASC, updated_at DESC, scope ASC
            """
        ).fetchall():
            if row["dataset"] in sync_states:
                continue
            state = dict(row)
            details_json = state.pop("details_json")
            state["details"] = json.loads(details_json) if details_json else None
            sync_states[row["dataset"]] = state

    datasets = {}
    required_populated = []
    any_rows = False
    for spec in catalog:
        stats = row_stats.get(spec.dataset_id) or {}
        row_count = int(stats.get("row_count") or 0)
        any_rows = any_rows or row_count > 0
        if spec.required:
            required_populated.append(row_count > 0)
        datasets[spec.dataset_id] = {
            "row_count": row_count,
            "max_data_date": stats.get("max_data_date"),
            "max_updated_at": stats.get("max_updated_at"),
            "required": spec.required,
            "sync_state": sync_states.get(spec.dataset_id),
        }
    state = (
        "empty"
        if not any_rows
        else "ready"
        if required_populated and all(required_populated)
        else "partial"
    )
    return jsonify({
        "domain": domain,
        "state": state,
        "datasets": datasets,
        "schema_version": health["schema_version"],
        "storage_bytes": health["db_size_bytes"],
        "integrity": health["integrity"],
    })


@domain_data_blueprint.get("/hong-kong/instruments")
def hong_kong_instruments():
    return _call(lambda: _service("hong_kong").search(
        request.args.get("q", ""), _integer("limit", 50, 200), _integer("offset", 0, 100000)
    ))


@domain_data_blueprint.get("/hong-kong/kline/<path:symbol>")
def hong_kong_kline(symbol: str):
    return _call(lambda: _service("hong_kong").kline(
        symbol, _integer("limit", 260, 1000), request.args.get("adjustment", "raw")
    ))


@domain_data_blueprint.get("/hong-kong/overview")
def hong_kong_overview():
    return _call(lambda: _service("hong_kong").overview())


@domain_data_blueprint.get("/futures/contracts")
def futures_contracts():
    return _call(lambda: _service("futures").contracts(
        query=request.args.get("q"), exchange=request.args.get("exchange"),
        product=request.args.get("product"), active_on=request.args.get("active_on"),
        limit=_integer("limit", 100, 2000), offset=_integer("offset", 0, 100000),
    ))


@domain_data_blueprint.get("/futures/kline/<path:symbol>")
def futures_kline(symbol: str):
    return _call(lambda: _service("futures").kline(symbol, limit=_integer("limit", 260, 2000)))


@domain_data_blueprint.get("/macro/catalog")
def macro_catalog():
    return _call(lambda: {"items": _service("macro").series_catalog(request.args.get("family"))})


@domain_data_blueprint.get("/macro/series")
def macro_series():
    return _call(lambda: _service("macro").series(
        request.args.getlist("series_id"), request.args.get("start"), request.args.get("end"),
        _integer("max_points", 120, 2000),
    ))


@domain_data_blueprint.get("/industry/classifications")
def industry_classifications():
    return _call(lambda: _service("industry").classifications(request.args.get("level")))


@domain_data_blueprint.get("/industry/detail/<path:industry_id>")
def industry_detail(industry_id: str):
    return _call(lambda: _service("industry").detail(industry_id, candle_limit=_integer("limit", 520, 2000)))


@domain_data_blueprint.get("/industry/cycle-series")
def industry_cycle_series():
    return _call(lambda: _service("industry").cycle_series(
        request.args.getlist("series_id"), normalize=request.args.get("normalize") == "1"
    ))


@domain_data_blueprint.post("/sync/start")
def sync_start():
    payload = request.get_json(silent=True) or {}
    domain = str(payload.get("domain") or "").strip()
    if domain not in _CATALOGS:
        return jsonify({"error": "unknown domain", "code": "INVALID_DOMAIN"}), 400
    datasets = payload.get("datasets") or ()
    if not isinstance(datasets, (list, tuple)) or any(not isinstance(item, str) for item in datasets):
        return jsonify({"error": "datasets must be a string list", "code": "INVALID_DATASETS"}), 400
    factory = TushareClientFactory.from_config(load_config_file(), os.environ)
    if not factory.token_present:
        return jsonify({
            "error": "未找到本机 Tushare Token；请设置 TUSHARE_TOKEN 或 config/config_local.yaml",
            "code": "TOKEN_MISSING",
        }), 400
    try:
        client = factory.client()
        sync_request = SyncRequest(
            domain=domain,
            datasets=tuple(datasets),
            scope=str(payload.get("scope") or "default"),
            params=payload.get("params") or {},
            force=bool(payload.get("force")),
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc), "code": "INVALID_REQUEST"}), 400

    job_id = uuid.uuid4().hex
    job = {"job_id": job_id, "domain": domain, "status": "queued", "events": [], "result": None}
    cancel = Event()
    with _SYNC_LOCK:
        _prune_sync_jobs_locked()
        active_job_id = _ACTIVE_SYNC_DOMAINS.get(domain)
        if active_job_id is not None:
            return jsonify({
                "error": "a synchronization job is already active for this domain",
                "code": "DOMAIN_SYNC_ACTIVE",
                "domain": domain,
                "job_id": active_job_id,
            }), 409
        if len(_ACTIVE_SYNC_DOMAINS) >= _MAX_ACTIVE_SYNC_JOBS:
            return jsonify({
                "error": "the bounded domain synchronization budget is exhausted",
                "code": "SYNC_BUDGET_EXHAUSTED",
                "active": len(_ACTIVE_SYNC_DOMAINS),
                "limit": _MAX_ACTIVE_SYNC_JOBS,
            }), 429
        _SYNC_JOBS[job_id] = job
        _SYNC_CANCEL[job_id] = cancel
        _ACTIVE_SYNC_DOMAINS[domain] = job_id
    try:
        _SYNC_EXECUTOR.submit(
            _run_sync_job,
            job_id,
            sync_request,
            _CATALOGS[domain](),
            _store(domain),
            client,
            cancel,
        )
    except Exception as exc:
        with _SYNC_LOCK:
            _SYNC_JOBS.pop(job_id, None)
            _SYNC_CANCEL.pop(job_id, None)
            if _ACTIVE_SYNC_DOMAINS.get(domain) == job_id:
                _ACTIVE_SYNC_DOMAINS.pop(domain, None)
        return jsonify({"error": str(exc), "code": "SYNC_SUBMIT_FAILED"}), 503
    return jsonify({"job_id": job_id, "status": "queued"}), 202


def _run_sync_job(job_id, sync_request, catalog, store, client, cancel):
    domain = sync_request.domain
    try:
        with _SYNC_LOCK:
            _SYNC_JOBS[job_id]["status"] = "running"

        def emit(event):
            with _SYNC_LOCK:
                events = _SYNC_JOBS[job_id]["events"]
                events.append(dict(event))
                del events[:-200]
            _observe_sync_event(job_id, domain, dict(event))

        result = SyncEngine(catalog=catalog, store=store, client=client).run(
            sync_request, cancel_event=cancel, emit=emit
        )
        with _SYNC_LOCK:
            _SYNC_JOBS[job_id]["status"] = result.status
            _SYNC_JOBS[job_id]["result"] = {
                "status": result.status,
                "rows_written": result.rows_written,
                "warnings": list(result.warnings),
                "error": result.error,
                "error_code": result.error_code,
                "retryable": result.retryable,
                "datasets": {
                    dataset_id: asdict(dataset_result)
                    for dataset_id, dataset_result in result.datasets.items()
                },
            }
    except Exception as exc:
        issue = classify_provider_error(exc)
        with _SYNC_LOCK:
            job = _SYNC_JOBS.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["result"] = {
                    "status": "error",
                    "rows_written": 0,
                    "warnings": [],
                    "error": issue.message or exc.__class__.__name__,
                    "error_code": issue.code,
                    "retryable": issue.retryable,
                    "datasets": {},
                }
        _observe_sync_event(job_id, domain, {
            "phase": "terminal",
            "status": "error",
            "rows_written": 0,
            "error": issue.message or exc.__class__.__name__,
            "error_code": issue.code,
            "retryable": issue.retryable,
        })
    finally:
        with _SYNC_LOCK:
            _SYNC_CANCEL.pop(job_id, None)
            if _ACTIVE_SYNC_DOMAINS.get(domain) == job_id:
                _ACTIVE_SYNC_DOMAINS.pop(domain, None)
            _prune_sync_jobs_locked()


@domain_data_blueprint.get("/sync/status/<job_id>")
def sync_status(job_id: str):
    with _SYNC_LOCK:
        job = _SYNC_JOBS.get(job_id)
        if job is None:
            return jsonify({"error": "unknown job"}), 404
        return jsonify(dict(job))


@domain_data_blueprint.post("/sync/cancel/<job_id>")
def sync_cancel(job_id: str):
    with _SYNC_LOCK:
        cancel = _SYNC_CANCEL.get(job_id)
        if cancel is None:
            return jsonify({"error": "unknown job"}), 404
        cancel.set()
    return jsonify({"job_id": job_id, "status": "cancelling"})
