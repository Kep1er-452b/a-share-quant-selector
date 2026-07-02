from pathlib import Path

from utils.tushare_ext_store import TushareExtStore


def test_store_creates_schema_and_upserts_rows_idempotently(tmp_path):
    store = TushareExtStore(tmp_path / "extended")

    rows = [
        {"ts_code": "000001.SZ", "trade_date": "20260629", "close": 10.2},
        {"ts_code": "000001.SZ", "trade_date": "20260630", "close": 10.4},
    ]
    assert store.upsert_rows("daily_basic", rows, key_fields=("ts_code", "trade_date")) == 2
    assert store.upsert_rows("daily_basic", rows, key_fields=("ts_code", "trade_date")) == 2

    loaded = store.query_rows("daily_basic", ts_code="000001.SZ")
    assert [row["trade_date"] for row in loaded] == ["20260630", "20260629"]
    assert loaded[0]["close"] == 10.4
    assert Path(store.db_path).name == "tushare_ext.sqlite"


def test_store_records_and_lists_sync_warnings(tmp_path):
    store = TushareExtStore(tmp_path / "extended")

    store.set_sync_state(
        "top_inst",
        scope="recent",
        status="warning",
        start_date="20240101",
        end_date="20260630",
        warning="Tushare top_inst permission denied",
        row_count=0,
    )

    warnings = store.list_warnings()
    assert warnings == [
        {
            "dataset": "top_inst",
            "scope": "recent",
            "status": "warning",
            "start_date": "20240101",
            "end_date": "20260630",
            "warning": "Tushare top_inst permission denied",
        }
    ]


def test_store_returns_latest_trade_date_for_dataset_and_code(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    store.upsert_rows(
        "index_daily",
        [
            {"ts_code": "000001.SH", "trade_date": "20260627", "close": 3000},
            {"ts_code": "000001.SH", "trade_date": "20260630", "close": 3010},
            {"ts_code": "399006.SZ", "trade_date": "20260629", "close": 2100},
        ],
        key_fields=("ts_code", "trade_date"),
    )

    assert store.latest_trade_date("index_daily", ts_code="000001.SH") == "20260630"
    assert store.latest_trade_date("index_daily", ts_code="399006.SZ") == "20260629"


def test_store_indexes_dataset_trade_date_and_finds_previous_date(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    store.upsert_rows(
        "moneyflow",
        [
            {"trade_date": "20260627", "ts_code": "000001.SZ", "net_mf_amount": 10},
            {"trade_date": "20260630", "ts_code": "000001.SZ", "net_mf_amount": 20},
        ],
        key_fields=("trade_date", "ts_code"),
    )
    store.upsert_rows(
        "top_list",
        [{"trade_date": "20260629", "ts_code": "000002.SZ", "net_amount": 30}],
        key_fields=("trade_date", "ts_code"),
    )

    with store.connect() as conn:
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(ext_dataset_rows)")}

    assert "idx_ext_rows_dataset_date" in indexes
    assert store.latest_trade_date_before(["moneyflow", "top_list"], "20260630") == "20260629"
    assert store.latest_trade_date_before(["moneyflow"], "20260627") is None


def test_store_rows_signature_changes_when_cached_source_payload_changes(tmp_path):
    store = TushareExtStore(tmp_path / "extended")
    store.upsert_rows(
        "moneyflow",
        [{"trade_date": "20260630", "ts_code": "000001.SZ", "net_mf_amount": 10}],
        key_fields=("trade_date", "ts_code"),
    )

    initial = store.rows_signature(["moneyflow"], ["20260630"])
    store.upsert_rows(
        "moneyflow",
        [{"trade_date": "20260630", "ts_code": "000001.SZ", "net_mf_amount": 11}],
        key_fields=("trade_date", "ts_code"),
    )

    assert store.rows_signature(["moneyflow"], ["20260630"]) != initial
    assert store.get_row("moneyflow", "20260630|000001.SZ")["net_mf_amount"] == 11
