from __future__ import annotations

import json
from pathlib import Path

from ops.events import OpsEvent
from ops.store import OpsStore


class EventLogger:
    def __init__(self, store: OpsStore, jsonl_path: str | Path, max_bytes: int = 8 * 1024 * 1024):
        self.store = store
        self.jsonl_path = Path(jsonl_path)
        self.max_bytes = max_bytes

    def emit(self, *, message: str, **kwargs) -> OpsEvent:
        event = OpsEvent.create(message=message, **kwargs)
        self.store.append(event)
        self._append_jsonl(event)
        return event

    def _append_jsonl(self, event: OpsEvent) -> None:
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        if self.jsonl_path.exists() and self.jsonl_path.stat().st_size >= self.max_bytes:
            rotated = self.jsonl_path.with_suffix(self.jsonl_path.suffix + ".1")
            rotated.unlink(missing_ok=True)
            self.jsonl_path.replace(rotated)
        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
