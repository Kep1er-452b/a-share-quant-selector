"""System and error logging helpers shared by Web and background tasks."""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
SYSTEM_LOG_FILE = LOG_DIR / "system.log"
ERROR_DIR = LOG_DIR / "errors"


def json_default(value):
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def sanitize_for_log(value):
    sensitive_markers = ("token", "secret", "password", "passwd", "api_key", "apikey", "key")
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(marker in lowered for marker in sensitive_markers):
                sanitized[key_text] = "***REDACTED***"
            else:
                sanitized[key_text] = sanitize_for_log(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_for_log(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_for_log(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def append_system_log(event: str, message: str, detail=None) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        "message": message,
    }
    if detail is not None:
        payload["detail"] = sanitize_for_log(detail)
    with open(SYSTEM_LOG_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, default=json_default) + "\n")
    return SYSTEM_LOG_FILE


def write_error_report(
    module: str,
    error,
    context: dict | None = None,
    error_id: str | None = None,
) -> Path:
    ERROR_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now()
    safe_module = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(module or "system"))
    safe_id = error_id or timestamp.strftime("%Y%m%d-%H%M%S-%f")
    path = ERROR_DIR / f"{timestamp.strftime('%Y%m%d-%H%M%S')}-{safe_module}-{safe_id}.json"
    payload = {
        "error_id": safe_id,
        "module": module,
        "created_at": timestamp.isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "error_type": type(error).__name__,
        "error_message": str(error),
        "traceback": traceback.format_exc(),
        "context": sanitize_for_log(context or {}),
    }
    with open(path, "w", encoding="utf-8") as file:
        json.dump(sanitize_for_log(payload), file, ensure_ascii=False, indent=2, default=json_default)
    append_system_log(
        f"{module}_error_report",
        f"错误日志已写入: {path}",
        {"error_report_path": str(path), "error": str(error), "context": context or {}},
    )
    print(f"错误日志已写入: {path}")
    return path
