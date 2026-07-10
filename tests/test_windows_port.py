import web_server
from utils import error_logging
from utils.runtime_paths import runtime_logs_dir


def test_windows_uses_process_selection_backend(monkeypatch):
    monkeypatch.setattr(web_server.platform, "system", lambda: "Windows")
    monkeypatch.setattr(web_server, "_load_config", lambda: {})

    settings = web_server._get_web_selection_settings()

    assert settings["backend"] == "process"


def test_web_and_error_logs_use_the_runtime_log_directory():
    assert web_server.LOG_DIR == runtime_logs_dir()
    assert error_logging.LOG_DIR == runtime_logs_dir()
