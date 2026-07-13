"""Cancellation-aware lifecycle runner for registry-backed datasets."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from market_data.catalog import DatasetCatalog
from market_data.models import DatasetSpec, DatasetSyncResult, FetchPage, SyncRequest, SyncResult


@dataclass(frozen=True)
class _WriteOutcome:
    rows_written: int
    cancelled: bool = False


class _WriteFailure(RuntimeError):
    def __init__(self, cause: Exception, rows_written: int):
        super().__init__(str(cause) or cause.__class__.__name__)
        self.cause = cause
        self.rows_written = rows_written


@dataclass(frozen=True)
class _PlannedOutcome:
    result: DatasetSyncResult
    terminal_status: str | None = None


class SyncEngine:
    """Fetch registered datasets and persist rows through a DomainStore-like object."""

    def __init__(
        self,
        *,
        catalog: DatasetCatalog,
        store,
        client,
        cache_refresher: Callable[[DatasetSpec, SyncRequest, list[dict]], None] | None = None,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.client = client
        self.cache_refresher = cache_refresher

    def run(
        self,
        request: SyncRequest,
        *,
        cancel_event,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> SyncResult:
        emitter = self._safe_emitter(emit)
        results: dict[str, DatasetSyncResult] = {}
        warnings: list[str] = []
        total_written = 0
        self._emit(emitter, request, phase="preflight", status="running")

        try:
            specs = self._resolve_specs(request)
        except Exception as exc:
            return self._terminal(
                emitter, request, "failed", results, total_written, warnings, self._message(exc)
            )
        self._emit(
            emitter,
            request,
            phase="plan",
            status="completed",
            datasets=[spec.dataset_id for spec in specs],
        )

        if cancel_event.is_set():
            return self._terminal(emitter, request, "cancelled", results, total_written, warnings)

        for spec in specs:
            if cancel_event.is_set():
                return self._terminal(
                    emitter, request, "cancelled", results, total_written, warnings
                )

            try:
                state = self.store.get_sync_state(spec.dataset_id, request.scope)
            except Exception as exc:
                message = self._message(exc)
                results[spec.dataset_id] = DatasetSyncResult(
                    spec.dataset_id, "failed", error=message
                )
                return self._terminal(
                    emitter, request, "failed", results, total_written, warnings, message
                )

            if self._is_fresh(spec, state, request):
                cursor = (state or {}).get("cursor")
                for phase in ("fetch", "normalize", "write"):
                    self._emit(
                        emitter,
                        request,
                        phase=phase,
                        status="skipped",
                        dataset=spec.dataset_id,
                        reason="fresh",
                    )
                self._emit(
                    emitter,
                    request,
                    phase="cache_refresh",
                    status="skipped",
                    dataset=spec.dataset_id,
                    reason="no_new_rows",
                )
                self._emit(
                    emitter,
                    request,
                    phase="quality",
                    status="completed",
                    dataset=spec.dataset_id,
                    row_count=0,
                )
                results[spec.dataset_id] = DatasetSyncResult(
                    spec.dataset_id, "fresh", cursor=cursor
                )
                continue

            if spec.request_planner is not None:
                planned = self._run_planned_dataset(
                    spec,
                    request,
                    state,
                    cancel_event,
                    emitter,
                )
                issue = planned.result
                results[spec.dataset_id] = issue
                total_written += issue.rows_written
                if issue.status == "warning":
                    warnings.append(issue.warning or "planned dataset warning")
                    continue
                if planned.terminal_status is not None:
                    return self._terminal(
                        emitter,
                        request,
                        planned.terminal_status,
                        results,
                        total_written,
                        warnings,
                        issue.error,
                    )
                continue

            try:
                params = self._parameters(spec, request, state)
                self._emit(
                    emitter,
                    request,
                    phase="fetch",
                    status="running",
                    dataset=spec.dataset_id,
                )
                payload = getattr(self.client, spec.method)(**params)
                if cancel_event.is_set():
                    return self._terminal(
                        emitter, request, "cancelled", results, total_written, warnings
                    )
                self._emit(
                    emitter,
                    request,
                    phase="normalize",
                    status="running",
                    dataset=spec.dataset_id,
                )
                rows, explicit_cursor = self._normalize_payload(payload)
            except Exception as exc:
                issue = self._record_endpoint_issue(spec, request, state, exc)
                results[spec.dataset_id] = issue
                if issue.status == "warning":
                    warnings.append(issue.warning or self._message(exc))
                    self._emit(
                        emitter,
                        request,
                        phase="quality",
                        status="warning",
                        dataset=spec.dataset_id,
                        warning=issue.warning,
                    )
                    continue
                return self._terminal(
                    emitter,
                    request,
                    "failed",
                    results,
                    total_written,
                    warnings,
                    issue.error,
                )

            if cancel_event.is_set():
                return self._terminal(
                    emitter, request, "cancelled", results, total_written, warnings
                )

            self._emit(
                emitter,
                request,
                phase="write",
                status="running",
                dataset=spec.dataset_id,
                row_count=len(rows),
            )
            try:
                write = self._write_rows(spec, rows, cancel_event)
            except _WriteFailure as exc:
                total_written += exc.rows_written
                message = self._message(exc.cause)
                state_error = self._safe_set_state(
                    spec,
                    request,
                    status="failed",
                    cursor=(state or {}).get("cursor"),
                    error=message,
                    row_count=exc.rows_written,
                )
                if state_error:
                    message = f"{message}; sync state write failed: {state_error}"
                results[spec.dataset_id] = DatasetSyncResult(
                    spec.dataset_id,
                    "failed",
                    rows_written=exc.rows_written,
                    error=message,
                )
                return self._terminal(
                    emitter, request, "failed", results, total_written, warnings, message
                )

            total_written += write.rows_written
            if write.cancelled or cancel_event.is_set():
                state_error = self._safe_set_state(
                    spec,
                    request,
                    status="cancelled",
                    cursor=(state or {}).get("cursor"),
                    row_count=write.rows_written,
                )
                status = "failed" if state_error else "cancelled"
                message = (
                    f"sync state write failed: {state_error}" if state_error else None
                )
                results[spec.dataset_id] = DatasetSyncResult(
                    spec.dataset_id,
                    status,
                    rows_written=write.rows_written,
                    error=message,
                )
                return self._terminal(
                    emitter, request, status, results, total_written, warnings, message
                )

            cursor = self._next_cursor(spec, rows, explicit_cursor, state)
            if self.cache_refresher is None:
                self._emit(
                    emitter,
                    request,
                    phase="cache_refresh",
                    status="skipped",
                    dataset=spec.dataset_id,
                    reason="no_registered_cache",
                )
            else:
                self._emit(
                    emitter,
                    request,
                    phase="cache_refresh",
                    status="running",
                    dataset=spec.dataset_id,
                )
                try:
                    self.cache_refresher(spec, request, rows)
                except Exception as exc:
                    message = self._message(exc)
                    self._emit(
                        emitter,
                        request,
                        phase="cache_refresh",
                        status="failed",
                        dataset=spec.dataset_id,
                        error=message,
                    )
                    state_error = self._safe_set_state(
                        spec,
                        request,
                        status="failed",
                        cursor=(state or {}).get("cursor"),
                        error=message,
                        row_count=write.rows_written,
                    )
                    if state_error:
                        message = f"{message}; sync state write failed: {state_error}"
                    results[spec.dataset_id] = DatasetSyncResult(
                        spec.dataset_id,
                        "failed",
                        rows_written=write.rows_written,
                        error=message,
                    )
                    return self._terminal(
                        emitter, request, "failed", results, total_written, warnings, message
                    )
                self._emit(
                    emitter,
                    request,
                    phase="cache_refresh",
                    status="completed",
                    dataset=spec.dataset_id,
                )
            if cancel_event.is_set():
                state_error = self._safe_set_state(
                    spec,
                    request,
                    status="cancelled",
                    cursor=(state or {}).get("cursor"),
                    row_count=write.rows_written,
                )
                message = f"sync state write failed: {state_error}" if state_error else None
                status = "failed" if state_error else "cancelled"
                results[spec.dataset_id] = DatasetSyncResult(
                    spec.dataset_id,
                    status,
                    rows_written=write.rows_written,
                    error=message,
                )
                return self._terminal(
                    emitter, request, status, results, total_written, warnings, message
                )
            self._emit(
                emitter,
                request,
                phase="quality",
                status="completed",
                dataset=spec.dataset_id,
                row_count=write.rows_written,
            )
            state_error = self._safe_set_state(
                spec,
                request,
                status="completed",
                cursor=cursor,
                row_count=write.rows_written,
            )
            if state_error:
                message = f"sync state write failed: {state_error}"
                results[spec.dataset_id] = DatasetSyncResult(
                    spec.dataset_id,
                    "failed",
                    rows_written=write.rows_written,
                    cursor=cursor,
                    error=message,
                )
                return self._terminal(
                    emitter, request, "failed", results, total_written, warnings, message
                )
            results[spec.dataset_id] = DatasetSyncResult(
                spec.dataset_id,
                "completed",
                rows_written=write.rows_written,
                cursor=cursor,
            )

        if cancel_event.is_set():
            return self._terminal(
                emitter, request, "cancelled", results, total_written, warnings
            )
        status = "completed_with_warnings" if warnings else "completed"
        return self._terminal(emitter, request, status, results, total_written, warnings)

    def _run_planned_dataset(
        self,
        spec: DatasetSpec,
        request: SyncRequest,
        state: Mapping[str, Any] | None,
        cancel_event,
        emitter,
    ) -> _PlannedOutcome:
        """Execute explicit resumable request slices without retaining all rows."""

        try:
            raw_pages = tuple(spec.request_planner(request, state, self.store))
            if len(raw_pages) > spec.max_fetch_pages:
                raise ValueError(
                    f"sync plan for {spec.dataset_id} exceeds {spec.max_fetch_pages} pages"
                )
            pages = tuple(self._fetch_page(item) for item in raw_pages)
            if not pages:
                raise ValueError(f"sync plan for {spec.dataset_id} is empty")
            if any(not page.params for page in pages):
                raise ValueError(
                    f"full sync plan for {spec.dataset_id} requires explicit parameters"
                )
        except Exception as exc:
            issue = self._record_endpoint_issue(spec, request, state, exc)
            return _PlannedOutcome(
                issue,
                None if issue.status == "warning" else "failed",
            )

        resume_cursor, resume_offset, resume_complete = self._resume_position(state)
        start_index = 0
        if resume_cursor:
            matched = next(
                (index for index, page in enumerate(pages) if page.cursor == resume_cursor),
                None,
            )
            if matched is not None:
                start_index = matched + 1 if resume_complete else matched

        data_cursor = (state or {}).get("cursor")
        rows_written = 0
        completed_pages = start_index
        last_rows: list[dict[str, Any]] = []
        fetch_calls = 0

        for page_index in range(start_index, len(pages)):
            page = pages[page_index]
            offset = resume_offset if page_index == start_index else 0
            while True:
                if cancel_event.is_set():
                    return self._planned_cancelled(
                        spec,
                        request,
                        rows_written,
                        data_cursor,
                        page.cursor,
                        offset,
                        False,
                    )
                params = dict(page.params)
                if spec.fetch_page_size is not None:
                    params.update(limit=spec.fetch_page_size, offset=offset)
                if fetch_calls >= spec.max_fetch_pages:
                    return self._planned_failure(
                        spec,
                        request,
                        state,
                        RuntimeError(
                            f"fetch page limit exceeded for {spec.dataset_id}: "
                            f"{spec.max_fetch_pages}"
                        ),
                        rows_written,
                        data_cursor,
                        page.cursor,
                        offset,
                    )
                fetch_calls += 1
                self._emit(
                    emitter,
                    request,
                    phase="fetch",
                    status="running",
                    dataset=spec.dataset_id,
                    plan_cursor=page.cursor,
                    offset=offset,
                )
                try:
                    payload = getattr(self.client, spec.method)(**params)
                    if cancel_event.is_set():
                        return self._planned_cancelled(
                            spec,
                            request,
                            rows_written,
                            data_cursor,
                            page.cursor,
                            offset,
                            False,
                        )
                    self._emit(
                        emitter,
                        request,
                        phase="normalize",
                        status="running",
                        dataset=spec.dataset_id,
                        plan_cursor=page.cursor,
                        offset=offset,
                    )
                    rows, explicit_cursor = self._normalize_payload(payload)
                except Exception as exc:
                    return self._planned_failure(
                        spec,
                        request,
                        state,
                        exc,
                        rows_written,
                        data_cursor,
                        page.cursor,
                        offset,
                    )

                self._emit(
                    emitter,
                    request,
                    phase="write",
                    status="running",
                    dataset=spec.dataset_id,
                    plan_cursor=page.cursor,
                    offset=offset,
                    row_count=len(rows),
                )
                try:
                    write = self._write_rows(spec, rows, cancel_event)
                except _WriteFailure as exc:
                    rows_written += exc.rows_written
                    return self._planned_failure(
                        spec,
                        request,
                        state,
                        exc.cause,
                        rows_written,
                        data_cursor,
                        page.cursor,
                        offset,
                    )
                rows_written += write.rows_written
                data_cursor = self._next_cursor(
                    spec,
                    rows,
                    explicit_cursor,
                    {"cursor": data_cursor},
                )
                last_rows = rows
                page_complete = (
                    spec.fetch_page_size is None
                    or len(rows) < spec.fetch_page_size
                )
                next_offset = 0 if page_complete else offset + spec.fetch_page_size
                state_error = self._safe_set_state(
                    spec,
                    request,
                    status="running",
                    cursor=data_cursor,
                    row_count=rows_written,
                    details={
                        "method": spec.method,
                        "plan_cursor": page.cursor,
                        "page_offset": next_offset,
                        "page_complete": page_complete and not write.cancelled,
                    },
                )
                if state_error:
                    message = f"sync state write failed: {state_error}"
                    return _PlannedOutcome(
                        DatasetSyncResult(
                            spec.dataset_id,
                            "failed",
                            rows_written=rows_written,
                            cursor=data_cursor,
                            error=message,
                        ),
                        "failed",
                    )
                if write.cancelled or cancel_event.is_set():
                    return self._planned_cancelled(
                        spec,
                        request,
                        rows_written,
                        data_cursor,
                        page.cursor,
                        offset,
                        False,
                    )
                if page_complete:
                    completed_pages = page_index + 1
                    break
                offset = next_offset

        if self.cache_refresher is None:
            self._emit(
                emitter,
                request,
                phase="cache_refresh",
                status="skipped",
                dataset=spec.dataset_id,
                reason="no_registered_cache",
            )
        else:
            self._emit(
                emitter,
                request,
                phase="cache_refresh",
                status="running",
                dataset=spec.dataset_id,
            )
            try:
                self.cache_refresher(spec, request, last_rows)
            except Exception as exc:
                return self._planned_failure(
                    spec,
                    request,
                    state,
                    exc,
                    rows_written,
                    data_cursor,
                    pages[-1].cursor,
                    0,
                )
            self._emit(
                emitter,
                request,
                phase="cache_refresh",
                status="completed",
                dataset=spec.dataset_id,
            )

        self._emit(
            emitter,
            request,
            phase="quality",
            status="completed",
            dataset=spec.dataset_id,
            row_count=rows_written,
        )
        state_error = self._safe_set_state(
            spec,
            request,
            status="completed",
            cursor=data_cursor,
            row_count=rows_written,
            details={"method": spec.method, "completed_plan_pages": completed_pages},
        )
        if state_error:
            message = f"sync state write failed: {state_error}"
            return _PlannedOutcome(
                DatasetSyncResult(
                    spec.dataset_id,
                    "failed",
                    rows_written=rows_written,
                    cursor=data_cursor,
                    error=message,
                ),
                "failed",
            )
        return _PlannedOutcome(
            DatasetSyncResult(
                spec.dataset_id,
                "completed",
                rows_written=rows_written,
                cursor=data_cursor,
            )
        )

    @staticmethod
    def _fetch_page(value) -> FetchPage:
        if not isinstance(value, FetchPage):
            raise TypeError("request_planner must yield FetchPage values")
        return value

    @staticmethod
    def _resume_position(state) -> tuple[str | None, int, bool]:
        if not state or state.get("status") not in {
            "running",
            "failed",
            "warning",
            "cancelled",
        }:
            return None, 0, False
        details = state.get("details") or {}
        try:
            offset = max(0, int(details.get("page_offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        return (
            str(details.get("plan_cursor") or "").strip() or None,
            offset,
            bool(details.get("page_complete")),
        )

    def _planned_cancelled(
        self,
        spec,
        request,
        rows_written,
        cursor,
        plan_cursor,
        page_offset,
        page_complete,
    ) -> _PlannedOutcome:
        state_error = self._safe_set_state(
            spec,
            request,
            status="cancelled",
            cursor=cursor,
            row_count=rows_written,
            details={
                "method": spec.method,
                "plan_cursor": plan_cursor,
                "page_offset": page_offset,
                "page_complete": bool(page_complete),
            },
        )
        message = f"sync state write failed: {state_error}" if state_error else None
        status = "failed" if state_error else "cancelled"
        return _PlannedOutcome(
            DatasetSyncResult(
                spec.dataset_id,
                status,
                rows_written=rows_written,
                cursor=cursor,
                error=message,
            ),
            status,
        )

    def _planned_failure(
        self,
        spec,
        request,
        state,
        exc,
        rows_written,
        cursor,
        plan_cursor,
        page_offset,
    ) -> _PlannedOutcome:
        message = self._message(exc)
        status = "failed" if spec.required else "warning"
        warning = None if spec.required else f"{spec.dataset_id}: {message}"
        state_error = self._safe_set_state(
            spec,
            request,
            status=status,
            cursor=cursor or (state or {}).get("cursor"),
            error=message if spec.required else None,
            warning=warning,
            row_count=rows_written,
            details={
                "method": spec.method,
                "plan_cursor": plan_cursor,
                "page_offset": page_offset,
                "page_complete": False,
            },
        )
        if state_error:
            message = f"{message}; sync state write failed: {state_error}"
            status = "failed"
            warning = None
        return _PlannedOutcome(
            DatasetSyncResult(
                spec.dataset_id,
                status,
                rows_written=rows_written,
                cursor=cursor,
                warning=warning,
                error=message if status == "failed" else None,
            ),
            None if status == "warning" else "failed",
        )

    def _record_endpoint_issue(self, spec, request, state, exc) -> DatasetSyncResult:
        message = self._message(exc)
        if spec.required:
            state_error = self._safe_set_state(
                spec,
                request,
                status="failed",
                cursor=(state or {}).get("cursor"),
                error=message,
                row_count=0,
            )
            if state_error:
                message = f"{message}; sync state write failed: {state_error}"
            return DatasetSyncResult(spec.dataset_id, "failed", error=message)

        warning = f"{spec.dataset_id}: {message}"
        state_error = self._safe_set_state(
            spec,
            request,
            status="warning",
            cursor=(state or {}).get("cursor"),
            warning=warning,
            row_count=0,
        )
        if state_error:
            message = f"sync state write failed: {state_error}"
            return DatasetSyncResult(spec.dataset_id, "failed", error=message)
        return DatasetSyncResult(spec.dataset_id, "warning", warning=warning)

    def _safe_set_state(self, spec, request, **values) -> str | None:
        try:
            details = values.pop("details", {"method": spec.method})
            self.store.set_sync_state(
                spec.dataset_id,
                scope=request.scope,
                details=details,
                **values,
            )
            return None
        except Exception as exc:
            return self._message(exc)

    def _resolve_specs(self, request: SyncRequest) -> tuple[DatasetSpec, ...]:
        specs = (
            tuple(self.catalog.get(dataset_id) for dataset_id in request.datasets)
            if request.datasets
            else self.catalog.for_domain(request.domain)
        )
        for spec in specs:
            if spec.domain != request.domain:
                raise ValueError(
                    f"dataset {spec.dataset_id} does not belong to domain {request.domain}"
                )
        return specs

    @staticmethod
    def _parameters(spec, request, state) -> dict[str, Any]:
        if spec.parameter_builder is None:
            return dict(request.params)
        params = spec.parameter_builder(request, state)
        if not isinstance(params, Mapping):
            raise ValueError(f"parameter_builder for {spec.dataset_id} must return a mapping")
        return dict(params)

    @staticmethod
    def _normalize_payload(payload) -> tuple[list[dict[str, Any]], Any]:
        explicit_cursor = None
        if payload is None:
            return [], None
        if isinstance(payload, Mapping) and "rows" in payload:
            explicit_cursor = payload.get("cursor")
            payload = payload.get("rows") or []
        elif hasattr(payload, "to_dict"):
            if bool(getattr(payload, "empty", False)):
                return [], None
            payload = payload.to_dict("records")
        elif isinstance(payload, Mapping):
            payload = [payload]
        if not isinstance(payload, Iterable) or isinstance(payload, (str, bytes)):
            raise ValueError("provider result must be rows, a DataFrame, or a rows payload")
        rows = []
        for row in payload:
            if not isinstance(row, Mapping):
                raise ValueError("provider rows must be mappings")
            rows.append(dict(row))
        return rows, explicit_cursor

    def _write_rows(self, spec, rows, cancel_event) -> _WriteOutcome:
        written = 0
        for start in range(0, len(rows), spec.batch_size):
            if cancel_event.is_set():
                return _WriteOutcome(written, cancelled=True)
            batch = rows[start : start + spec.batch_size]
            try:
                written += int(
                    self.store.upsert_rows(
                        spec.dataset_id,
                        batch,
                        key_fields=spec.key_fields,
                        symbol_field=spec.symbol_field,
                        date_field=spec.date_field,
                    )
                    or 0
                )
            except Exception as exc:
                raise _WriteFailure(exc, written) from exc
            if cancel_event.is_set():
                return _WriteOutcome(written, cancelled=True)
        return _WriteOutcome(written)

    @staticmethod
    def _next_cursor(spec, rows, explicit_cursor, state):
        if explicit_cursor is not None:
            return explicit_cursor
        if spec.date_field:
            values = [row.get(spec.date_field) for row in rows if row.get(spec.date_field) is not None]
            if values:
                return max(values, key=lambda value: str(value))
        return (state or {}).get("cursor")

    @staticmethod
    def _is_fresh(
        spec: DatasetSpec,
        state: Mapping[str, Any] | None,
        request: SyncRequest,
    ) -> bool:
        if request.force or request.params:
            return False
        if spec.freshness is None or not state or state.get("status") != "completed":
            return False
        updated_at = state.get("updated_at")
        if not updated_at:
            return False
        try:
            updated = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return False
        freshness = (
            spec.freshness
            if isinstance(spec.freshness, timedelta)
            else timedelta(seconds=float(spec.freshness))
        )
        return datetime.now(timezone.utc) - updated <= freshness

    @staticmethod
    def _safe_emitter(emit):
        if emit is None:
            return lambda _event: None

        def safe(event):
            try:
                emit(event)
            except Exception:
                return None

        return safe

    @staticmethod
    def _message(exc: Exception) -> str:
        return str(exc) or exc.__class__.__name__

    @staticmethod
    def _emit(emitter, request, *, phase, status, **details) -> None:
        emitter(
            {
                "phase": phase,
                "status": status,
                "domain": request.domain,
                "scope": request.scope,
                **details,
            }
        )

    def _terminal(
        self,
        emitter,
        request,
        status,
        results,
        rows_written,
        warnings,
        error=None,
    ) -> SyncResult:
        self._emit(
            emitter,
            request,
            phase="terminal",
            status=status,
            row_count=rows_written,
            warning_count=len(warnings),
            error=error,
        )
        return SyncResult(
            domain=request.domain,
            status=status,
            datasets=results,
            rows_written=rows_written,
            warnings=tuple(warnings),
            error=error,
        )
