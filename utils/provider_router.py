"""Provider storage routing for local A-share CSV warehouses."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional


VALID_PROVIDERS = ("tushare", "akshare", "tencent")
ACTIVE_PROVIDER_FILE = "active_provider.json"
PROVIDER_STATE_FILE = "provider_state.json"


def normalize_provider(provider: Optional[str], default: str = "akshare") -> str:
    value = str(provider or default).strip().lower()
    if value not in VALID_PROVIDERS:
        raise ValueError(f"不支持的数据源: {provider}")
    return value


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file) or default
    except Exception:
        return default


def _write_json_atomic(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".json",
        prefix=f".{path.name}.",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return path


def provider_data_dir(data_dir: str | Path, provider: str) -> Path:
    provider = normalize_provider(provider)
    return Path(data_dir) / "providers" / provider


def provider_state_path(data_dir: str | Path, provider: str) -> Path:
    return provider_data_dir(data_dir, provider) / PROVIDER_STATE_FILE


def active_provider_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / ACTIVE_PROVIDER_FILE


def legacy_data_dir(data_dir: str | Path) -> Path:
    return Path(data_dir)


def _stock_csv_files(path: Path) -> list[Path]:
    return sorted(path.glob("[0-9][0-9]/*.csv"))


def _has_stock_csv_file(path: Path) -> bool:
    return next(path.glob("[0-9][0-9]/*.csv"), None) is not None


def _latest_csv_date(path: Path) -> Optional[str]:
    latest = None
    for csv_path in _stock_csv_files(path):
        try:
            import pandas as pd

            frame = pd.read_csv(csv_path, usecols=["date"], nrows=1)
            if frame.empty:
                continue
            value = pd.to_datetime(frame.iloc[0]["date"], errors="coerce")
            if pd.isna(value):
                continue
            date_text = value.strftime("%Y-%m-%d")
            if latest is None or date_text > latest:
                latest = date_text
        except Exception:
            continue
    return latest


def warehouse_summary(data_dir: str | Path, provider: str) -> dict:
    provider = normalize_provider(provider)
    path = provider_data_dir(data_dir, provider)
    state = _read_json(provider_state_path(data_dir, provider), {})
    stock_count = len(_stock_csv_files(path)) if path.exists() else 0
    latest_date = state.get("latest_trade_date") or _latest_csv_date(path)
    return {
        "provider": provider,
        "label": provider.upper(),
        "path": str(path),
        "exists": path.exists(),
        "stock_count": stock_count,
        "latest_trade_date": latest_date,
        "updated_at": state.get("updated_at"),
        "status": state.get("status") or ("ready" if stock_count else "empty"),
        "target_count": state.get("target_count", 0),
        "success_count": state.get("success_count", 0),
        "failed_count": state.get("failed_count", 0),
        "warning_count": state.get("warning_count", 0),
        "coverage_ratio": state.get("coverage_ratio"),
        "is_complete": bool(state.get("is_complete", False)),
        "last_error_report_path": state.get("last_error_report_path"),
        "status_summary": state.get("status_summary") or {},
        "runtime_stats": state.get("runtime_stats") or {},
    }


def legacy_summary(data_dir: str | Path) -> dict:
    path = legacy_data_dir(data_dir)
    stock_count = len(_stock_csv_files(path)) if path.exists() else 0
    return {
        "provider": "legacy",
        "label": "LEGACY",
        "path": str(path),
        "exists": path.exists(),
        "stock_count": stock_count,
        "latest_trade_date": _latest_csv_date(path),
        "updated_at": None,
        "status": "ready" if stock_count else "empty",
        "target_count": stock_count,
        "success_count": stock_count,
        "failed_count": 0,
        "warning_count": 0,
        "coverage_ratio": 1.0 if stock_count else 0.0,
        "is_complete": bool(stock_count),
    }


def list_provider_statuses(data_dir: str | Path) -> list[dict]:
    return [warehouse_summary(data_dir, provider) for provider in VALID_PROVIDERS]


def load_active_provider(data_dir: str | Path) -> dict:
    payload = _read_json(active_provider_path(data_dir), {})
    provider = payload.get("active_provider")
    if provider in VALID_PROVIDERS:
        return payload

    statuses = list_provider_statuses(data_dir)
    ready = [
        status for status in statuses
        if status.get("stock_count") and status.get("latest_trade_date")
    ]
    if ready:
        ready.sort(
            key=lambda item: (
                item.get("latest_trade_date") or "",
                item.get("updated_at") or "",
                item.get("stock_count") or 0,
            ),
            reverse=True,
        )
        chosen = ready[0]
        return {
            "active_provider": chosen["provider"],
            "latest_trade_date": chosen.get("latest_trade_date"),
            "updated_at": chosen.get("updated_at"),
            "generation": 0,
            "source": "auto_detected",
        }

    return {
        "active_provider": "legacy",
        "latest_trade_date": _latest_csv_date(legacy_data_dir(data_dir)),
        "updated_at": None,
        "generation": 0,
        "source": "legacy_fallback",
    }


def get_active_provider_name(data_dir: str | Path, default: str = "akshare") -> str:
    payload = load_active_provider(data_dir)
    provider = payload.get("active_provider")
    if provider in VALID_PROVIDERS:
        return provider
    return default


def active_data_dir(data_dir: str | Path, allow_legacy_fallback: bool = True) -> Path:
    payload = load_active_provider(data_dir)
    provider = payload.get("active_provider")
    if provider in VALID_PROVIDERS:
        path = provider_data_dir(data_dir, provider)
        if path.exists() and _has_stock_csv_file(path):
            return path
        if not allow_legacy_fallback:
            return path
    return legacy_data_dir(data_dir)


def write_provider_state(data_dir: str | Path, provider: str, payload: dict) -> Path:
    provider = normalize_provider(provider)
    now = datetime.now().isoformat(timespec="seconds")
    state = {
        "provider": provider,
        "updated_at": now,
        **(payload or {}),
    }
    return _write_json_atomic(provider_state_path(data_dir, provider), state)


def activate_provider(data_dir: str | Path, provider: str, summary: dict | None = None) -> Path:
    provider = normalize_provider(provider)
    previous = load_active_provider(data_dir)
    generation = int(previous.get("generation") or 0) + 1
    summary = summary or warehouse_summary(data_dir, provider)
    payload = {
        "active_provider": provider,
        "latest_trade_date": summary.get("latest_trade_date"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "generation": generation,
        "provider_state": summary,
    }
    return _write_json_atomic(active_provider_path(data_dir), payload)
