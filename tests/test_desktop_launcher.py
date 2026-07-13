from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import launch_desktop_app


def test_run_gui_passes_transparent_bundle_icon_to_pywebview(tmp_path, monkeypatch):
    start_kwargs = {}
    fake_window = SimpleNamespace(load_url=lambda _url: None, load_html=lambda _html: None)
    fake_webview = SimpleNamespace(
        create_window=lambda *_args, **_kwargs: fake_window,
        start=lambda **kwargs: start_kwargs.update(kwargs),
    )

    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr(launch_desktop_app, "configure_local_proxy_bypass", lambda: None)
    monkeypatch.setattr(launch_desktop_app, "setup_logging", lambda: None)
    monkeypatch.setattr(launch_desktop_app.threading, "Thread", FakeThread)
    app_bundle = tmp_path / "A股量化选股系统.app"
    runtime_icon = (
        app_bundle
        / "Contents"
        / "Resources"
        / launch_desktop_app.RUNTIME_ICON_NAME
    )
    runtime_icon.parent.mkdir(parents=True)
    runtime_icon.write_bytes(b"png")
    monkeypatch.setenv(launch_desktop_app.APP_BUNDLE_ENV, str(app_bundle))
    monkeypatch.setattr(launch_desktop_app, "desktop_storage_path", lambda: tmp_path / "webview-storage")

    assert launch_desktop_app.run_gui() == 0
    assert start_kwargs == {
        "debug": False,
        "icon": str(runtime_icon),
        "private_mode": False,
        "storage_path": str(tmp_path / "webview-storage"),
    }


def test_run_gui_does_not_pass_opaque_icns_without_app_bundle(monkeypatch):
    start_kwargs = {}
    fake_window = SimpleNamespace(load_url=lambda _url: None, load_html=lambda _html: None)
    fake_webview = SimpleNamespace(
        create_window=lambda *_args, **_kwargs: fake_window,
        start=lambda **kwargs: start_kwargs.update(kwargs),
    )

    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.delenv(launch_desktop_app.APP_BUNDLE_ENV, raising=False)
    monkeypatch.setattr(launch_desktop_app, "configure_local_proxy_bypass", lambda: None)
    monkeypatch.setattr(launch_desktop_app, "setup_logging", lambda: None)
    monkeypatch.setattr(launch_desktop_app, "desktop_storage_path", lambda: Path("/tmp/aqs-webview-storage"))
    monkeypatch.setattr(launch_desktop_app.threading, "Thread", FakeThread)

    assert launch_desktop_app.run_gui() == 0
    assert start_kwargs == {
        "debug": False,
        "private_mode": False,
        "storage_path": "/tmp/aqs-webview-storage",
    }


def test_desktop_storage_path_comes_from_platform_authority(tmp_path, monkeypatch):
    expected = tmp_path / "runtime" / "webview"
    monkeypatch.setattr(
        launch_desktop_app,
        "runtime_paths",
        lambda: SimpleNamespace(webview_root=expected),
    )

    assert launch_desktop_app.desktop_storage_path() == expected


def test_launcher_logs_come_from_platform_authority(tmp_path, monkeypatch):
    expected = tmp_path / "runtime" / "logs"
    monkeypatch.setattr(
        launch_desktop_app,
        "runtime_paths",
        lambda: SimpleNamespace(logs_root=expected),
    )

    log_dir, log_file, incident_dir = launch_desktop_app.launcher_log_paths()

    assert log_dir == expected
    assert log_file == expected / "desktop_app_launcher.log"
    assert incident_dir == expected / "incidents"


def test_launcher_load_config_uses_runtime_aware_default(monkeypatch):
    calls = []
    monkeypatch.setattr(launch_desktop_app, "yaml", object())
    monkeypatch.setattr(launch_desktop_app, "DEFAULT_CONFIG", Path("missing-resource-config"))

    import utils.local_config as local_config

    monkeypatch.setattr(
        local_config,
        "load_config_file",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"web": {"port": 5080}},
    )

    assert launch_desktop_app.load_config()["web"]["port"] == 5080
    assert calls == [((), {})]


def test_setup_logging_reserves_runtime_roots(tmp_path, monkeypatch):
    calls = []
    paths = SimpleNamespace(
        logs_root=tmp_path / "logs",
        ensure_writable_roots=lambda: calls.append("prepared"),
    )
    monkeypatch.setattr(launch_desktop_app, "runtime_paths", lambda: paths)

    launch_desktop_app.setup_logging()

    assert calls == ["prepared"]
