"""
Helpers for loading the tracked config plus the ignored local override.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    return payload if isinstance(payload, dict) else {}


def resolve_config_path(config_file="config/config.yaml") -> Path:
    path = Path(config_file)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def local_override_path(config_file="config/config.yaml") -> Path:
    config_path = resolve_config_path(config_file)
    return config_path.parent / "config_local.yaml"


def load_config_file(config_file="config/config.yaml", include_local=True) -> dict:
    """
    Load config/config.yaml and merge config/config_local.yaml when present.

    config_local.yaml is intended for machine-local secrets and should remain
    ignored by git. Values in the local file override the tracked config.
    """
    config_path = resolve_config_path(config_file)
    config = _load_yaml(config_path)
    if include_local:
        local_path = local_override_path(config_path)
        if local_path.exists():
            config = _deep_merge(config, _load_yaml(local_path))
    return config

