import sys
import threading
import time
import types
import json

import pandas as pd

from utils.market_overview import build_industry_cache


def test_cninfo_industry_fetch_is_serialized(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    stock_dir = data_dir / "00"
    stock_dir.mkdir(parents=True)
    for code in ["000001", "000002"]:
        (stock_dir / f"{code}.csv").write_text("date,open,high,low,close,volume,amount,turnover,market_cap\n", encoding="utf-8")

    state = {"active": 0, "max_seen": 0}
    lock = threading.Lock()

    def fake_eastmoney_industry(symbol):
        return pd.DataFrame({"item": [], "value": []})

    def fake_cninfo_industry(symbol, start_date, end_date):
        with lock:
            state["active"] += 1
            state["max_seen"] = max(state["max_seen"], state["active"])
        try:
            time.sleep(0.02)
            return pd.DataFrame(
                [
                    {
                        "分类标准": "申银万国行业分类标准",
                        "变更日期": "2024-01-01",
                        "行业次类": "测试行业",
                    }
                ]
            )
        finally:
            with lock:
                state["active"] -= 1

    fake_akshare = types.SimpleNamespace(
        stock_individual_info_em=fake_eastmoney_industry,
        stock_industry_change_cninfo=fake_cninfo_industry,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    payload = build_industry_cache(data_dir=str(data_dir))

    assert payload["mapped_count"] == 2
    assert payload["cninfo_count"] == 2
    assert state["max_seen"] == 1


def test_provider_industry_cache_reuses_sibling_provider(monkeypatch, tmp_path):
    storage_root = tmp_path / "data"
    akshare_dir = storage_root / "providers" / "akshare"
    tushare_dir = storage_root / "providers" / "tushare"
    (akshare_dir / "00").mkdir(parents=True)
    (tushare_dir).mkdir(parents=True)
    for code in ["000001", "000002"]:
        (akshare_dir / "00" / f"{code}.csv").write_text(
            "date,open,high,low,close,volume,amount,turnover,market_cap\n",
            encoding="utf-8",
        )
    (tushare_dir / "industry_map.json").write_text(
        json.dumps({"items": {"000001": "银行", "000002": "房地产"}}),
        encoding="utf-8",
    )

    fake_akshare = types.SimpleNamespace(
        stock_individual_info_em=lambda symbol: (_ for _ in ()).throw(AssertionError("network should not run")),
        stock_industry_change_cninfo=lambda **kwargs: (_ for _ in ()).throw(AssertionError("network should not run")),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    payload = build_industry_cache(data_dir=str(akshare_dir))

    assert payload["mapped_count"] == 2
    assert payload["unmapped_count"] == 0
    assert payload["related_reused_count"] == 2
    assert payload["items"] == {"000001": "银行", "000002": "房地产"}
