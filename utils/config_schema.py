"""
Validation helpers for user-editable YAML configuration.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent


NUMERIC_RANGES = {
    "BowlReboundStrategy": {
        "N": (0, 100),
        "M": (1, 500),
        "CAP": (0, 10**14),
        "J_VAL": (-200, 200),
        "duokong_pct": (0, 100),
        "short_pct": (0, 100),
        "M1": (1, 500),
        "M2": (1, 500),
        "M3": (1, 500),
        "M4": (1, 500),
    },
    "B1V242BStrategy": {
        "J_MAX": (-200, 200),
        "MV_MIN_BILLION": (0, 100000),
        "YANGYIN_RATIO_57": (0, 100),
        "YANGYIN_RATIO_14": (0, 100),
        "PLRY_VOL_RATIO": (0, 100),
        "HALF_DOWN_VOL_RATIO": (0, 10),
        "TOP_RANGE_RATIO": (0, 2),
        "B1_TREND_TOLERANCE": (0, 2),
    },
    "B2BetaStrategy": {
        "J_MAX": (-200, 200),
        "J13_THRESHOLD": (-200, 200),
        "J13_LOOKBACK": (1, 200),
        "MIN_GAIN_RATIO": (0, 10),
        "VOLUP_RATIO": (0, 100),
        "YANGYIN_RATIO_14": (0, 100),
        "MV_MIN_BILLION": (0, 100000),
        "TOP_RANGE_WINDOW": (1, 500),
        "TOP_RANGE_RATIO": (0, 2),
        "FD15_VOL_RATIO": (0, 100),
        "GOOD28_MAX_COUNT": (0, 500),
        "PLRY_VOL_RATIO": (0, 100),
        "PLRY_WINDOW": (1, 500),
        "PLRY_MIN_COUNT": (0, 500),
    },
    "B1MinJSimpleStrategy": {
        "MIN_HISTORY_DAYS": (1, 1000),
        "J_VALLEY_MAX": (-200, 200),
        "LONG_OFFSET": (-200, 200),
    },
    "B1MinJComplexStrategy": {
        "MIN_HISTORY_DAYS": (1, 1000),
        "J_VALLEY_MAX": (-200, 200),
        "LONG_OFFSET": (-200, 200),
        "MV_MIN_BILLION": (0, 100000),
        "YANGYIN_RATIO_57": (0, 100),
        "YANGYIN_RATIO_14": (0, 100),
        "PLRY_VOL_RATIO": (0, 100),
        "HALF_DOWN_VOL_RATIO": (0, 10),
        "TOP_RANGE_RATIO": (0, 2),
        "FD15_VOL_RATIO": (0, 100),
        "B1_TREND_TOLERANCE": (0, 2),
    },
}


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_strategy_params(payload):
    """Return a list of user-facing validation errors."""
    errors = []
    if not isinstance(payload, dict):
        return ["配置必须是 YAML 对象"]

    for section, value in payload.items():
        if not isinstance(value, dict):
            errors.append(f"{section}: 必须是参数对象")
            continue

        range_map = NUMERIC_RANGES.get(section)
        if not range_map:
            continue

        for param_name, (minimum, maximum) in range_map.items():
            if param_name not in value:
                continue
            param_value = value[param_name]
            if not _is_number(param_value):
                errors.append(f"{section}.{param_name}: 必须是数字")
                continue
            if not minimum <= float(param_value) <= maximum:
                errors.append(f"{section}.{param_name}: 必须在 {minimum} 到 {maximum} 之间")

    pattern = payload.get("B1PatternMatch")
    if isinstance(pattern, dict):
        weights = pattern.get("weights")
        if weights is not None:
            if not isinstance(weights, dict):
                errors.append("B1PatternMatch.weights: 必须是对象")
            else:
                total = 0.0
                for key, value in weights.items():
                    if not _is_number(value) or not 0 <= float(value) <= 1:
                        errors.append(f"B1PatternMatch.weights.{key}: 必须在 0 到 1 之间")
                    else:
                        total += float(value)
                if weights and not 0.5 <= total <= 1.5:
                    errors.append("B1PatternMatch.weights: 权重总和应接近 1")

        tolerances = pattern.get("tolerances")
        if tolerances is not None:
            if not isinstance(tolerances, dict):
                errors.append("B1PatternMatch.tolerances: 必须是对象")
            else:
                for key, value in tolerances.items():
                    if not _is_number(value) or float(value) <= 0:
                        errors.append(f"B1PatternMatch.tolerances.{key}: 必须是正数")

    return errors


def atomic_write_yaml(path, payload):
    """Write YAML atomically and keep a best-effort backup."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    backup_path = target.with_suffix(target.suffix + ".bak")
    if target.exists():
        backup_path.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")

    tmp_path = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
    tmp_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(tmp_path, target)
    return backup_path if backup_path.exists() else None


def load_yaml_file(path):
    with open(path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def assert_strategy_params_file(path):
    payload = load_yaml_file(path)
    errors = validate_strategy_params(copy.deepcopy(payload))
    if errors:
        raise ValueError("; ".join(errors))
    return payload
