"""Tushare client creation with in-memory credentials and safe diagnostics."""

from __future__ import annotations

import importlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ProviderIssue:
    """A provider failure reduced to stable operational semantics."""

    code: str
    category: str
    retryable: bool
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TushareClientFactory:
    """Create Tushare clients without mutating the SDK's persisted token."""

    __slots__ = ("__token", "token_present", "token_source")

    def __init__(self, token: str | None, token_source: str) -> None:
        normalized = str(token or "").strip()
        self.__token = normalized or None
        self.token_present = self.__token is not None
        self.token_source = token_source if self.token_present else "missing"

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any] | None,
        environ: Mapping[str, Any] | None,
    ) -> "TushareClientFactory":
        environment_token = str((environ or {}).get("TUSHARE_TOKEN") or "").strip()
        if environment_token:
            return cls(environment_token, "environment")

        data_source = (config or {}).get("data_source") or {}
        tushare_config = data_source.get("tushare") or {} if isinstance(data_source, Mapping) else {}
        config_token = (
            str(tushare_config.get("token") or "").strip()
            if isinstance(tushare_config, Mapping)
            else ""
        )
        if config_token:
            return cls(config_token, "config")
        return cls(None, "missing")

    def client(self) -> object:
        if self.__token is None:
            raise RuntimeError("未找到 Tushare Token；请通过环境变量或本机配置提供")
        tushare = importlib.import_module("tushare")
        return tushare.pro_api(self.__token)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "token_present": self.token_present,
            "token_source": self.token_source,
        }

    def __repr__(self) -> str:
        return (
            "TushareClientFactory("
            f"token_present={self.token_present!r}, token_source={self.token_source!r})"
        )


def classify_provider_error(exc: BaseException) -> ProviderIssue:
    """Classify provider failures without assuming an unsafe retry."""

    message = str(exc or "")
    combined = f"{type(exc).__name__} {message}".lower()
    if re.search(r"没有接口\s*(?:\([^)]*\))?\s*访问权限", combined):
        return ProviderIssue("PERMISSION_DENIED", "permission", False, message)
    rules = (
        (
            "TOKEN_MISSING",
            "authentication",
            False,
            ("未找到 tushare token", "missing token", "token missing"),
        ),
        (
            "TOKEN_INVALID",
            "authentication",
            False,
            (
                "token不对",
                "token 无效",
                "invalid token",
                "token error",
                "unauthorized",
                "authentication failed",
            ),
        ),
        (
            "PERMISSION_DENIED",
            "permission",
            False,
            (
                "没有访问该接口的权限",
                "没有权限",
                "权限不足",
                "积分不足",
                "permission denied",
                "not have access",
                "forbidden",
            ),
        ),
        (
            "RATE_LIMITED",
            "rate_limit",
            True,
            (
                "每分钟最多",
                "频率超限",
                "访问频次",
                "rate limit",
                "too many requests",
                "http 429",
            ),
        ),
        (
            "NETWORK_UNREACHABLE",
            "network",
            True,
            (
                "timeout",
                "timed out",
                "connection",
                "proxyerror",
                "unable to connect",
                "remote end closed",
                "network is unreachable",
                "dns",
                "ssl",
            ),
        ),
    )
    for code, category, retryable, markers in rules:
        if any(marker in combined for marker in markers):
            return ProviderIssue(code, category, retryable, message)
    return ProviderIssue("UNKNOWN", "unknown", False, message)


__all__ = ["ProviderIssue", "TushareClientFactory", "classify_provider_error"]
