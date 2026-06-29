from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from wyckoff_ai.schema import WyckoffSchemaError, validate_analysis


def _df():
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-05-18", "2026-05-19"]),
        "open": [10.0, 10.5],
        "high": [11.0, 11.2],
        "low": [9.8, 10.1],
        "close": [10.6, 10.9],
    })


def test_validate_unclear_without_events():
    payload = {
        "mode": "unclear",
        "current_phase": "unclear",
        "summary_text": "结构证据不足，暂不强行归类。",
    }
    result = validate_analysis(payload, _df())
    assert result["mode"] == "unclear"
    assert result["events"] == []
    assert result["book_judgment"]["as_of"] == "2026-05-19"
    assert result["book_judgment"]["invalidation"]
    assert result["book_judgment"]["limitations"]


def test_validate_book_judgment_normalizes_fields():
    payload = {
        "mode": "accumulation",
        "current_phase": "Phase D",
        "summary_text": "下跌后进入交易区间，右手边出现需求尝试越过区间上沿，但仍需回测确认。",
        "book_judgment": {
            "as_of": "2026-05-19",
            "background": "吸筹后尝试右手边确认。",
            "action_bias": "偏多但等待回测质量。",
            "next_scenarios": [
                "若缩量回踩守住区间上沿，则SOS/JOC路径增强。",
                {"name": "失效", "description": "若放量跌回区间，则突破判断失效。"},
            ],
            "invalidation": "若放量跌破区间下沿，吸筹判断失效。",
            "limitations": ["样本仅用于单元测试。"],
        },
    }
    result = validate_analysis(payload, _df())
    judgment = result["book_judgment"]
    assert judgment["reading_order"].startswith("背景")
    assert judgment["action_bias"] == "偏多但等待回测质量。"
    assert judgment["next_scenarios"][1] == "失效: 若放量跌回区间，则突破判断失效。"
    assert judgment["limitations"] == ["样本仅用于单元测试。"]


def test_validate_rejects_unknown_date():
    payload = {
        "mode": "accumulation",
        "current_phase": "Phase B",
        "summary_text": "测试",
        "events": [{"term": "SC", "date": "2026-05-17", "price": 10.0, "reason": "测试"}],
    }
    with pytest.raises(WyckoffSchemaError):
        validate_analysis(payload, _df())


def test_validate_rejects_far_price():
    payload = {
        "mode": "accumulation",
        "current_phase": "Phase B",
        "summary_text": "测试",
        "events": [{"term": "SC", "date": "2026-05-19", "price": 99.0, "reason": "测试"}],
    }
    with pytest.raises(WyckoffSchemaError):
        validate_analysis(payload, _df())
