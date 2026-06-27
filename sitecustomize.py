"""Process-wide console defaults for local Windows runs.

Python imports this module automatically when the project root is on sys.path.
It keeps status output containing check marks and Chinese text from crashing
under the default Windows GBK console.
"""

from __future__ import annotations

import os
import sys


os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if stream is not None and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
