import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import market_data.store as store_module
from market_data.store import DomainStore, SCHEMA_VERSION


def test_domain_store_creates_versioned_wal_schema(tmp_path):
    store = DomainStore(tmp_path / "domain.sqlite")

    with sqlite3.connect(store.db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert {"schema_meta", "dataset_rows", "sync_state"}.issubset(tables)
    assert int(version) == SCHEMA_VERSION == 1
    assert journal_mode.lower() == "wal"


def test_domain_store_upserts_filters_and_pages_rows(tmp_path):
    store = DomainStore(tmp_path / "domain.sqlite")
    rows = [
        {"ts_code": "00700.HK", "trade_date": "20260708", "close": 410.0},
        {"ts_code": "00700.HK", "trade_date": "20260710", "close": 420.0},
        {"ts_code": "00941.HK", "trade_date": "20260709", "close": 88.0},
    ]

    assert (
        store.upsert_rows(
            "daily",
            rows,
            key_fields=("ts_code", "trade_date"),
            symbol_field="ts_code",
            date_field="trade_date",
        )
        == 3
    )

    first_page = store.query_rows("daily", symbol="00700.HK", limit=1)
    second_page = store.query_rows(
        "daily", symbol="00700.HK", limit=1, offset=1
    )
    bounded = store.query_rows(
        "daily",
        start_date="20260709",
        end_date="20260710",
        descending=False,
    )

    assert first_page == [
        {"close": 420.0, "trade_date": "20260710", "ts_code": "00700.HK"}
    ]
    assert second_page[0]["trade_date"] == "20260708"
    assert [row["trade_date"] for row in bounded] == ["20260709", "20260710"]

    assert store.upsert_rows(
        "daily",
        [{"ts_code": "00700.HK", "trade_date": "20260710", "close": 421.0}],
        key_fields=("ts_code", "trade_date"),
        symbol_field="ts_code",
        date_field="trade_date",
    ) == 1
    assert store.query_rows("daily", symbol="00700.HK", limit=1)[0]["close"] == 421.0


def test_domain_store_emits_low_cardinality_database_duration_observations(tmp_path):
    assert hasattr(store_module, "configure_store_performance_observer")
    observed = []
    previous = store_module.configure_store_performance_observer(
        lambda metric, value, labels: observed.append((metric, value, labels))
    )
    try:
        store = DomainStore(tmp_path / "domain.sqlite")
        store.upsert_rows(
            "daily",
            [{"symbol": "00700.HK", "date": "20260713", "close": 420}],
            key_fields=("symbol", "date"),
            symbol_field="symbol",
            date_field="date",
        )
        store.query_rows("daily", symbol="00700.HK", limit=1)
    finally:
        store_module.configure_store_performance_observer(previous)

    operations = {labels["operation"] for metric, value, labels in observed if metric == "db_duration_ms" and value >= 0}
    assert {"upsert_rows", "query_rows"}.issubset(operations)
    assert all(set(labels) == {"operation"} for _metric, _value, labels in observed)


def test_domain_store_queries_only_latest_bounded_rows_per_symbol(tmp_path):
    store = DomainStore(tmp_path / "domain.sqlite")
    rows = []
    for symbol in ("00700.HK", "00941.HK", "01211.HK"):
        rows.extend(
            {
                "ts_code": symbol,
                "trade_date": f"202601{day:02d}",
                "close": float(day),
            }
            for day in range(1, 21)
        )
    store.upsert_rows(
        "hk_daily",
        rows,
        key_fields=("ts_code", "trade_date"),
        symbol_field="ts_code",
        date_field="trade_date",
    )

    latest = store.query_latest_rows("hk_daily", limit=2)
    latest_with_context = store.query_latest_rows(
        "hk_daily", rows_per_symbol=2, limit=1, offset=2
    )

    assert [(row["ts_code"], row["trade_date"]) for row in latest] == [
        ("00700.HK", "20260120"),
        ("00941.HK", "20260120"),
    ]
    assert [(row["ts_code"], row["trade_date"]) for row in latest_with_context] == [
        ("01211.HK", "20260120"),
        ("01211.HK", "20260119"),
    ]
    assert len(latest) == 2
    assert len(latest_with_context) == 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 2001}, "limit"),
        ({"limit": 0}, "limit"),
        ({"offset": -1}, "offset"),
        ({"rows_per_symbol": 0}, "rows_per_symbol"),
        ({"rows_per_symbol": 3}, "rows_per_symbol"),
    ],
)
def test_domain_store_rejects_unbounded_latest_row_arguments(
    tmp_path, kwargs, message
):
    store = DomainStore(tmp_path / "domain.sqlite")

    with pytest.raises(ValueError, match=message):
        store.query_latest_rows("hk_daily", **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 2001}, "limit"),
        ({"limit": -1}, "limit"),
        ({"offset": -1}, "offset"),
    ],
)
def test_domain_store_rejects_unbounded_page_arguments(tmp_path, kwargs, message):
    store = DomainStore(tmp_path / "domain.sqlite")

    with pytest.raises(ValueError, match=message):
        store.query_rows("daily", **kwargs)


def test_domain_store_batch_rolls_back_when_sqlite_rejects_a_row(tmp_path):
    store = DomainStore(tmp_path / "domain.sqlite")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_bad_row
            BEFORE INSERT ON dataset_rows
            WHEN json_extract(NEW.payload_json, '$.close') = 999
            BEGIN
                SELECT RAISE(ABORT, 'rejected test row');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="rejected test row"):
        store.upsert_rows(
            "daily",
            [
                {"ts_code": "00700.HK", "trade_date": "20260710", "close": 420},
                {"ts_code": "00941.HK", "trade_date": "20260710", "close": 999},
            ],
            key_fields=("ts_code", "trade_date"),
            symbol_field="ts_code",
            date_field="trade_date",
        )

    assert store.query_rows("daily") == []


def test_domain_store_round_trips_sync_state_and_replaces_previous_state(tmp_path):
    store = DomainStore(tmp_path / "domain.sqlite")

    store.set_sync_state(
        "hk_daily",
        scope="00700.HK",
        status="running",
        cursor="20260709",
        start_date="20260701",
        end_date="20260710",
        row_count=8,
        details={"attempt": 1},
    )
    store.set_sync_state(
        "hk_daily",
        scope="00700.HK",
        status="warning",
        cursor="20260710",
        warning="部分日期无行情",
        row_count=9,
        details={"attempt": 2},
    )

    state = store.get_sync_state("hk_daily", "00700.HK")
    assert state is not None
    assert state["status"] == "warning"
    assert state["cursor"] == "20260710"
    assert state["warning"] == "部分日期无行情"
    assert state["row_count"] == 9
    assert state["details"] == {"attempt": 2}
    assert state["updated_at"]
    assert store.get_sync_state("hk_daily", "missing") is None


def test_domain_store_health_is_lightweight_unless_deep_check_requested(tmp_path):
    store = DomainStore(tmp_path / "domain.sqlite")
    store.upsert_rows(
        "macro_series",
        [
            {"series_id": "cn_cpi.nt_yoy", "period": "202606", "value": 0.3},
            {"series_id": "cn_cpi.nt_yoy", "period": "202607", "value": 0.4},
        ],
        key_fields=("series_id", "period"),
        symbol_field="series_id",
        date_field="period",
    )

    health = store.health()
    deep_health = store.health(deep=True)

    assert health["integrity"] == "not_checked"
    assert health["integrity_checked"] is False
    assert health["dataset_count"] is None
    assert health["row_count"] is None
    assert health["db_size_bytes"] > 0
    assert deep_health["integrity"] == "ok"
    assert deep_health["integrity_checked"] is True
    assert deep_health["dataset_count"] == 1
    assert deep_health["row_count"] == 2
    assert health["schema_version"] == SCHEMA_VERSION
    assert health["journal_mode"] == "wal"
    assert json.loads(json.dumps(health))["db_path"].endswith("domain.sqlite")


def test_domain_store_rejects_future_schema_before_mutating_database(tmp_path):
    db_path = tmp_path / "future.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION + 1),),
        )
    before = db_path.read_bytes()

    with pytest.raises(RuntimeError, match="schema is newer"):
        DomainStore(db_path)

    assert db_path.read_bytes() == before
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert tables == {"schema_meta"}
    assert journal_mode.lower() == "delete"


def test_domain_store_row_keys_do_not_collide_when_values_contain_separator(tmp_path):
    store = DomainStore(tmp_path / "domain.sqlite")

    store.upsert_rows(
        "composite",
        [
            {"left": "a|b", "right": "c", "value": 1},
            {"left": "a", "right": "b|c", "value": 2},
        ],
        key_fields=("left", "right"),
    )

    assert sorted(row["value"] for row in store.query_rows("composite")) == [1, 2]
