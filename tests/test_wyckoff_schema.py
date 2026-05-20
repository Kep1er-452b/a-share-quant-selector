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
