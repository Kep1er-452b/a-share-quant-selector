"""Validated registry for domain dataset specifications."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from market_data.models import DatasetSpec, FetchPage


class DatasetCatalog:
    """Keep dataset identifiers unique while preserving registration order."""

    def __init__(self, specs: Iterable[DatasetSpec] = ()) -> None:
        self._specs: dict[str, DatasetSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: DatasetSpec) -> DatasetSpec:
        if not isinstance(spec, DatasetSpec):
            raise TypeError("spec must be a DatasetSpec")
        if spec.dataset_id in self._specs:
            raise ValueError(f"duplicate dataset: {spec.dataset_id}")
        self._specs[spec.dataset_id] = spec
        return spec

    def get(self, dataset_id: str) -> DatasetSpec:
        dataset_id = str(dataset_id or "").strip()
        try:
            return self._specs[dataset_id]
        except KeyError as exc:
            raise KeyError(f"unknown dataset: {dataset_id}") from exc

    def for_domain(self, domain: str) -> tuple[DatasetSpec, ...]:
        domain = str(domain or "").strip()
        return tuple(spec for spec in self._specs.values() if spec.domain == domain)

    def __iter__(self) -> Iterator[DatasetSpec]:
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)


def _request_params(request, allowed: Sequence[str]) -> dict[str, Any]:
    allowed_keys = frozenset(str(item) for item in allowed)
    return {
        key: value
        for key, value in request.params.items()
        if key in allowed_keys and value not in (None, "")
    }


def partition_planner(
    field: str,
    values: Iterable[str],
    *,
    fixed: Mapping[str, Any] | None = None,
    allowed: Sequence[str] = (),
):
    """Build stable metadata partitions such as exchange or list status."""

    partitions = tuple(str(value) for value in values)
    defaults = dict(fixed or {})

    def plan(request, _state, _store):
        supplied = _request_params(request, (field, *allowed))
        selected = (str(supplied[field]),) if field in supplied else partitions
        base = {**defaults, **{key: value for key, value in supplied.items() if key != field}}
        return tuple(
            FetchPage(
                cursor=f"{field}={value}",
                params={**base, field: value},
            )
            for value in selected
        )

    return plan


def _parse_day(value: object) -> date:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 8:
        raise ValueError("date bound must use YYYYMMDD")
    return datetime.strptime(digits, "%Y%m%d").date()


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


def _calendar_pages(
    *,
    start: object,
    end: object,
    start_key: str,
    end_key: str,
    years_per_window: int,
    base: Mapping[str, Any] | None = None,
    cursor_prefix: str = "",
) -> tuple[FetchPage, ...]:
    first = _parse_day(start)
    last = _parse_day(end)
    if first > last:
        first = last
    pages = []
    current = first
    while current <= last:
        window_end = min(last, _add_years(current, years_per_window) - timedelta(days=1))
        start_text = current.strftime("%Y%m%d")
        end_text = window_end.strftime("%Y%m%d")
        pages.append(
            FetchPage(
                cursor=f"{cursor_prefix}{start_text}",
                params={
                    **dict(base or {}),
                    start_key: start_text,
                    end_key: end_text,
                },
            )
        )
        current = window_end + timedelta(days=1)
    return tuple(pages)


def calendar_window_planner(
    *,
    full_start: str,
    years_per_window: int = 5,
    start_key: str = "start_date",
    end_key: str = "end_date",
    exact_key: str | None = None,
    fixed: Mapping[str, Any] | None = None,
    allowed: Sequence[str] = (),
):
    """Plan explicit calendar windows, using the completed data cursor incrementally."""

    def plan(request, state, _store):
        keys = [start_key, end_key, *allowed]
        if exact_key:
            keys.append(exact_key)
        supplied = _request_params(request, keys)
        base = {**dict(fixed or {}), **supplied}
        if exact_key and exact_key in supplied:
            return (
                FetchPage(
                    cursor=f"{exact_key}={supplied[exact_key]}",
                    params=base,
                ),
            )
        completed_cursor = (
            str((state or {}).get("cursor") or "")
            if (state or {}).get("status") == "completed"
            else ""
        )
        start = supplied.get(start_key) or (
            completed_cursor if len("".join(filter(str.isdigit, completed_cursor))) == 8 else full_start
        )
        end = supplied.get(end_key) or date.today().strftime("%Y%m%d")
        base.pop(start_key, None)
        base.pop(end_key, None)
        return _calendar_pages(
            start=start,
            end=end,
            start_key=start_key,
            end_key=end_key,
            years_per_window=years_per_window,
            base=base,
        )

    return plan


def _all_store_rows(store, dataset: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while offset < 100_000:
        page = store.query_rows(dataset, limit=2000, offset=offset)
        rows.extend(page)
        if len(page) < 2000:
            return rows
        offset += len(page)
    raise ValueError(f"source dataset {dataset} exceeds the bounded planner limit")


def source_partition_planner(
    *,
    source_dataset: str,
    source_field: str,
    provider_field: str,
    fixed: Mapping[str, Any] | None = None,
    source_filter=None,
    allowed: Sequence[str] = (),
):
    """Partition a request by values already synchronized into a metadata store."""

    def plan(request, _state, store):
        supplied = _request_params(request, (provider_field, *allowed))
        if provider_field in supplied:
            values = (str(supplied[provider_field]),)
        else:
            rows = _all_store_rows(store, source_dataset)
            if source_filter is not None:
                rows = [row for row in rows if source_filter(row)]
            values = tuple(
                sorted(
                    {
                        str(row.get(source_field) or "").strip()
                        for row in rows
                        if str(row.get(source_field) or "").strip()
                    }
                )
            )
        if not values:
            raise ValueError(
                f"sync planning requires populated {source_dataset} metadata"
            )
        base = {
            **dict(fixed or {}),
            **{key: value for key, value in supplied.items() if key != provider_field},
        }
        return tuple(
            FetchPage(
                cursor=f"{provider_field}={value}",
                params={**base, provider_field: value},
            )
            for value in values
        )

    return plan


def symbol_calendar_planner(
    *,
    source_dataset: str,
    source_field: str,
    provider_field: str = "ts_code",
    full_start: str,
    years_per_window: int = 10,
    start_key: str = "start_date",
    end_key: str = "end_date",
    allowed: Sequence[str] = (),
    source_filter=None,
):
    """Plan bounded date windows for every locally known instrument symbol."""

    def plan(request, state, store):
        supplied = _request_params(
            request,
            (provider_field, start_key, end_key, *allowed),
        )
        if provider_field in supplied:
            symbols = (str(supplied[provider_field]),)
        else:
            rows = _all_store_rows(store, source_dataset)
            if source_filter is not None:
                rows = [row for row in rows if source_filter(row)]
            symbols = tuple(
                sorted(
                    {
                        str(row.get(source_field) or "").strip()
                        for row in rows
                        if str(row.get(source_field) or "").strip()
                    }
                )
            )
        if not symbols:
            raise ValueError(
                f"sync planning requires populated {source_dataset} metadata"
            )
        completed_cursor = (
            str((state or {}).get("cursor") or "")
            if (state or {}).get("status") == "completed"
            else ""
        )
        cursor_digits = "".join(character for character in completed_cursor if character.isdigit())
        start = supplied.get(start_key) or (cursor_digits if len(cursor_digits) == 8 else full_start)
        end = supplied.get(end_key) or date.today().strftime("%Y%m%d")
        base = {
            key: value
            for key, value in supplied.items()
            if key not in {provider_field, start_key, end_key}
        }
        pages = []
        for symbol in symbols:
            pages.extend(
                _calendar_pages(
                    start=start,
                    end=end,
                    start_key=start_key,
                    end_key=end_key,
                    years_per_window=years_per_window,
                    base={**base, provider_field: symbol},
                    cursor_prefix=f"{symbol}:",
                )
            )
        return tuple(pages)

    return plan


def period_window_planner(
    kind: str,
    *,
    full_start: str,
    window_years: int = 10,
):
    """Plan explicit monthly or quarterly release ranges."""

    if kind == "month":
        exact_key, start_key, end_key, periods_per_year = "m", "start_m", "end_m", 12
        default_end = f"{date.today().year:04d}{date.today().month:02d}"

        def parse(value):
            text = str(value).upper()
            return int(text[:4]) * 12 + int(text[4:6]) - 1

        def format_period(index):
            year, month = divmod(index, 12)
            return f"{year:04d}{month + 1:02d}"
    elif kind == "quarter":
        exact_key, start_key, end_key, periods_per_year = "q", "start_q", "end_q", 4
        default_end = f"{date.today().year:04d}Q{(date.today().month - 1) // 3 + 1}"

        def parse(value):
            text = str(value).upper()
            year, quarter = text.split("Q", 1)
            return int(year) * 4 + int(quarter) - 1

        def format_period(index):
            year, quarter = divmod(index, 4)
            return f"{year:04d}Q{quarter + 1}"
    else:
        raise ValueError("period planner kind must be month or quarter")

    def plan(request, state, _store):
        supplied = _request_params(request, (exact_key, start_key, end_key, "fields"))
        if exact_key in supplied:
            return (
                FetchPage(
                    cursor=f"{exact_key}={supplied[exact_key]}",
                    params=supplied,
                ),
            )
        completed_cursor = (
            str((state or {}).get("cursor") or "")
            if (state or {}).get("status") == "completed"
            else ""
        )
        start_text = str(supplied.get(start_key) or completed_cursor or full_start)
        end_text = str(supplied.get(end_key) or default_end)
        first, last = parse(start_text), parse(end_text)
        if first > last:
            first = last
        step = max(1, int(window_years)) * periods_per_year
        pages = []
        current = first
        base = {key: value for key, value in supplied.items() if key not in {start_key, end_key}}
        while current <= last:
            window_end = min(last, current + step - 1)
            start_value, end_value = format_period(current), format_period(window_end)
            pages.append(
                FetchPage(
                    cursor=start_value,
                    params={**base, start_key: start_value, end_key: end_value},
                )
            )
            current = window_end + 1
        return tuple(pages)

    return plan
