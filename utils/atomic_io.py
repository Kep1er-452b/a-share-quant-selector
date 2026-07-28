"""Durable atomic file-write helpers for runtime state and caches."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: str | Path, content: str, *, encoding: str = "utf-8") -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return target


def atomic_write_json(
    path: str | Path,
    payload: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
    default=None,
) -> Path:
    return atomic_write_text(
        path,
        json.dumps(
            payload,
            ensure_ascii=ensure_ascii,
            indent=indent,
            default=default,
        ),
    )
