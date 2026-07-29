from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _run_visible_moving_average(values: list[int], window: int, visible_count: int):
    source = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
    start = source.index("function calculateMovingAverage")
    end = source.index("\nfunction ema", start)
    function_source = source[start:end]
    script = (
        f"{function_source}\n"
        f"const values = {json.dumps(values)};\n"
        f"const result = calculateVisibleMovingAverage(values, {window}, {visible_count});\n"
        "process.stdout.write(JSON.stringify(result));\n"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_ma200_uses_available_prewindow_history_without_visible_gap():
    # Mirrors 317 available weekly bars with a 260-bar visible window:
    # only 57 older bars exist, so the first visible point has 58 observations.
    values = list(range(1, 318))

    visible = _run_visible_moving_average(values, window=200, visible_count=260)

    assert visible[0] == 29.5
    assert all(value is not None for value in visible)
    assert visible[-1] == 217.5
