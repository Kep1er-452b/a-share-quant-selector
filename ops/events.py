from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
import re
import uuid


_SECRET_PARTS = ("token", "api_key", "secret", "password", "webhook", "credential")
_MESSAGE_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(
        r"(?i)\b([\w-]*(?:token|api[_-]?key|secret|password|credential)"
        r"\s*[=:]\s*)[^\s,;&#]+"
    ),
    re.compile(
        r"(?i)([?&][\w-]*(?:token|api[_-]?key|secret|password|credential)=)"
        r"[^&#\s]+"
    ),
    re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@"),
)


def _redact_text(value: str) -> str:
    redacted = value
    for pattern in _MESSAGE_SECRET_PATTERNS:
        if pattern.groups == 3:
            redacted = pattern.sub(r"\1[REDACTED]:[REDACTED]@", redacted)
        else:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted


def redact(value: Any, key: str = "") -> Any:
    normalized = key.lower().replace("-", "_")
    if any(part in normalized for part in _SECRET_PARTS) and not normalized.endswith(("_source", "_present")):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


@dataclass(frozen=True)
class RetentionPolicy:
    task_days: int = 30
    performance_days: int = 7


@dataclass(frozen=True)
class OpsEvent:
    event_id: str
    timestamp: str
    severity: str
    message: str
    domain: str | None = None
    market: str | None = None
    module: str | None = None
    job_id: str | None = None
    dataset: str | None = None
    symbol: str | None = None
    error_code: str | None = None
    event_type: str = "task"
    details: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, *, message: str, timestamp: str | None = None, **kwargs) -> "OpsEvent":
        return cls(
            event_id=uuid.uuid4().hex,
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
            severity=str(kwargs.pop("severity", "info")),
            message=_redact_text(str(message)),
            details=redact(kwargs.pop("details", {}) or {}),
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        return redact(asdict(self))
