"""
终端进度显示工具
"""
from __future__ import annotations

import time


def format_duration(seconds: float) -> str:
    """把秒数格式化成 HH:MM:SS。"""
    total_seconds = max(int(seconds), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_progress(current: int, total: int, width: int = 24) -> str:
    """生成简单的 ASCII 进度条。"""
    safe_total = max(int(total), 1)
    safe_current = min(max(int(current), 0), safe_total)
    ratio = safe_current / safe_total
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {ratio * 100:5.1f}% ({safe_current}/{safe_total})"


class ProgressTracker:
    """记录已耗时并输出进度文本。"""

    def __init__(self, total: int, label: str, width: int = 24):
        self.total = max(int(total), 1)
        self.label = label
        self.width = width
        self.start_time = time.time()

    def line(self, current: int, extra: str = "") -> str:
        progress_text = format_progress(current, self.total, width=self.width)
        elapsed_text = format_duration(time.time() - self.start_time)
        suffix = f" | {extra}" if extra else ""
        return f"{self.label}: {progress_text} | 已耗时 {elapsed_text}{suffix}"

    def elapsed_text(self) -> str:
        return format_duration(time.time() - self.start_time)
