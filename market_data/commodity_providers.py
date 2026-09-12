"""Bounded public-provider adapter for the Sina CFD observation series."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
import time
from typing import Any, Mapping

import requests

from market_data.commodity_ratios import canonical_unit
from market_data.global_commodities import (
    SOURCE_PROVIDER,
    series_spec,
    normalize_commodity_rows,
)


SINA_GLOBAL_DAILY_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_S{today}=/GlobalFuturesService.getGlobalFuturesDailyKLine"
)


class CommodityProviderError(RuntimeError):
    """A provider response that cannot be treated as a valid empty dataset."""


class CommodityHTTPError(CommodityProviderError):
    def __init__(self, status_code: int, reason: str = "") -> None:
        self.status_code = int(status_code)
        super().__init__(f"Commodity HTTP {self.status_code}{': ' + reason if reason else ''}")


class CommoditySchemaError(CommodityProviderError):
    pass


def _today_token() -> str:
    now = datetime.now()
    return f"{now.year}_{now.month}_{now.day}"


def _jsonp_rows(text: str) -> list[dict[str, Any]]:
    source = str(text or "")
    start = source.find("[")
    end = source.rfind("]")
    if start < 0 or end < start:
        raise CommoditySchemaError("Sina commodity response did not contain a JSON array")
    try:
        payload = json.loads(source[start : end + 1])
    except (TypeError, ValueError) as exc:
        raise CommoditySchemaError("Sina commodity response was not valid JSONP") from exc
    if not isinstance(payload, list) or any(not isinstance(row, Mapping) for row in payload):
        raise CommoditySchemaError("Sina commodity response rows have an invalid schema")
    return [dict(row) for row in payload]


class SinaCFDProvider:
    """Fetch one full provider history per instrument with bounded retries."""

    provider_name = SOURCE_PROVIDER

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        connect_timeout: float = 3.0,
        read_timeout: float = 10.0,
        retries: int = 1,
        copper_raw_unit: str = "USD/lb",
    ) -> None:
        self.session = session or requests.Session()
        self.session.trust_env = True
        headers = getattr(self.session, "headers", None)
        if headers is not None:
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
                    ),
                    "Accept": "application/json, text/javascript, */*;q=0.01",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Referer": "https://finance.sina.com.cn/futures/quotes/",
                }
            )
        self.connect_timeout = max(1.0, min(float(connect_timeout), 10.0))
        self.read_timeout = max(1.0, min(float(read_timeout), 30.0))
        self.retries = max(0, min(int(retries), 2))
        self.copper_raw_unit = canonical_unit(copper_raw_unit)
        if self.copper_raw_unit not in {"USD/lb", "cents/lb"}:
            raise ValueError("Sina copper raw unit must be USD/lb or cents/lb")

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any] | None = None,
        environ: Mapping[str, Any] | None = None,
    ) -> "SinaCFDProvider":
        config = config or {}
        environ = environ if environ is not None else os.environ
        data_source = config.get("data_source") if isinstance(config, Mapping) else {}
        source_config = (
            data_source.get("global_commodities")
            if isinstance(data_source, Mapping)
            else None
        ) or {}
        if not isinstance(source_config, Mapping):
            source_config = {}

        def number(key: str, env_key: str, default: float, minimum: float, maximum: float):
            value = source_config.get(key, environ.get(env_key, default))
            try:
                return max(minimum, min(float(value), maximum))
            except (TypeError, ValueError):
                return default

        raw_unit = source_config.get(
            "copper_raw_unit", environ.get("AQS_COMMODITY_COPPER_RAW_UNIT", "USD/lb")
        )
        return cls(
            connect_timeout=number("connect_timeout_seconds", "AQS_COMMODITY_CONNECT_TIMEOUT", 3.0, 1.0, 10.0),
            read_timeout=number("read_timeout_seconds", "AQS_COMMODITY_READ_TIMEOUT", 10.0, 1.0, 30.0),
            retries=int(number("retries", "AQS_COMMODITY_RETRIES", 1, 0, 2)),
            copper_raw_unit=str(raw_unit),
        )

    def _request_rows(self, provider_symbol: str) -> list[dict[str, Any]]:
        token = _today_token()
        url = SINA_GLOBAL_DAILY_URL.format(today=token)
        params = {"symbol": provider_symbol, "_": token, "source": "web"}
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=(self.connect_timeout, self.read_timeout),
                )
                status_code = int(getattr(response, "status_code", 200))
                if status_code >= 400:
                    raise CommodityHTTPError(status_code, str(getattr(response, "reason", ""))[:120])
                raise_for_status = getattr(response, "raise_for_status", None)
                if callable(raise_for_status):
                    raise_for_status()
                return _jsonp_rows(getattr(response, "text", ""))
            except CommodityHTTPError as exc:
                last_error = exc
                if not (exc.status_code == 429 or exc.status_code >= 500) or attempt >= self.retries:
                    raise
            except (requests.RequestException, CommoditySchemaError) as exc:
                last_error = exc
                if isinstance(exc, CommoditySchemaError) or attempt >= self.retries:
                    raise
            if attempt < self.retries:
                time.sleep(min(0.4 * (2**attempt), 1.0))
        raise CommodityProviderError(str(last_error or "commodity request failed"))

    def fetch_daily(
        self,
        *,
        series_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        spec = series_spec(series_id)
        raw_rows = self._request_rows(spec.provider_symbol)
        if not raw_rows:
            raise CommoditySchemaError(f"Sina returned no rows for {spec.provider_symbol}")
        resolved = spec
        if spec.asset == "copper" and self.copper_raw_unit != spec.raw_unit:
            # Keep the source identity explicit when a provider unit profile is
            # changed.  The series ID remains the same only for configuration
            # of this declared source; every row carries the actual unit.
            resolved = replace(spec, raw_unit=self.copper_raw_unit, normalized_unit="USD/lb")
        request_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "provider": self.provider_name,
                    "provider_symbol": spec.provider_symbol,
                    "start_date": start_date,
                    "end_date": end_date,
                    "raw_unit": resolved.raw_unit,
                    "definition_version": resolved.definition_version,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = normalize_commodity_rows(
            raw_rows,
            resolved,
            start_date=start_date,
            end_date=end_date,
            fetched_at=fetched_at,
            request_fingerprint=request_fingerprint,
        )
        if not rows:
            raise CommoditySchemaError(f"Sina returned no valid rows for {spec.provider_symbol}")
        return rows

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


CommodityProvider = SinaCFDProvider


__all__ = [
    "CommodityHTTPError",
    "CommodityProvider",
    "CommodityProviderError",
    "CommoditySchemaError",
    "SINA_GLOBAL_DAILY_URL",
    "SinaCFDProvider",
]
