from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import wyckoff_ai.pipeline as pipeline_module
import web_server
from market_data.services import HongKongService
from market_data.store import DomainStore
from utils.market_watchlist import MarketWatchlistStore
from utils.selection_worker import process_selection_chunk
from wyckoff_ai.data import build_wyckoff_input
from wyckoff_ai.pipeline import WyckoffPipeline


def _price_frame(rows: int = 260) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=rows, freq="D")
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": [100.0] * rows,
            "high": [102.0] * rows,
            "low": [99.0] * rows,
            "close": [101.0] * rows,
            "volume": [1000.0] * rows,
        }
    )


class FakeHongKongReader:
    def __init__(self):
        self.frames_read = []

    def list_instruments(self, query="", limit=50, offset=0):
        return {
            "items": [{"symbol": "00700.HK", "name": "腾讯控股"}],
            "total": 1,
            "limit": limit,
            "offset": offset,
        }

    def read_analysis_frame(self, symbol):
        self.frames_read.append(symbol)
        return _price_frame()

    def instrument_metadata(self, symbol):
        return {
            "symbol": "00700.HK",
            "name": "腾讯控股",
            "currency": "HKD",
            "source": "hong_kong_domain_store",
        }


class SharedTrendStrategy:
    market_scope = "market_neutral"

    def calculate_indicators(self, frame):
        return frame.assign(shared_trend=1.0)

    def select_stocks(self, frame, name):
        return [{"category": "all", "close": float(frame.iloc[-1]["close"])}]


class AShareOnlyStrategy(SharedTrendStrategy):
    market_scope = "a_share_only"


class FakeClient:
    last_messages = None

    def __init__(self, api_key, **kwargs):
        self.api_key = api_key

    def analyze(self, messages):
        type(self).last_messages = messages
        return {
            "mode": "unclear",
            "current_phase": "unclear",
            "summary_text": "港股量价证据不足。",
            "ranges": [],
            "phases": [],
            "events": [],
            "scenarios": ["若结构改善，则等待确认。"],
            "risk_note": "仅供技术分析。",
        }, '{"mode":"unclear"}'


def test_watchlist_migrates_legacy_a_share_and_allows_cross_market_codes(tmp_path):
    path = tmp_path / "watchlist.json"
    path.write_text(
        json.dumps(
            {
                "items": {
                    "000001": {
                        "code": "000001",
                        "name": "平安银行",
                        "note": "legacy",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    watchlist = MarketWatchlistStore(path)

    watchlist.add("hong_kong", "00001.HK", name="长和")
    rows = watchlist.list_all()

    assert {(row["market"], row["symbol"]) for row in rows} == {
        ("a_share", "000001.SZ"),
        ("hong_kong", "00001.HK"),
    }
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["version"] == 2
    assert set(stored["items"]) == {
        "a_share:000001.SZ",
        "hong_kong:00001.HK",
    }


def test_watchlist_removal_is_scoped_by_market(tmp_path):
    watchlist = MarketWatchlistStore(tmp_path / "watchlist.json")
    watchlist.add("a_share", "000001.SZ", name="平安银行")
    watchlist.add("hong_kong", "00001.HK", name="长和")

    assert watchlist.remove("hong_kong", "00001.HK") is True
    assert [(row["market"], row["symbol"]) for row in watchlist.list_all()] == [
        ("a_share", "000001.SZ")
    ]


def test_hong_kong_heatmap_uses_real_market_segments_and_performance(tmp_path):
    store = DomainStore(tmp_path / "hong_kong.sqlite")
    store.upsert_rows(
        "hk_basic",
        [
            {"ts_code": "00700.HK", "name": "腾讯控股", "market": "主板"},
            {"ts_code": "08083.HK", "name": "中国有赞", "market": "创业板"},
        ],
        ("ts_code",),
        symbol_field="ts_code",
    )
    store.upsert_rows(
        "hk_daily",
        [
            {
                "ts_code": "00700.HK",
                "trade_date": "20260710",
                "open": 500,
                "high": 510,
                "low": 495,
                "close": 505,
                "pre_close": 500,
                "vol": 100,
            },
            {
                "ts_code": "08083.HK",
                "trade_date": "20260710",
                "open": 0.08,
                "high": 0.08,
                "low": 0.07,
                "close": 0.07,
                "pre_close": 0.08,
                "vol": 200,
            },
        ],
        ("ts_code", "trade_date"),
        symbol_field="ts_code",
        date_field="trade_date",
    )

    payload = HongKongService(store).heatmap()

    assert payload["grouping_mode"] == "market_segment"
    assert {group["name"] for group in payload["groups"]} == {"主板", "创业板"}
    items = [item for group in payload["groups"] for item in group["items"]]
    assert {item["performance_bucket"] for item in items} == {"上涨", "下跌"}
    assert all("industry" not in item for item in items)


def test_hong_kong_wyckoff_input_uses_only_explicit_reader():
    reader = FakeHongKongReader()

    payload = build_wyckoff_input("700", market="hong_kong", reader=reader)

    assert payload["market"] == "hong_kong"
    assert payload["symbol"] == "00700.HK"
    assert payload["currency"] == "HKD"
    assert payload["source"] == "hong_kong_domain_store"
    assert reader.frames_read == ["00700.HK"]
    assert len(payload["frame"]) == 260


def test_hong_kong_wyckoff_pipeline_never_reads_a_share_csv(tmp_path, monkeypatch):
    reader = FakeHongKongReader()
    FakeClient.last_messages = None

    def fake_render(csv_path, analysis, output_path, title):
        source = Path(csv_path)
        assert str(source).startswith(str(tmp_path / "outputs"))
        assert source.exists()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"png")
        return str(output_path)

    monkeypatch.setattr(pipeline_module, "DeepSeekWyckoffClient", FakeClient)
    monkeypatch.setattr(pipeline_module, "render_chart", fake_render)
    runner = WyckoffPipeline(
        config={"wyckoff_ai": {"deepseek_api_key": "test"}},
        data_dir=str(tmp_path / "must-not-be-read"),
        output_dir=tmp_path / "outputs",
        market="hong_kong",
        reader=reader,
    )

    result = runner.analyze_stock("700")

    assert result["stock"]["market"] == "hong_kong"
    assert result["stock"]["symbol"] == "00700.HK"
    assert result["stock"]["currency"] == "HKD"
    assert result["stock"]["source"] == "hong_kong_domain_store"
    assert reader.frames_read == ["00700.HK"]
    assert not (tmp_path / "must-not-be-read").exists()
    prompt = FakeClient.last_messages[-1]["content"]
    assert '"market": "hong_kong"' in prompt
    assert '"currency": "HKD"' in prompt
    assert '"source": "hong_kong_domain_store"' in prompt


def test_hong_kong_selection_returns_market_and_canonical_symbol():
    reader = FakeHongKongReader()
    context = {
        "market_id": "hong_kong",
        "reader": reader,
        "strategies": {"shared_trend": SharedTrendStrategy()},
        "strategy_scopes": {"shared_trend": "market_neutral"},
    }

    result = process_selection_chunk(
        [("700", "腾讯控股")], category="all", context=context
    )

    selected = result["results_by_strategy"]["shared_trend"][0]
    assert selected["market"] == "hong_kong"
    assert selected["symbol"] == "00700.HK"
    assert selected["code"] == "00700.HK"
    assert reader.frames_read == ["00700.HK"]


def test_hong_kong_selection_rejects_a_share_only_strategy_before_reading():
    reader = FakeHongKongReader()
    context = {
        "market_id": "hong_kong",
        "reader": reader,
        "strategies": {"a_share_only": AShareOnlyStrategy()},
        "strategy_scopes": {"a_share_only": "a_share_only"},
    }

    with pytest.raises(ValueError, match="not supported for hong_kong"):
        process_selection_chunk([("00700.HK", "腾讯控股")], context=context)

    assert reader.frames_read == []


class FakeHongKongWebService:
    def search(self, query, limit=20, offset=0):
        return {
            "items": [
                {
                    "symbol": "00700.HK",
                    "name": "腾讯控股",
                    "market": "主板",
                    "currency": "HKD",
                }
            ],
            "total": 1,
            "limit": limit,
            "offset": offset,
        }

    def heatmap(self):
        return {
            "grouping_mode": "market_segment",
            "groups": [{"name": "主板", "items": []}],
        }

    def kline(self, symbol, limit=1000, adjustment="raw"):
        return {"symbol": "00700.HK", "currency": "HKD", "items": []}


def test_market_explicit_hong_kong_watchlist_and_heatmap_routes(
    tmp_path, monkeypatch
):
    service = FakeHongKongWebService()
    monkeypatch.setattr(web_server, "domain_service", lambda domain: service)
    monkeypatch.setattr(
        web_server, "_watchlist_path", lambda: tmp_path / "watchlist.json"
    )
    client = web_server.app.test_client()
    headers = {"X-Quant-Session": web_server.WEB_SESSION_TOKEN}

    created = client.post(
        "/api/equities/hong_kong/watchlist",
        json={"symbol": "700", "note": "核心观察"},
        headers=headers,
    )
    listed = client.get("/api/equities/hong_kong/watchlist")
    heatmap = client.get("/api/equities/hong_kong/heatmap")

    assert created.status_code == 200
    assert created.get_json()["symbol"] == "00700.HK"
    assert listed.get_json()["items"][0]["market"] == "hong_kong"
    assert listed.get_json()["items"][0]["note"] == "核心观察"
    assert heatmap.get_json()["grouping_mode"] == "market_segment"
    assert heatmap.get_json()["groups"][0]["name"] == "主板"


def test_market_explicit_hong_kong_wyckoff_job_passes_reader(
    monkeypatch,
):
    service = FakeHongKongWebService()
    monkeypatch.setattr(web_server, "domain_service", lambda domain: service)
    captured = {}

    def fake_start(market, query, reader):
        captured.update({"market": market, "query": query, "reader": reader})
        return {"job_id": "hk-wyckoff-1", "status": "queued", "market": market}

    monkeypatch.setattr(web_server, "_start_market_wyckoff_job", fake_start)
    client = web_server.app.test_client()
    response = client.post(
        "/api/equities/hong_kong/wyckoff/start",
        json={"symbol": "700"},
        headers={"X-Quant-Session": web_server.WEB_SESSION_TOKEN},
    )

    assert response.status_code == 202
    assert response.get_json()["market"] == "hong_kong"
    assert response.get_json()["symbol"] == "00700.HK"
    assert captured["market"] == "hong_kong"
    assert captured["query"] == "00700.HK"
    assert captured["reader"].service is service


def test_legacy_a_share_wyckoff_start_still_accepts_name_query(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            started.append({"target": target, "args": args, "daemon": daemon})

        def start(self):
            return None

    monkeypatch.setattr(web_server, "Thread", FakeThread)

    job = web_server._start_market_wyckoff_job("a_share", "平安银行")

    assert job["query"] == "平安银行"
    assert job["market"] == "a_share"
    assert started[0]["args"][1] == "平安银行"
    with web_server.wyckoff_jobs_lock:
        web_server.wyckoff_jobs.pop(job["job_id"], None)
