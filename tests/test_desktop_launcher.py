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

    assert launch_desktop_app.run_gui() == 0
    assert start_kwargs == {
        "debug": False,
        "icon": str(runtime_icon),
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
    monkeypatch.setattr(launch_desktop_app.threading, "Thread", FakeThread)

    assert launch_desktop_app.run_gui() == 0
    assert start_kwargs == {"debug": False}
