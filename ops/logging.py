from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock

from ops.events import OpsEvent
from ops.store import OpsStore


class EventLogger:
    def __init__(self, store: OpsStore, jsonl_path: str | Path, max_bytes: int = 8 * 1024 * 1024):
        self.store = store
        self.jsonl_path = Path(jsonl_path)
        self.max_bytes = max_bytes
        self._jsonl_lock = Lock()

    def emit(self, *, message: str, **kwargs) -> OpsEvent:
        event = OpsEvent.create(message=message, **kwargs)
        self.store.append(event)
        self._append_jsonl(event)
        return event

    def _append_jsonl(self, event: OpsEvent) -> None:
        with self._jsonl_lock:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.jsonl_path.with_suffix(self.jsonl_path.suffix + ".lock")
            with lock_path.open("a+b") as lock_handle:
                if os.name == "nt" and lock_handle.seek(0, os.SEEK_END) == 0:
                    lock_handle.write(b"\0")
                    lock_handle.flush()
                self._lock_file(lock_handle)
                try:
                    if self.jsonl_path.exists() and self.jsonl_path.stat().st_size >= self.max_bytes:
                        rotated = self.jsonl_path.with_suffix(self.jsonl_path.suffix + ".1")
                        temporary = rotated.with_suffix(rotated.suffix + ".tmp")
                        if temporary.exists():
                            temporary.unlink()
                        self.jsonl_path.replace(temporary)
                        os.replace(temporary, rotated)
                    with self.jsonl_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                finally:
                    self._unlock_file(lock_handle)

    @staticmethod
    def _lock_file(handle) -> None:
        if os.name == "nt":  # pragma: no cover - exercised on Windows builds
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _unlock_file(handle) -> None:
        if os.name == "nt":  # pragma: no cover - exercised on Windows builds
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
