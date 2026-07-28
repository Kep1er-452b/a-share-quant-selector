"""
Helpers for loading the tracked config plus the ignored local override.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile

import yaml

from utils.platform_paths import resolve_data_root, runtime_paths


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
    if path.is_absolute():
        return path
    paths = runtime_paths()
    relative = path.parts[1:] if path.parts and path.parts[0] == "config" else path.parts
    return paths.config_root.joinpath(*relative)


def local_override_path(config_file="config/config.yaml") -> Path:
    config_path = resolve_config_path(config_file)
    return config_path.parent / "config_local.yaml"


def _resource_template_path(config_file="config/config.yaml") -> Path:
    path = Path(config_file)
    paths = runtime_paths()
    if path.is_absolute():
        return path.with_name(f"{path.name}.template")
    relative = path.parts[1:] if path.parts and path.parts[0] == "config" else path.parts
    resource_path = paths.resource_root / "config" / Path(*relative)
    return resource_path.with_name(f"{resource_path.name}.template")


def ensure_runtime_config_templates() -> tuple[Path, Path]:
    """Copy fillable, secret-free templates into the writable config directory."""

    paths = runtime_paths()
    targets = []
    for name in ("config.yaml.template", "config_local.yaml.template"):
        source = paths.resource_root / "config" / name
        target = paths.config_root / name
        targets.append(target)
        if target == source or target.exists():
            continue
        if not source.is_file():
            continue
        content = source.read_text(encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x", encoding="utf-8") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
        except FileExistsError:
            pass
    return tuple(targets)


def load_config_file(config_file="config/config.yaml", include_local=True) -> dict:
    """
    Load config/config.yaml and merge config/config_local.yaml when present.

    config_local.yaml is intended for machine-local secrets and should remain
    ignored by git. Values in the local file override the tracked config.
    """
    if not Path(config_file).is_absolute():
        ensure_runtime_config_templates()
    config_path = resolve_config_path(config_file)
    config = _load_yaml(_resource_template_path(config_file))
    config = _deep_merge(config, _load_yaml(config_path))
    if include_local:
        local_path = local_override_path(config_path)
        if local_path.exists():
            config = _deep_merge(config, _load_yaml(local_path))
    config["data_dir"] = str(
        resolve_data_root(config.get("data_dir", "data"), paths=runtime_paths())
    )
    return config


def load_local_config_file(config_file="config/config.yaml") -> dict:
    """Load only the ignored machine-local override, never tracked config."""
    return _load_yaml(local_override_path(config_file))


def write_local_config_file(payload: dict, config_file="config/config.yaml") -> Path:
    """Atomically write the machine-local override under the writable config root."""

    if not isinstance(payload, dict):
        raise TypeError("local config must be a mapping")
    path = local_override_path(config_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            yaml.safe_dump(payload, file, allow_unicode=True, sort_keys=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return path
