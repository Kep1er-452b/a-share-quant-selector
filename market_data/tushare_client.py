"""Tushare client creation with in-memory credentials and safe diagnostics."""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import asdict, dataclass
from functools import partial
from typing import Any, Mapping

import pandas as pd
import requests


TUSHARE_API_ENDPOINT = "https://api.tushare.pro"


class TushareHTTPError(RuntimeError):
    """An HTTP failure that must not be mistaken for an empty dataset."""

    def __init__(self, status_code: int, reason: str = "") -> None:
        self.status_code = int(status_code)
        super().__init__(f"Tushare HTTP {self.status_code}{': ' + reason if reason else ''}")


class TushareBusinessError(RuntimeError):
    """A successful HTTP response carrying a Tushare business error."""

    def __init__(self, code: Any, message: str) -> None:
        self.provider_code = code
        super().__init__(message or f"Tushare API error ({code})")


class SecureTushareApi:
    """Small DataApi-compatible transport with explicit TLS and validation.

    The installed Tushare SDK has historically changed its default endpoint
    and, in some releases, treats HTTP 4xx/5xx responses as false and returns
    an empty DataFrame.  Keeping this boundary independent of the SDK makes
    HTTP status and business errors observable while retaining the SDK's
    familiar ``pro.daily(...)`` interface.
    """

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 30,
        session: requests.Session | None = None,
        endpoint: str = TUSHARE_API_ENDPOINT,
    ) -> None:
        endpoint = str(endpoint or "").strip().rstrip("/")
        if not endpoint.lower().startswith("https://"):
            raise ValueError("Tushare endpoint must use HTTPS")
        self.token = str(token or "").strip()
        if not self.token:
            raise ValueError("Tushare token must not be empty")
        self.timeout = max(1.0, min(float(timeout), 300.0))
        self.endpoint = endpoint
        self.session = session or requests.Session()
        # Explicitly retain the user's configured proxy environment.  The
        # provider-specific direct/no-proxy fallback remains opt-in in the
        # stock fetcher and is also HTTPS-only.
        self.session.trust_env = True

    def query(self, api_name: str, fields: str = "", **kwargs):
        api_name = str(api_name or "").strip()
        if not api_name or not re.fullmatch(r"[A-Za-z0-9_]+", api_name):
            raise ValueError("invalid Tushare API name")
        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": kwargs,
            "fields": fields,
        }
        response = self.session.post(
            self.endpoint,
            json=payload,
            timeout=self.timeout,
        )
        status_code = getattr(response, "status_code", 200)
        if int(status_code) >= 400:
            reason = str(getattr(response, "reason", "") or "")[:120]
            raise TushareHTTPError(status_code, reason)
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        try:
            result = response.json()
        except (AttributeError, ValueError, TypeError):
            result = json.loads(getattr(response, "text", ""))
        if not isinstance(result, Mapping):
            raise ValueError("Tushare response must be a JSON object")
        code = result.get("code")
        if code is None:
            raise ValueError("Tushare response is missing business status code")
        if str(code) not in {"0", "0.0"}:
            raise TushareBusinessError(code, str(result.get("msg") or "Tushare API error"))
        data = result.get("data") or {}
        if not isinstance(data, Mapping):
            raise ValueError("Tushare response data must be an object")
        fields_value = data.get("fields") or []
        items = data.get("items") or []
        if not isinstance(fields_value, (list, tuple)) or not isinstance(items, (list, tuple)):
            raise ValueError("Tushare response rows have an invalid schema")
        return pd.DataFrame(items, columns=list(fields_value))

    def __getattr__(self, api_name: str):
        return partial(self.query, api_name)

    def close(self) -> None:
        self.session.close()


def _looks_like_sdk_data_api(client: object) -> bool:
    client_type = type(client)
    return (
        client_type.__module__.startswith("tushare.")
        and client_type.__name__ == "DataApi"
    ) or all(
        hasattr(client, attribute)
        for attribute in ("_DataApi__token", "_DataApi__timeout")
    )


def ensure_tushare_transport(client: object, token: str, *, timeout: float = 30) -> object:
    """Upgrade a real SDK DataApi in place while leaving test doubles intact."""

    if not _looks_like_sdk_data_api(client):
        return client
    transport = SecureTushareApi(token, timeout=timeout)
    # Preserve SDK identity and its compatibility with code that passes the
    # object to ``ts.pro_bar(api=...)``.  Dynamic endpoint methods resolve
    # ``self.query`` through DataApi.__getattr__, so replacing this instance
    # method covers all API calls.
    setattr(client, "query", transport.query)
    setattr(client, "_aqs_secure_transport", transport)
    setattr(client, "_DataApi__http_url", TUSHARE_API_ENDPOINT)
    setattr(client, "_DataApi__timeout", transport.timeout)
    return client


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

    __slots__ = ("__token", "token_present", "token_source", "timeout")

    def __init__(self, token: str | None, token_source: str) -> None:
        normalized = str(token or "").strip()
        self.__token = normalized or None
        self.token_present = self.__token is not None
        self.token_source = token_source if self.token_present else "missing"
        self.timeout = 30.0

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any] | None,
        environ: Mapping[str, Any] | None,
    ) -> "TushareClientFactory":
        environment_token = str((environ or {}).get("TUSHARE_TOKEN") or "").strip()
        timeout_value = 30.0
        data_source = (config or {}).get("data_source") or {}
        tushare_config = data_source.get("tushare") or {} if isinstance(data_source, Mapping) else {}
        if isinstance(tushare_config, Mapping):
            try:
                timeout_value = max(1.0, min(float(tushare_config.get("request_timeout", 30)), 300.0))
            except (TypeError, ValueError):
                timeout_value = 30.0

        def build(token, source):
            factory = cls(token, source)
            factory.timeout = timeout_value
            return factory

        if environment_token:
            return build(environment_token, "environment")

        config_token = (
            str(tushare_config.get("token") or "").strip()
            if isinstance(tushare_config, Mapping)
            else ""
        )
        if config_token:
            return build(config_token, "config")
        return build(None, "missing")

    def client(self) -> object:
        if self.__token is None:
            raise RuntimeError("未找到 Tushare Token；请通过环境变量或本机配置提供")
        tushare = importlib.import_module("tushare")
        try:
            client = tushare.pro_api(self.__token, timeout=self.timeout)
        except TypeError:
            # Some older/fake SDK surfaces do not expose timeout in pro_api;
            # the secure transport still enforces it after construction.
            client = tushare.pro_api(self.__token)
        return ensure_tushare_transport(client, self.__token, timeout=self.timeout)

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
            "TOKEN_INVALID",
            "authentication",
            False,
            ("http 401", "http 403", "status code: 401", "status code: 403"),
        ),
        (
            "RATE_LIMITED",
            "rate_limit",
            True,
            ("http 429", "status code: 429"),
        ),
        (
            "NETWORK_UNREACHABLE",
            "network",
            True,
            (
                "http 408",
                "http 500",
                "http 502",
                "http 503",
                "http 504",
                "status code: 408",
                "status code: 500",
                "status code: 502",
                "status code: 503",
                "status code: 504",
            ),
        ),
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


__all__ = [
    "ProviderIssue",
    "SecureTushareApi",
    "TushareBusinessError",
    "TushareClientFactory",
    "TushareHTTPError",
    "TUSHARE_API_ENDPOINT",
    "classify_provider_error",
    "ensure_tushare_transport",
]
