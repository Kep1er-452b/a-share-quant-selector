from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.package_manifest import assert_package_safe, package_resource_files
import build_macos_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_package_manifest_excludes_runtime_and_secrets():
    files = package_resource_files(PROJECT_ROOT)
    relative = {path.relative_to(PROJECT_ROOT).as_posix() for path in files}

    assert files
    assert not any(item.startswith("data/") for item in relative)
    assert not any(item.startswith("logs/") for item in relative)
    assert not any(item.startswith("outputs/") for item in relative)
    assert "config/config.yaml" not in relative
    assert "config/config_local.yaml" not in relative
    assert "config/github.yaml" not in relative
    assert "config/config.yaml.template" in relative
    assert "config/config_local.yaml.template" in relative


def test_package_manifest_rejects_forbidden_artifact(tmp_path):
    sqlite_path = tmp_path / "market.sqlite"
    sqlite_path.write_bytes(b"SQLite format 3")

    with pytest.raises(ValueError, match="forbidden package resource"):
        assert_package_safe([sqlite_path])


def test_package_manifest_rejects_token_like_content(tmp_path):
    config_path = tmp_path / "unsafe.txt"
    config_path.write_text("TUSHARE_TOKEN=0123456789abcdef0123456789abcdef", encoding="utf-8")

    with pytest.raises(ValueError, match="credential-like content"):
        assert_package_safe([config_path])


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("unsafe.yaml", "provider_token: real-secret-value\n"),
        ("unsafe.json", '{"nested": {"api_key": "real-secret-value"}}'),
        ("unsafe.yml", "service:\n  webhook_url: https://secret.invalid/hook\n"),
    ],
)
def test_package_manifest_recursively_rejects_structured_credentials(tmp_path, filename, content):
    config_path = tmp_path / filename
    config_path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="credential-like content"):
        assert_package_safe([config_path])


def test_macos_builder_uses_shared_package_contract():
    source = (PROJECT_ROOT / "build_macos_app.py").read_text(encoding="utf-8")

    assert "package_resource_files" in source
    assert "assert_package_safe" in source
    assert "app_path.rglob" in source


@pytest.mark.parametrize(
    "relative_path",
    ("data/cache.json", "logs/desktop.log", "outputs/result.json", "config/config_local.yaml"),
)
def test_final_bundle_scan_rejects_runtime_paths_even_without_credentials(tmp_path, relative_path):
    bundle = tmp_path / "A股量化选股系统.app"
    resource = bundle / "Contents" / "Resources" / relative_path
    resource.parent.mkdir(parents=True, exist_ok=True)
    resource.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden package resource"):
        assert_package_safe([resource], root=bundle)


def test_macos_build_layout_accepts_environment_paths(tmp_path):
    project = tmp_path / "checkout"
    python = tmp_path / "python"
    applications = tmp_path / "Applications"

    layout = build_macos_app.resolve_build_layout(
        {
            "AQS_PROJECT_ROOT": str(project),
            "AQS_PYTHON": str(python),
            "AQS_MACOS_APP_DIR": str(applications),
        }
    )

    assert layout["project_root"] == project
    assert layout["python"] == python
    assert layout["app_path"] == applications / f"{build_macos_app.APP_NAME}.app"


def test_generated_macos_launcher_allows_project_and_python_relocation(tmp_path):
    script = build_macos_app.render_launcher_script(tmp_path / "checkout")

    assert "AQS_PROJECT_ROOT" in script
    assert "AQS_PYTHON" in script
    assert '$PROJECT_ROOT/launch_desktop_app.py' in script
