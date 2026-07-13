"""Allowlist and validate resources that may enter a desktop package."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

import yaml


_EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    ".superpowers",
    ".venv",
    "__pycache__",
    "build",
    "data",
    "dist",
    "docs",
    "htmlcov",
    "logs",
    "outputs",
    "stock-selected",
    "tests",
}
_EXCLUDED_RELATIVE_FILES = {
    "config/config.yaml",
    "config/config_local.yaml",
    "config/github.yaml",
}
_ALLOWED_SUFFIXES = {
    ".css",
    ".html",
    ".icns",
    ".js",
    ".json",
    ".png",
    ".py",
    ".svg",
    ".template",
    ".yaml",
    ".yml",
}
_ALLOWED_NAMES = {"requirements.txt"}
_FORBIDDEN_SUFFIXES = {".csv", ".db", ".jsonl", ".sqlite", ".sqlite3"}
_SECRET_ASSIGNMENTS = (
    re.compile(
        r"(?m)^\s*(?:export\s+)?(?:TUSHARE_TOKEN|DEEPSEEK_API_KEY)"
        r"\s*=\s*['\"]?([^\s'\"#]+)"
    ),
    re.compile(
        r"(?im)^\s*(?:token|api_key|deepseek_api_key|secret|webhook_url)"
        r"\s*:\s*['\"]([^'\"#]+)['\"]"
    ),
)
_PLACEHOLDER_MARKERS = ("your_", "example", "placeholder", "<", "${", "os.getenv")
_SENSITIVE_KEY_PARTS = ("token", "api_key", "secret", "webhook", "password", "credential")


def package_resource_files(project_root: Path) -> list[Path]:
    """Return deterministic source resources from an explicit allowlist.

    Runtime stores and local configuration are excluded by directory/name before
    any file-content inspection, so a build never needs to traverse market data.
    """

    root = Path(project_root).resolve()
    resources: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _EXCLUDED_DIRS for part in relative.parts[:-1]):
            continue
        relative_name = relative.as_posix()
        if relative_name in _EXCLUDED_RELATIVE_FILES:
            continue
        if path.name not in _ALLOWED_NAMES and path.suffix.lower() not in _ALLOWED_SUFFIXES:
            continue
        resources.append(path)

    resources.sort(key=lambda item: item.relative_to(root).as_posix())
    assert_package_safe(resources)
    return resources


def assert_package_safe(paths: Iterable[Path], *, root: Path | None = None) -> None:
    """Reject runtime artifacts and non-empty credential assignments."""

    resolved_root = Path(root).resolve() if root is not None else None
    for candidate in paths:
        path = Path(candidate)
        relative = _relative_for_safety(path, resolved_root)
        relative_parts = tuple(part.lower() for part in relative.parts)
        if any(part in {"data", "logs", "outputs"} for part in relative_parts):
            raise ValueError(f"forbidden package resource: {path}")
        if path.name.lower() in {"config.yaml", "config_local.yaml", "github.yaml"}:
            raise ValueError(f"forbidden package resource: {path}")
        if path.suffix.lower() in _FORBIDDEN_SUFFIXES:
            raise ValueError(f"forbidden package resource: {path}")
        if not path.is_file() or path.suffix.lower() in {".icns", ".png"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        structured = _load_structured_content(path, content)
        if structured is not None and _contains_credential(structured):
            raise ValueError(f"credential-like content in package resource: {path}")
        for pattern in _SECRET_ASSIGNMENTS:
            for match in pattern.finditer(content):
                value = match.group(1).strip().lower()
                if any(marker in value for marker in _PLACEHOLDER_MARKERS):
                    continue
                raise ValueError(f"credential-like content in package resource: {path}")


def _relative_for_safety(path: Path, root: Path | None) -> Path:
    if root is None:
        return path
    try:
        return path.resolve().relative_to(root)
    except ValueError:
        raise ValueError(f"package resource is outside package root: {path}") from None


def _load_structured_content(path: Path, content: str):
    try:
        if path.suffix.lower() == ".json":
            return json.loads(content)
        if path.suffix.lower() in {".yaml", ".yml"} or ".yaml." in path.name or ".yml." in path.name:
            return yaml.safe_load(content)
    except (json.JSONDecodeError, yaml.YAMLError):
        return None
    return None


def _contains_credential(value, *, parent_key: str = "") -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            is_metadata = normalized.endswith(("_source", "_present"))
            if not is_metadata and any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                if _credential_value_is_real(item):
                    return True
            if _contains_credential(item, parent_key=normalized):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_credential(item, parent_key=parent_key) for item in value)
    return False


def _credential_value_is_real(value) -> bool:
    if value is None or value is False:
        return False
    text = str(value).strip().lower()
    if not text:
        return False
    return not any(marker in text for marker in _PLACEHOLDER_MARKERS)
