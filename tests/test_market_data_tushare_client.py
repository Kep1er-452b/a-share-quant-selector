import sys

import pytest

from market_data.tushare_client import (
    TushareClientFactory,
    classify_provider_error,
)


def test_client_factory_prefers_environment_without_exposing_token():
    factory = TushareClientFactory.from_config(
        {"data_source": {"tushare": {"token": "config-token"}}},
        {"TUSHARE_TOKEN": " env-token "},
    )

    assert factory.token_present is True
    assert factory.token_source == "environment"
    assert factory.diagnostics() == {
        "token_present": True,
        "token_source": "environment",
    }
    assert "env-token" not in repr(factory)
    assert "config-token" not in repr(factory)


def test_client_factory_uses_config_after_empty_environment_value():
    factory = TushareClientFactory.from_config(
        {"data_source": {"tushare": {"token": " config-token "}}},
        {"TUSHARE_TOKEN": "   "},
    )

    assert factory.token_present is True
    assert factory.token_source == "config"
    assert factory.diagnostics() == {
        "token_present": True,
        "token_source": "config",
    }


def test_client_passes_token_directly_without_calling_set_token(monkeypatch):
    calls = []
    expected_client = object()

    class FakeTushare:
        @staticmethod
        def set_token(value):
            raise AssertionError("set_token must never be called")

        @staticmethod
        def pro_api(value):
            calls.append(value)
            return expected_client

    monkeypatch.setitem(sys.modules, "tushare", FakeTushare)
    factory = TushareClientFactory.from_config(
        {"data_source": {"tushare": {"token": "config-token"}}},
        {},
    )

    assert factory.client() is expected_client
    assert calls == ["config-token"]


def test_client_refuses_to_fall_back_to_sdk_token_storage(monkeypatch):
    class FakeTushare:
        @staticmethod
        def pro_api(*args, **kwargs):
            raise AssertionError("pro_api must not run without an in-memory token")

    monkeypatch.setitem(sys.modules, "tushare", FakeTushare)
    factory = TushareClientFactory.from_config({}, {})

    with pytest.raises(RuntimeError, match="Tushare Token"):
        factory.client()

    assert factory.diagnostics() == {
        "token_present": False,
        "token_source": "missing",
    }


@pytest.mark.parametrize(
    ("error", "code", "category", "retryable"),
    [
        (RuntimeError("没有访问该接口的权限"), "PERMISSION_DENIED", "permission", False),
        (RuntimeError("invalid token"), "TOKEN_INVALID", "authentication", False),
        (RuntimeError("未找到 Tushare Token"), "TOKEN_MISSING", "authentication", False),
        (RuntimeError("每分钟最多访问该接口 200 次"), "RATE_LIMITED", "rate_limit", True),
        (TimeoutError("connection timed out"), "NETWORK_UNREACHABLE", "network", True),
        (RuntimeError("provider returned something unexpected"), "UNKNOWN", "unknown", False),
    ],
)
def test_provider_error_classification(error, code, category, retryable):
    issue = classify_provider_error(error)

    assert issue.code == code
    assert issue.category == category
    assert issue.retryable is retryable
    assert issue.message == str(error)
