from dataclasses import FrozenInstanceError
import importlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _runtime_paths_class():
    return importlib.import_module("utils.platform_paths").RuntimePaths


def test_packaged_windows_paths_use_local_app_data(tmp_path):
    runtime_paths = _runtime_paths_class()

    paths = runtime_paths.for_environment(
        project_root=tmp_path / "bundle",
        packaged=True,
        system="Windows",
        environ={"LOCALAPPDATA": str(tmp_path / "LocalAppData")},
    )

    assert paths.runtime_root == tmp_path / "LocalAppData" / "A股量化选股系统"
    assert paths.data_root == paths.runtime_root / "data"
    assert paths.logs_root == paths.runtime_root / "logs"
    assert paths.outputs_root == paths.runtime_root / "outputs"
    assert paths.config_root == paths.runtime_root / "config"
    assert paths.webview_root == paths.runtime_root / "webview"
    assert paths.resource_root == tmp_path / "bundle"


def test_packaged_macos_paths_use_application_support(tmp_path):
    runtime_paths = _runtime_paths_class()

    paths = runtime_paths.for_environment(
        project_root=tmp_path / "bundle",
        packaged=True,
        system="Darwin",
        environ={"HOME": str(tmp_path / "home")},
    )

    assert paths.runtime_root == (
        tmp_path / "home" / "Library" / "Application Support" / "A股量化选股系统"
    )
    assert paths.data_root == paths.runtime_root / "data"
    assert paths.resource_root == tmp_path / "bundle"


def test_source_mode_preserves_repository_data_and_logs(tmp_path):
    runtime_paths = _runtime_paths_class()

    paths = runtime_paths.for_environment(
        project_root=tmp_path,
        packaged=False,
        system="Darwin",
        environ={"HOME": str(tmp_path / "home")},
    )

    assert paths.runtime_root == tmp_path
    assert paths.resource_root == tmp_path
    assert paths.data_root == tmp_path / "data"
    assert paths.logs_root == tmp_path / "logs"
    assert paths.webview_root == (
        tmp_path
        / "home"
        / "Library"
        / "Application Support"
        / "A股量化选股系统"
        / "webview"
    )


def test_runtime_override_does_not_move_source_provider_warehouse(tmp_path):
    runtime_paths = _runtime_paths_class()
    override = tmp_path / "runtime-override"

    paths = runtime_paths.for_environment(
        project_root=tmp_path / "checkout",
        packaged=False,
        system="Windows",
        environ={"AQS_RUNTIME_ROOT": str(override)},
    )

    assert paths.runtime_root == override
    assert paths.data_root == tmp_path / "checkout" / "data"
    assert paths.logs_root == tmp_path / "checkout" / "logs"
    assert paths.config_root == override / "config"


def test_output_override_is_preserved_in_source_and_packaged_modes(tmp_path):
    runtime_paths = _runtime_paths_class()
    output_root = tmp_path / "compatible-outputs"

    for packaged in (False, True):
        paths = runtime_paths.for_environment(
            project_root=tmp_path / "project",
            packaged=packaged,
            system="Windows",
            environ={
                "LOCALAPPDATA": str(tmp_path / "LocalAppData"),
                "A_SHARE_QUANT_OUTPUT_ROOT": str(output_root),
            },
        )
        assert paths.outputs_root == output_root


def test_runtime_paths_are_immutable(tmp_path):
    runtime_paths = _runtime_paths_class()
    paths = runtime_paths.for_environment(
        project_root=tmp_path,
        packaged=False,
        system="Darwin",
        environ={},
    )

    with pytest.raises(FrozenInstanceError):
        paths.data_root = Path("elsewhere")


def test_relative_custom_data_dir_uses_writable_runtime_root_in_packaged_mode(tmp_path):
    platform_paths = importlib.import_module("utils.platform_paths")
    paths = platform_paths.RuntimePaths.for_environment(
        project_root=tmp_path / "bundle",
        packaged=True,
        system="Windows",
        environ={"LOCALAPPDATA": str(tmp_path / "LocalAppData")},
    )

    assert platform_paths.resolve_data_root("warehouse", paths=paths) == (
        paths.runtime_root / "warehouse"
    )


def test_packaged_first_start_creates_approved_empty_runtime_layout(tmp_path):
    runtime_paths = _runtime_paths_class()
    paths = runtime_paths.for_environment(
        project_root=tmp_path / "bundle",
        packaged=True,
        system="Windows",
        environ={"LOCALAPPDATA": str(tmp_path / "LocalAppData")},
    )

    paths.ensure_writable_roots()

    expected = (
        paths.data_root / "markets" / "hong_kong",
        paths.data_root / "markets" / "futures",
        paths.data_root / "economy",
        paths.data_root / "industry",
        paths.data_root / "ops",
        paths.logs_root,
        paths.outputs_root / "selection",
        paths.outputs_root / "wyckoff",
        paths.config_root,
        paths.webview_root,
    )
    assert all(path.is_dir() for path in expected)
    assert not (paths.data_root / "domains").exists()


def test_approved_domain_and_ops_store_paths_match_packaged_layout(tmp_path):
    runtime_paths = _runtime_paths_class()
    paths = runtime_paths.for_environment(
        project_root=tmp_path / "bundle",
        packaged=True,
        system="Darwin",
        environ={"HOME": str(tmp_path / "home")},
    )

    assert paths.domain_store_path("hong_kong") == (
        paths.data_root / "markets" / "hong_kong" / "hong_kong.sqlite"
    )
    assert paths.domain_store_path("futures") == (
        paths.data_root / "markets" / "futures" / "futures.sqlite"
    )
    assert paths.domain_store_path("macro") == paths.data_root / "economy" / "economy.sqlite"
    assert paths.domain_store_path("industry") == paths.data_root / "industry" / "industry.sqlite"
    assert paths.ops_store_path() == paths.data_root / "ops" / "ops.sqlite"


def test_source_mode_falls_back_to_existing_legacy_domain_store_without_moving_it(tmp_path):
    runtime_paths = _runtime_paths_class()
    paths = runtime_paths.for_environment(
        project_root=tmp_path / "checkout",
        packaged=False,
        system="Darwin",
        environ={"HOME": str(tmp_path / "home")},
    )
    legacy = paths.data_root / "domains" / "hong_kong.sqlite"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"existing-domain-store")

    assert paths.domain_store_path("hong_kong") == legacy
    assert legacy.read_bytes() == b"existing-domain-store"
    assert not (paths.data_root / "markets" / "hong_kong" / "hong_kong.sqlite").exists()


def test_approved_domain_store_wins_when_new_and_legacy_paths_both_exist(tmp_path):
    runtime_paths = _runtime_paths_class()
    paths = runtime_paths.for_environment(
        project_root=tmp_path / "checkout",
        packaged=False,
        system="Darwin",
        environ={"HOME": str(tmp_path / "home")},
    )
    legacy = paths.data_root / "domains" / "futures.sqlite"
    approved = paths.data_root / "markets" / "futures" / "futures.sqlite"
    legacy.parent.mkdir(parents=True)
    approved.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")
    approved.write_bytes(b"approved")

    assert paths.domain_store_path("futures") == approved
