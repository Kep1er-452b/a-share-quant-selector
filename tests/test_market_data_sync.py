from __future__ import annotations

from pathlib import Path
import sys
from threading import Event

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data.catalog import DatasetCatalog, calendar_window_planner
from market_data.models import DatasetSpec, FetchPage, SyncRequest
from market_data.sync_engine import SyncEngine


class RecordingStore:
    def __init__(self):
        self.rows: dict[str, list[dict]] = {}
        self.states: dict[tuple[str, str], dict] = {}
        self.writes: list[dict] = []

    def upsert_rows(
        self,
        dataset,
        rows,
        key_fields,
        symbol_field=None,
        date_field=None,
    ):
        batch = [dict(row) for row in rows]
        self.rows.setdefault(dataset, []).extend(batch)
        self.writes.append(
            {
                "dataset": dataset,
                "rows": batch,
                "key_fields": tuple(key_fields),
                "symbol_field": symbol_field,
                "date_field": date_field,
            }
        )
        return len(batch)

    def set_sync_state(self, dataset, *, scope="default", **values):
        self.states[(dataset, scope)] = {
            "dataset": dataset,
            "scope": scope,
            **values,
        }

    def get_sync_state(self, dataset, scope="default"):
        return self.states.get((dataset, scope))


class FakeClient:
    def __init__(self, **responses):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, method):
        if method not in self.responses:
            raise AttributeError(method)

        def call(**params):
            self.calls.append((method, params))
            response = self.responses[method]
            if isinstance(response, Exception):
                raise response
            if callable(response):
                return response(**params)
            return response

        return call


def spec(dataset_id="hk_basic", **overrides):
    values = {
        "dataset_id": dataset_id,
        "domain": "hong_kong",
        "method": dataset_id,
        "key_fields": ("ts_code",),
    }
    values.update(overrides)
    return DatasetSpec(**values)


def engine_for(client, *specs):
    catalog = DatasetCatalog(specs)
    store = RecordingStore()
    events = []
    engine = SyncEngine(catalog=catalog, store=store, client=client)
    return engine, store, events


def test_catalog_rejects_duplicate_dataset_ids():
    catalog = DatasetCatalog()
    catalog.register(spec())

    with pytest.raises(ValueError, match="duplicate dataset"):
        catalog.register(spec())


def test_catalog_filters_domain_and_rejects_invalid_specs():
    catalog = DatasetCatalog(
        [
            spec("hk_basic"),
            DatasetSpec(
                dataset_id="fut_basic",
                domain="futures",
                method="fut_basic",
                key_fields=("ts_code",),
            ),
        ]
    )

    assert [item.dataset_id for item in catalog.for_domain("hong_kong")] == ["hk_basic"]
    with pytest.raises(KeyError, match="unknown dataset"):
        catalog.get("missing")
    with pytest.raises(ValueError, match="key_fields"):
        spec(key_fields=())


def test_calendar_plan_cursor_is_stable_when_the_open_window_end_advances():
    planner = calendar_window_planner(full_start="20250101", years_per_window=5)
    store = RecordingStore()

    first = tuple(
        planner(
            SyncRequest(
                domain="hong_kong",
                params={"start_date": "20250101", "end_date": "20260713"},
            ),
            None,
            store,
        )
    )
    next_day = tuple(
        planner(
            SyncRequest(
                domain="hong_kong",
                params={"start_date": "20250101", "end_date": "20260714"},
            ),
            None,
            store,
        )
    )

    assert first[0].cursor == next_day[0].cursor


def test_sync_engine_writes_rows_updates_cursor_and_emits_lifecycle():
    client = FakeClient(
        hk_daily=[
            {"ts_code": "00700.HK", "trade_date": "20260709", "close": 419.0},
            {"ts_code": "00700.HK", "trade_date": "20260710", "close": 420.0},
        ]
    )
    engine, store, events = engine_for(
        client,
        spec(
            "hk_daily",
            key_fields=("ts_code", "trade_date"),
            symbol_field="ts_code",
            date_field="trade_date",
        ),
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_daily",), scope="prices"),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "completed"
    assert result.rows_written == 2
    assert store.writes[0]["symbol_field"] == "ts_code"
    assert store.states[("hk_daily", "prices")]["cursor"] == "20260710"
    assert [event["phase"] for event in events] == [
        "preflight",
        "plan",
        "fetch",
        "normalize",
        "write",
        "cache_refresh",
        "quality",
        "terminal",
    ]
    assert events[-1]["status"] == "completed"


def test_optional_permission_error_becomes_warning_and_sync_continues():
    client = FakeClient(
        hk_finance=RuntimeError("没有访问该接口的权限"),
        hk_basic=[{"ts_code": "00700.HK", "name": "腾讯控股"}],
    )
    engine, store, events = engine_for(
        client,
        spec("hk_finance", required=False),
        spec("hk_basic"),
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_finance", "hk_basic")),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "completed_with_warnings"
    assert len(result.warnings) == 1
    assert store.states[("hk_finance", "default")]["status"] == "warning"
    assert store.rows["hk_basic"][0]["name"] == "腾讯控股"
    assert events[-1]["status"] == "completed_with_warnings"


def test_optional_non_permission_endpoint_error_also_becomes_warning():
    client = FakeClient(
        hk_finance=RuntimeError("upstream temporarily unavailable"),
        hk_basic=[{"ts_code": "00700.HK"}],
    )
    engine, store, events = engine_for(
        client,
        spec("hk_finance", required=False),
        spec("hk_basic"),
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_finance", "hk_basic")),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "completed_with_warnings"
    assert store.states[("hk_finance", "default")]["status"] == "warning"
    assert store.rows["hk_basic"] == [{"ts_code": "00700.HK"}]


def test_required_dataset_failure_stops_job_and_records_error():
    client = FakeClient(
        hk_basic=RuntimeError("upstream unavailable"),
        hk_daily=[{"ts_code": "00700.HK", "trade_date": "20260710"}],
    )
    engine, store, events = engine_for(client, spec("hk_basic"), spec("hk_daily"))

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_basic", "hk_daily")),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "failed"
    assert "upstream unavailable" in result.error
    assert store.states[("hk_basic", "default")]["status"] == "failed"
    assert "hk_daily" not in store.rows
    assert [call[0] for call in client.calls] == ["hk_basic"]
    assert events[-1]["status"] == "failed"


def test_sync_engine_does_not_fetch_or_write_when_already_cancelled():
    client = FakeClient(hk_basic=[{"ts_code": "00700.HK"}])
    engine, store, events = engine_for(client, spec())
    cancel = Event()
    cancel.set()

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_basic",)),
        cancel_event=cancel,
        emit=events.append,
    )

    assert result.status == "cancelled"
    assert client.calls == []
    assert store.writes == []
    assert events[-1]["status"] == "cancelled"


def test_sync_engine_discards_rows_when_cancelled_during_fetch():
    cancel = Event()

    def fetch_then_cancel(**_params):
        cancel.set()
        return [{"ts_code": "00700.HK"}]

    client = FakeClient(hk_basic=fetch_then_cancel)
    engine, store, events = engine_for(client, spec())

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_basic",)),
        cancel_event=cancel,
        emit=events.append,
    )

    assert result.status == "cancelled"
    assert store.writes == []
    assert ("hk_basic", "default") not in store.states
    assert "write" not in [event["phase"] for event in events]


def test_sync_engine_rechecks_cancel_after_last_batch_and_reports_persisted_rows():
    cancel = Event()

    class CancelAfterWriteStore(RecordingStore):
        def upsert_rows(self, *args, **kwargs):
            written = super().upsert_rows(*args, **kwargs)
            cancel.set()
            return written

    client = FakeClient(hk_basic=[{"ts_code": "00700.HK"}])
    store = CancelAfterWriteStore()
    events = []
    engine = SyncEngine(
        catalog=DatasetCatalog([spec()]),
        store=store,
        client=client,
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_basic",)),
        cancel_event=cancel,
        emit=events.append,
    )

    assert result.status == "cancelled"
    assert result.rows_written == 1
    assert result.datasets["hk_basic"].rows_written == 1
    assert store.states[("hk_basic", "default")]["status"] == "cancelled"
    assert store.states[("hk_basic", "default")]["row_count"] == 1


def test_second_batch_storage_failure_reports_first_persisted_batch():
    class FailSecondWriteStore(RecordingStore):
        def upsert_rows(self, *args, **kwargs):
            if len(self.writes) == 1:
                raise OSError("disk full")
            return super().upsert_rows(*args, **kwargs)

    client = FakeClient(
        hk_basic=[{"ts_code": "00700.HK"}, {"ts_code": "00941.HK"}]
    )
    store = FailSecondWriteStore()
    events = []
    engine = SyncEngine(
        catalog=DatasetCatalog([spec(batch_size=1)]),
        store=store,
        client=client,
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_basic",)),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "failed"
    assert result.rows_written == 1
    assert result.datasets["hk_basic"].rows_written == 1
    assert store.states[("hk_basic", "default")]["row_count"] == 1
    assert "disk full" in result.error


@pytest.mark.parametrize("failure_point", ["get", "set"])
def test_sync_state_storage_failures_return_failed_result(failure_point):
    class FailingStateStore(RecordingStore):
        def get_sync_state(self, dataset, scope="default"):
            if failure_point == "get":
                raise OSError("state read failed")
            return super().get_sync_state(dataset, scope)

        def set_sync_state(self, dataset, *, scope="default", **values):
            if failure_point == "set":
                raise OSError("state write failed")
            return super().set_sync_state(dataset, scope=scope, **values)

    engine = SyncEngine(
        catalog=DatasetCatalog([spec()]),
        store=FailingStateStore(),
        client=FakeClient(hk_basic=[{"ts_code": "00700.HK"}]),
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_basic",)),
        cancel_event=Event(),
        emit=lambda _event: None,
    )

    assert result.status == "failed"
    assert "state" in result.error


def test_event_sink_failure_does_not_mask_completed_business_result():
    engine, store, _events = engine_for(
        FakeClient(hk_basic=[{"ts_code": "00700.HK"}]),
        spec(),
    )

    def broken_sink(_event):
        raise RuntimeError("event sink unavailable")

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_basic",)),
        cancel_event=Event(),
        emit=broken_sink,
    )

    assert result.status == "completed"
    assert result.rows_written == 1


def test_fresh_dataset_skips_provider_fetch_until_freshness_expires():
    client = FakeClient(hk_basic=[{"ts_code": "00700.HK"}])
    engine, store, events = engine_for(client, spec(freshness=3600))
    store.set_sync_state(
        "hk_basic",
        status="completed",
        cursor="cursor-1",
        updated_at="2999-01-01T00:00:00+00:00",
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_basic",)),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "completed"
    assert result.datasets["hk_basic"].status == "fresh"
    assert result.datasets["hk_basic"].cursor == "cursor-1"
    assert client.calls == []
    assert store.writes == []


@pytest.mark.parametrize(
    "sync_request",
    [
        SyncRequest(domain="hong_kong", datasets=("hk_basic",), force=True),
        SyncRequest(
            domain="hong_kong",
            datasets=("hk_basic",),
            params={"target_cursor": "cursor-2"},
        ),
    ],
)
def test_force_or_explicit_params_bypass_freshness(sync_request):
    client = FakeClient(hk_basic=[{"ts_code": "00700.HK"}])
    engine, store, events = engine_for(client, spec(freshness=3600))
    store.set_sync_state(
        "hk_basic",
        status="completed",
        cursor="cursor-1",
        updated_at="2999-01-01T00:00:00+00:00",
    )

    result = engine.run(sync_request, cancel_event=Event(), emit=events.append)

    assert result.status == "completed"
    assert result.datasets["hk_basic"].status == "completed"
    assert client.calls
    assert len(store.writes) == 1


def test_cache_refresh_failure_marks_persisted_dataset_and_job_failed():
    client = FakeClient(hk_basic=[{"ts_code": "00700.HK"}])
    store = RecordingStore()
    events = []

    def fail_refresh(_spec, _request, _rows):
        raise RuntimeError("cache rebuild failed")

    engine = SyncEngine(
        catalog=DatasetCatalog([spec()]),
        store=store,
        client=client,
        cache_refresher=fail_refresh,
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_basic",)),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "failed"
    assert result.rows_written == 1
    assert result.datasets["hk_basic"].rows_written == 1
    assert store.states[("hk_basic", "default")]["status"] == "failed"
    assert "cache rebuild failed" in result.error
    assert [event["phase"] for event in events][-3:] == [
        "cache_refresh",
        "cache_refresh",
        "terminal",
    ]


def test_parameter_builder_receives_persisted_cursor_and_request_params():
    seen = {}

    def build_params(request, state):
        seen["cursor"] = state["cursor"]
        return {"start_date": state["cursor"], "end_date": request.params["end_date"]}

    client = FakeClient(
        hk_daily={
            "rows": [{"ts_code": "00700.HK", "trade_date": "20260710"}],
            "cursor": "release-20260710",
        }
    )
    engine, store, events = engine_for(
        client,
        spec(
            "hk_daily",
            key_fields=("ts_code", "trade_date"),
            date_field="trade_date",
            parameter_builder=build_params,
        ),
    )
    store.set_sync_state("hk_daily", scope="prices", status="completed", cursor="20260709")

    result = engine.run(
        SyncRequest(
            domain="hong_kong",
            datasets=("hk_daily",),
            scope="prices",
            params={"end_date": "20260710"},
        ),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "completed"
    assert seen["cursor"] == "20260709"
    assert client.calls == [
        ("hk_daily", {"start_date": "20260709", "end_date": "20260710"})
    ]
    assert store.states[("hk_daily", "prices")]["cursor"] == "release-20260710"


def test_request_rejects_dataset_from_a_different_domain():
    client = FakeClient(fut_basic=[])
    engine, store, events = engine_for(
        client,
        DatasetSpec(
            dataset_id="fut_basic",
            domain="futures",
            method="fut_basic",
            key_fields=("ts_code",),
        ),
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("fut_basic",)),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "failed"
    assert "does not belong to domain" in result.error
    assert store.writes == []


def test_planned_full_sync_uses_explicit_bounds_and_provider_pagination():
    def plan(_request, _state, _store):
        return (
            FetchPage(
                cursor="2026",
                params={"start_date": "20260101", "end_date": "20261231"},
            ),
        )

    def fetch_page(**params):
        rows = [
            {"ts_code": "00700.HK", "trade_date": "20260102"},
            {"ts_code": "00941.HK", "trade_date": "20260102"},
            {"ts_code": "01211.HK", "trade_date": "20260102"},
        ]
        offset = params["offset"]
        return rows[offset : offset + params["limit"]]

    client = FakeClient(hk_daily=fetch_page)
    engine, store, events = engine_for(
        client,
        spec(
            "hk_daily",
            key_fields=("ts_code", "trade_date"),
            date_field="trade_date",
            request_planner=plan,
            fetch_page_size=2,
        ),
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_daily",)),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "completed"
    assert result.rows_written == 3
    assert client.calls == [
        (
            "hk_daily",
            {"start_date": "20260101", "end_date": "20261231", "limit": 2, "offset": 0},
        ),
        (
            "hk_daily",
            {"start_date": "20260101", "end_date": "20261231", "limit": 2, "offset": 2},
        ),
    ]
    assert store.states[("hk_daily", "default")]["cursor"] == "20260102"
    assert store.states[("hk_daily", "default")]["status"] == "completed"


def test_planned_sync_resumes_after_last_persisted_plan_cursor():
    def plan(_request, _state, _store):
        return tuple(
            FetchPage(cursor=name, params={"exchange": name})
            for name in ("CFFEX", "DCE", "SHFE")
        )

    client = FakeClient(fut_basic=lambda **params: [{"ts_code": params["exchange"]}])
    store = RecordingStore()
    store.set_sync_state(
        "fut_basic",
        status="cancelled",
        cursor="DCE",
        details={
            "method": "fut_basic",
            "plan_cursor": "DCE",
            "page_offset": 0,
            "page_complete": True,
        },
    )
    engine = SyncEngine(
        catalog=DatasetCatalog(
            [
                DatasetSpec(
                    dataset_id="fut_basic",
                    domain="futures",
                    method="fut_basic",
                    key_fields=("ts_code",),
                    request_planner=plan,
                )
            ]
        ),
        store=store,
        client=client,
    )

    result = engine.run(
        SyncRequest(domain="futures", datasets=("fut_basic",)),
        cancel_event=Event(),
        emit=lambda _event: None,
    )

    assert result.status == "completed"
    assert client.calls == [("fut_basic", {"exchange": "SHFE"})]
    assert store.rows["fut_basic"] == [{"ts_code": "SHFE"}]


def test_optional_planned_warning_resumes_from_the_failed_plan_page():
    def plan(_request, _state, _store):
        return tuple(
            FetchPage(cursor=name, params={"exchange": name})
            for name in ("CFFEX", "DCE")
        )

    store = RecordingStore()

    def fail_on_dce(**params):
        if params["exchange"] == "DCE":
            raise RuntimeError("temporary provider failure")
        return [{"ts_code": params["exchange"]}]

    dataset = DatasetSpec(
        dataset_id="fut_basic",
        domain="futures",
        method="fut_basic",
        key_fields=("ts_code",),
        required=False,
        request_planner=plan,
    )
    first_engine = SyncEngine(
        catalog=DatasetCatalog([dataset]),
        store=store,
        client=FakeClient(fut_basic=fail_on_dce),
    )

    first = first_engine.run(
        SyncRequest(domain="futures", datasets=("fut_basic",)),
        cancel_event=Event(),
        emit=lambda _event: None,
    )

    second_client = FakeClient(
        fut_basic=lambda **params: [{"ts_code": params["exchange"]}]
    )
    second = SyncEngine(
        catalog=DatasetCatalog([dataset]), store=store, client=second_client
    ).run(
        SyncRequest(domain="futures", datasets=("fut_basic",)),
        cancel_event=Event(),
        emit=lambda _event: None,
    )

    assert first.status == "completed_with_warnings"
    assert second.status == "completed"
    assert second_client.calls == [("fut_basic", {"exchange": "DCE"})]


def test_empty_store_planner_cannot_use_one_empty_request_as_full_sync():
    client = FakeClient(hk_daily=[])
    engine, store, events = engine_for(
        client,
        spec(
            "hk_daily",
            request_planner=lambda _request, _state, _store: (
                FetchPage(cursor="full", params={}),
            ),
        ),
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_daily",)),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "failed"
    assert "explicit parameters" in result.error
    assert client.calls == []
    assert store.writes == []


def test_planned_pagination_has_a_hard_provider_call_limit():
    client = FakeClient(
        hk_daily=lambda **_params: [
            {"ts_code": "00700.HK"},
            {"ts_code": "00941.HK"},
        ]
    )
    engine, store, events = engine_for(
        client,
        spec(
            "hk_daily",
            request_planner=lambda _request, _state, _store: (
                FetchPage(cursor="full", params={"trade_date": "20260710"}),
            ),
            fetch_page_size=2,
            max_fetch_pages=2,
        ),
    )

    result = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_daily",)),
        cancel_event=Event(),
        emit=events.append,
    )

    assert result.status == "failed"
    assert "fetch page limit" in result.error
    assert len(client.calls) == 2


def test_planned_partial_write_cancellation_refetches_the_same_provider_page():
    cancel = Event()

    class CancelFirstBatchStore(RecordingStore):
        cancelled_once = False

        def upsert_rows(self, *args, **kwargs):
            written = super().upsert_rows(*args, **kwargs)
            if not self.cancelled_once:
                self.cancelled_once = True
                cancel.set()
            return written

    client = FakeClient(
        hk_daily=[
            {"ts_code": "00700.HK"},
            {"ts_code": "00941.HK"},
            {"ts_code": "01211.HK"},
        ]
    )
    store = CancelFirstBatchStore()
    engine = SyncEngine(
        catalog=DatasetCatalog(
            [
                spec(
                    "hk_daily",
                    request_planner=lambda _request, _state, _store: (
                        FetchPage(cursor="full", params={"trade_date": "20260710"}),
                    ),
                    batch_size=1,
                )
            ]
        ),
        store=store,
        client=client,
    )

    first = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_daily",)),
        cancel_event=cancel,
        emit=lambda _event: None,
    )
    cancel.clear()
    second = engine.run(
        SyncRequest(domain="hong_kong", datasets=("hk_daily",)),
        cancel_event=cancel,
        emit=lambda _event: None,
    )

    assert first.status == "cancelled"
    assert second.status == "completed"
    assert len(client.calls) == 2
