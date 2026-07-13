from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.platform_paths import RuntimePaths, resolve_data_root
import utils.local_config as local_config


def paths_for(tmp_path, *, packaged, system):
    environ = {"HOME": str(tmp_path / "home")}
    if system == "Windows":
        environ["LOCALAPPDATA"] = str(tmp_path / "LocalAppData")
    return RuntimePaths.for_environment(
        project_root=tmp_path / "bundle",
        packaged=packaged,
        system=system,
        environ=environ,
    )


def test_source_data_and_config_paths_stay_inside_checkout(tmp_path, monkeypatch):
    paths = paths_for(tmp_path, packaged=False, system="Darwin")
    monkeypatch.setattr(local_config, "runtime_paths", lambda: paths)

    assert resolve_data_root("data", paths=paths) == paths.resource_root / "data"
    assert local_config.resolve_config_path() == paths.resource_root / "config" / "config.yaml"
    assert local_config.local_override_path() == paths.resource_root / "config" / "config_local.yaml"


def test_packaged_windows_data_logs_and_local_config_use_local_app_data(tmp_path, monkeypatch):
    paths = paths_for(tmp_path, packaged=True, system="Windows")
    monkeypatch.setattr(local_config, "runtime_paths", lambda: paths)

    assert resolve_data_root("data", paths=paths) == paths.runtime_root / "data"
    assert paths.logs_root == paths.runtime_root / "logs"
    assert local_config.resolve_config_path() == paths.config_root / "config.yaml"
    assert local_config.local_override_path() == paths.config_root / "config_local.yaml"


def test_packaged_macos_loads_resource_template_then_writable_local_override(
    tmp_path, monkeypatch
):
    paths = paths_for(tmp_path, packaged=True, system="Darwin")
    monkeypatch.setattr(local_config, "runtime_paths", lambda: paths)
    template = paths.resource_root / "config" / "config.yaml.template"
    template.parent.mkdir(parents=True)
    template.write_text(
        yaml.safe_dump({"data_dir": "data", "web": {"port": 5080}}),
        encoding="utf-8",
    )
    paths.config_root.mkdir(parents=True)
    (paths.config_root / "config_local.yaml").write_text(
        yaml.safe_dump({"web": {"port": 5090}}), encoding="utf-8"
    )

    config = local_config.load_config_file()

    assert config["data_dir"] == str(paths.data_root)
    assert config["web"]["port"] == 5090


def test_absolute_custom_data_path_is_preserved(tmp_path):
    paths = paths_for(tmp_path, packaged=True, system="Windows")
    custom = tmp_path / "external-data"

    assert resolve_data_root(custom, paths=paths) == custom


def test_runtime_roots_can_be_reserved_without_creating_resource_data(tmp_path):
    paths = paths_for(tmp_path, packaged=True, system="Windows")

    paths.ensure_writable_roots()

    for path in (
        paths.data_root,
        paths.logs_root,
        paths.outputs_root,
        paths.config_root,
        paths.webview_root,
    ):
        assert path.is_dir()
    assert not (paths.resource_root / "data").exists()


def test_packaged_local_config_write_targets_writable_config_root(tmp_path, monkeypatch):
    paths = paths_for(tmp_path, packaged=True, system="Darwin")
    monkeypatch.setattr(local_config, "runtime_paths", lambda: paths)

    written = local_config.write_local_config_file({"web": {"port": 5091}})

    assert written == paths.config_root / "config_local.yaml"
    assert yaml.safe_load(written.read_text(encoding="utf-8"))["web"]["port"] == 5091


def test_packaged_config_bootstrap_copies_fillable_secret_free_templates(tmp_path, monkeypatch):
    paths = paths_for(tmp_path, packaged=True, system="Darwin")
    monkeypatch.setattr(local_config, "runtime_paths", lambda: paths)
    resource_config = paths.resource_root / "config"
    resource_config.mkdir(parents=True)
    (resource_config / "config.yaml.template").write_text(
        "data_dir: data\n",
        encoding="utf-8",
    )
    (resource_config / "config_local.yaml.template").write_text(
        "data_source:\n  tushare:\n    token: ''\nwyckoff_ai:\n  deepseek_api_key: ''\n",
        encoding="utf-8",
    )

    created = local_config.ensure_runtime_config_templates()

    assert created == (
        paths.config_root / "config.yaml.template",
        paths.config_root / "config_local.yaml.template",
    )
    local_payload = yaml.safe_load(created[1].read_text(encoding="utf-8"))
    assert local_payload["data_source"]["tushare"]["token"] == ""
    assert local_payload["wyckoff_ai"]["deepseek_api_key"] == ""


def test_runtime_config_bootstrap_never_overwrites_existing_template(tmp_path, monkeypatch):
    paths = paths_for(tmp_path, packaged=True, system="Windows")
    monkeypatch.setattr(local_config, "runtime_paths", lambda: paths)
    resource = paths.resource_root / "config" / "config_local.yaml.template"
    resource.parent.mkdir(parents=True)
    resource.write_text("data_source: {}\n", encoding="utf-8")
    (resource.parent / "config.yaml.template").write_text("data_dir: data\n", encoding="utf-8")
    target = paths.config_root / "config_local.yaml.template"
    target.parent.mkdir(parents=True)
    target.write_text("user_notes: keep-me\n", encoding="utf-8")

    local_config.ensure_runtime_config_templates()

    assert target.read_text(encoding="utf-8") == "user_notes: keep-me\n"
