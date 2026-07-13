"""Platform-aware resource and writable runtime path resolution."""

from __future__ import annotations

from dataclasses import dataclass
import os
import platform
from pathlib import Path
import sys
from typing import Mapping

from platformdirs import PlatformDirs


APP_NAME = "A股量化选股系统"
RUNTIME_ROOT_ENV = "AQS_RUNTIME_ROOT"
OUTPUT_ROOT_ENV = "A_SHARE_QUANT_OUTPUT_ROOT"
DEFAULT_OUTPUT_FOLDER = "A股量化选股系统数据"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DOMAIN_STORE_PATHS = {
    "hong_kong": Path("markets/hong_kong/hong_kong.sqlite"),
    "futures": Path("markets/futures/futures.sqlite"),
    "macro": Path("economy/economy.sqlite"),
    "industry": Path("industry/industry.sqlite"),
}
_LEGACY_DOMAIN_STORE_FILES = {
    "hong_kong": "hong_kong.sqlite",
    "futures": "futures.sqlite",
    "macro": "economy.sqlite",
    "industry": "industry.sqlite",
}


def _home_path(environ: Mapping[str, str]) -> Path:
    configured = str(environ.get("HOME") or "").strip()
    return Path(configured).expanduser() if configured else Path.home()


def _platform_user_data_root(system: str, environ: Mapping[str, str]) -> Path:
    normalized = system.casefold()
    if normalized == "windows":
        local_app_data = str(environ.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            return Path(local_app_data).expanduser() / APP_NAME
        if platform.system().casefold() != "windows":
            raise RuntimeError("LOCALAPPDATA is required for packaged Windows paths")
    elif normalized == "darwin":
        return _home_path(environ) / "Library" / "Application Support" / APP_NAME

    return Path(PlatformDirs(APP_NAME, appauthor=False).user_data_path)


@dataclass(frozen=True)
class RuntimePaths:
    """Resolved read-only resources and writable application locations."""

    resource_root: Path
    runtime_root: Path
    data_root: Path
    logs_root: Path
    outputs_root: Path
    config_root: Path
    webview_root: Path

    def ensure_writable_roots(self) -> None:
        """Create empty writable roots without touching bundled resources."""

        for path in (
            self.data_root / "markets" / "hong_kong",
            self.data_root / "markets" / "futures",
            self.data_root / "economy",
            self.data_root / "industry",
            self.data_root / "ops",
            self.logs_root,
            self.outputs_root / "selection",
            self.outputs_root / "wyckoff",
            self.config_root,
            self.webview_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def domain_store_path(self, domain: str) -> Path:
        """Return the approved store path, with a non-destructive legacy fallback."""

        try:
            relative = _DOMAIN_STORE_PATHS[domain]
            legacy_name = _LEGACY_DOMAIN_STORE_FILES[domain]
        except KeyError:
            raise KeyError(f"unknown market-data domain: {domain}") from None
        approved = self.data_root / relative
        legacy = self.data_root / "domains" / legacy_name
        if not approved.exists() and legacy.is_file():
            return legacy
        return approved

    def ops_store_path(self) -> Path:
        """Return the writable structured-operations database path."""

        return self.data_root / "ops" / "ops.sqlite"

    @classmethod
    def for_environment(
        cls,
        *,
        project_root: str | Path,
        packaged: bool,
        system: str,
        environ: Mapping[str, str],
    ) -> "RuntimePaths":
        resource_root = Path(project_root)
        runtime_override = str(environ.get(RUNTIME_ROOT_ENV) or "").strip()
        output_override = str(environ.get(OUTPUT_ROOT_ENV) or "").strip()

        if runtime_override:
            runtime_root = Path(runtime_override).expanduser()
        elif packaged:
            runtime_root = _platform_user_data_root(system, environ)
        else:
            runtime_root = resource_root

        if output_override:
            outputs_root = Path(output_override).expanduser()
        elif packaged or runtime_override:
            outputs_root = runtime_root / "outputs"
        else:
            outputs_root = _home_path(environ) / "DocumentsData" / DEFAULT_OUTPUT_FOLDER

        return cls(
            resource_root=resource_root,
            runtime_root=runtime_root,
            data_root=resource_root / "data" if not packaged else runtime_root / "data",
            logs_root=resource_root / "logs" if not packaged else runtime_root / "logs",
            outputs_root=outputs_root,
            config_root=runtime_root / "config",
            webview_root=(
                runtime_root
                if packaged or runtime_override
                else _platform_user_data_root(system, environ)
            )
            / "webview",
        )


def runtime_paths() -> RuntimePaths:
    """Resolve paths for the current source checkout or frozen application."""

    packaged = bool(getattr(sys, "frozen", False))
    bundle_root = getattr(sys, "_MEIPASS", None)
    resource_root = Path(bundle_root) if packaged and bundle_root else PROJECT_ROOT
    return RuntimePaths.for_environment(
        project_root=resource_root,
        packaged=packaged,
        system=platform.system(),
        environ=os.environ,
    )


def resolve_data_root(
    configured: str | Path | None = None,
    *,
    paths: RuntimePaths | None = None,
) -> Path:
    """Resolve configured A-share storage under the writable runtime root."""

    resolved_paths = paths or runtime_paths()
    value = Path(str(configured or "data")).expanduser()
    if value.is_absolute():
        return value
    if value == Path("data"):
        return resolved_paths.data_root
    return resolved_paths.runtime_root / value
