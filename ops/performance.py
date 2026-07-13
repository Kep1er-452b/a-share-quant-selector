"""Bounded in-memory performance samples for the operations console."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import math
from threading import RLock


_EXPECTED_SIGNALS = (
    "api_call_count",
    "api_latency_ms",
    "db_duration_ms",
    "response_payload_bytes",
    "cache_hit_rate",
    "retained_task_count",
    "retained_event_count",
)
_SENSITIVE_LABEL_PARTS = ("token", "secret", "password", "authorization", "webhook", "credential")


class PerformanceRecorder:
    def __init__(self, *, max_samples: int = 2000):
        self.max_samples = min(10000, max(1, int(max_samples)))
        self._samples = deque(maxlen=self.max_samples)
        self._gauges = {}
        self._unavailable = {}
        self._lock = RLock()

    def record(self, metric: str, value, *, labels=None, timestamp=None) -> None:
        metric = str(metric or "").strip()
        number = float(value)
        if not metric or not math.isfinite(number):
            raise ValueError("performance sample requires a finite metric value")
        raw_labels = dict(labels or {})
        if len(raw_labels) > 8:
            raise ValueError("performance labels exceed the supported bound")
        safe_labels = {}
        for key, item in raw_labels.items():
            label_key = str(key).strip()
            label_value = str(item)
            if not label_key or len(label_key) > 40 or len(label_value) > 80:
                raise ValueError("performance labels must be short bounded strings")
            if any(part in label_key.lower() for part in _SENSITIVE_LABEL_PARTS):
                raise ValueError("sensitive performance labels are not allowed")
            safe_labels[label_key] = label_value
        sample = {
            "metric": metric,
            "value": number,
            "labels": safe_labels,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._samples.append(sample)

    def register_gauge(self, metric: str, provider) -> None:
        name = str(metric or "").strip()
        if not name or len(name) > 64 or not callable(provider):
            raise ValueError("performance gauge requires a bounded metric and provider")
        with self._lock:
            self._gauges[name] = provider
            self._unavailable.pop(name, None)

    def mark_unavailable(self, metric: str, reason: str) -> None:
        name = str(metric or "").strip()
        message = str(reason or "").strip()
        if not name or len(name) > 64 or not message or len(message) > 160:
            raise ValueError("unavailable performance signal requires a bounded reason")
        with self._lock:
            self._unavailable[name] = message
            self._gauges.pop(name, None)

    def summary(self) -> dict:
        with self._lock:
            samples = list(self._samples)
            gauges = dict(self._gauges)
            unavailable = dict(self._unavailable)
        grouped = defaultdict(list)
        for sample in samples:
            grouped[sample["metric"]].append(sample["value"])
        metrics = {}
        for name, values in grouped.items():
            metrics[name] = {
                "count": len(values),
                "min": min(values),
                "max": max(values),
                "avg": sum(values) / len(values),
                "latest": values[-1],
            }
        signals = {}
        names = set(_EXPECTED_SIGNALS) | set(metrics) | set(gauges) | set(unavailable)
        for name in sorted(names):
            if name in gauges:
                try:
                    value = float(gauges[name]())
                    if not math.isfinite(value):
                        raise ValueError("non-finite gauge")
                    signals[name] = {"status": "available", "value": value}
                except Exception as exc:
                    signals[name] = {
                        "status": "unavailable",
                        "reason": f"gauge failed: {type(exc).__name__}",
                    }
            elif name in metrics:
                signal = {"status": "available", **metrics[name]}
                if name.endswith("_count"):
                    signal["value"] = sum(grouped[name])
                signals[name] = signal
            else:
                signals[name] = {
                    "status": "unavailable",
                    "reason": unavailable.get(name, "no samples observed"),
                }
        return {
            "retained_samples": len(samples),
            "capacity": self.max_samples,
            "metrics": metrics,
            "signals": signals,
        }
