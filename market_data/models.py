"""Immutable contracts shared by the multi-market data services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping


ParameterBuilder = Callable[["SyncRequest", Mapping[str, Any] | None], Mapping[str, Any]]
RequestPlanner = Callable[
    ["SyncRequest", Mapping[str, Any] | None, Any], Iterable["FetchPage"]
]


@dataclass(frozen=True)
class FetchPage:
    """One stable, explicitly-parameterized provider request in a sync plan."""

    cursor: str
    params: Mapping[str, Any]

    def __post_init__(self) -> None:
        cursor = str(self.cursor or "").strip()
        if not cursor:
            raise ValueError("fetch page cursor must not be empty")
        object.__setattr__(self, "cursor", cursor)
        object.__setattr__(self, "params", MappingProxyType(dict(self.params or {})))


@dataclass(frozen=True)
class DatasetSpec:
    """Describe one provider dataset and its local storage contract."""

    dataset_id: str
    domain: str
    method: str
    key_fields: tuple[str, ...]
    symbol_field: str | None = None
    date_field: str | None = None
    required: bool = True
    batch_size: int = 1000
    freshness: timedelta | int | float | None = None
    parameter_builder: ParameterBuilder | None = None
    request_planner: RequestPlanner | None = None
    fetch_page_size: int | None = None
    max_fetch_pages: int = 10_000

    def __post_init__(self) -> None:
        for field_name in ("dataset_id", "domain", "method"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, value)

        key_fields = tuple(str(field).strip() for field in self.key_fields if str(field).strip())
        if not key_fields:
            raise ValueError("key_fields must contain at least one field")
        if len(key_fields) != len(set(key_fields)):
            raise ValueError("key_fields must not contain duplicates")
        object.__setattr__(self, "key_fields", key_fields)

        if self.symbol_field is not None:
            symbol_field = str(self.symbol_field).strip()
            object.__setattr__(self, "symbol_field", symbol_field or None)
        if self.date_field is not None:
            date_field = str(self.date_field).strip()
            object.__setattr__(self, "date_field", date_field or None)
        if int(self.batch_size) <= 0:
            raise ValueError("batch_size must be positive")
        object.__setattr__(self, "batch_size", int(self.batch_size))

        if self.freshness is not None:
            seconds = (
                self.freshness.total_seconds()
                if isinstance(self.freshness, timedelta)
                else float(self.freshness)
            )
            if seconds < 0:
                raise ValueError("freshness must not be negative")
        if self.parameter_builder is not None and not callable(self.parameter_builder):
            raise ValueError("parameter_builder must be callable")
        if self.request_planner is not None and not callable(self.request_planner):
            raise ValueError("request_planner must be callable")
        if self.fetch_page_size is not None:
            page_size = int(self.fetch_page_size)
            if page_size <= 0:
                raise ValueError("fetch_page_size must be positive")
            object.__setattr__(self, "fetch_page_size", page_size)
        max_fetch_pages = int(self.max_fetch_pages)
        if max_fetch_pages <= 0 or max_fetch_pages > 100_000:
            raise ValueError("max_fetch_pages must be between 1 and 100000")
        object.__setattr__(self, "max_fetch_pages", max_fetch_pages)


@dataclass(frozen=True)
class SyncRequest:
    """Select datasets in one domain for a bounded synchronization run."""

    domain: str
    datasets: tuple[str, ...] = ()
    scope: str = "default"
    params: Mapping[str, Any] = field(default_factory=dict)
    force: bool = False

    def __post_init__(self) -> None:
        domain = str(self.domain or "").strip()
        if not domain:
            raise ValueError("domain must not be empty")
        datasets = tuple(str(item).strip() for item in self.datasets if str(item).strip())
        if len(datasets) != len(set(datasets)):
            raise ValueError("datasets must not contain duplicates")
        scope = str(self.scope or "default").strip() or "default"
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "datasets", datasets)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "params", MappingProxyType(dict(self.params or {})))
        object.__setattr__(self, "force", bool(self.force))


@dataclass(frozen=True)
class DatasetSyncResult:
    dataset_id: str
    status: str
    rows_written: int = 0
    cursor: Any = None
    warning: str | None = None
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False


@dataclass(frozen=True)
class SyncResult:
    domain: str
    status: str
    datasets: Mapping[str, DatasetSyncResult] = field(default_factory=dict)
    rows_written: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "datasets", MappingProxyType(dict(self.datasets)))
        object.__setattr__(self, "warnings", tuple(self.warnings))
