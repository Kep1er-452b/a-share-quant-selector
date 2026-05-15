#!/usr/bin/env python3
"""
macOS desktop launcher for the A-share quant selector web UI.

This file intentionally reuses the existing Flask web app and project venv.
It is a convenience shell around the current system, not a replacement for it.
"""
from __future__ import annotations

import argparse
import html
import logging
import os
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover - surfaced in runtime validation
    yaml = None


APP_NAME = "A股量化选股系统"
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "desktop_app_launcher.log"


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_config() -> dict:
    if yaml is None or not DEFAULT_CONFIG.exists():
        return {}
    from utils.local_config import load_config_file

    return load_config_file(DEFAULT_CONFIG)


def validate_environment(require_webview: bool = False) -> list[str]:
    errors = []
    checks = [
        (PROJECT_ROOT, "项目目录不存在"),
        (PROJECT_ROOT / ".venv" / "bin" / "python", "项目 .venv/bin/python 不存在"),
        (PROJECT_ROOT / "web_server.py", "web_server.py 不存在"),
        (PROJECT_ROOT / "web" / "templates" / "index.html", "Web 首页模板不存在"),
        (DEFAULT_CONFIG, "config/config.yaml 不存在"),
    ]
    for path, message in checks:
        if not path.exists():
            errors.append(f"{message}: {path}")

    if require_webview:
        try:
            import webview  # noqa: F401
        except Exception as exc:
            errors.append(f"pywebview 无法导入: {exc}")

    return errors


def status_html(title: str, body: str, detail: str = "") -> str:
    escaped_detail = html.escape(detail)
    detail_block = f"<pre>{escaped_detail}</pre>" if escaped_detail else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <style>
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
      background: #111;
      color: #f5f5f5;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
    }}
    main {{
      width: min(720px, calc(100vw - 48px));
      border: 1px solid #333;
      padding: 28px;
      background: #181818;
    }}
    h1 {{ margin: 0 0 16px; font-size: 24px; }}
    p {{ line-height: 1.65; color: #ddd; }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #050505;
      border: 1px solid #303030;
      padding: 14px;
      color: #ffcc66;
      max-height: 360px;
      overflow: auto;
    }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    <p>{html.escape(body)}</p>
    {detail_block}
  </main>
</body>
</html>"""


def wait_for_server(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status < 500:
                    return True
        except Exception:
            time.sleep(0.35)
    return False


class DesktopApi:
    def __init__(self):
        self.window = None

    def bind_window(self, window) -> None:
        self.window = window

    def toggle_heatmap_fullscreen(self) -> dict:
        if self.window is None:
            return {"success": False, "error": "窗口尚未就绪"}
        self.window.toggle_fullscreen()
        return {"success": True}


def resolve_web_url() -> tuple[str, int, str]:
    os.chdir(PROJECT_ROOT)
    from web_server import _load_config, _resolve_web_address

    config = _load_config(str(DEFAULT_CONFIG))
    host, port = _resolve_web_address(config=config)
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return host, int(port), f"http://{display_host}:{int(port)}"


def start_server(host: str, port: int) -> None:
    os.chdir(PROJECT_ROOT)
    from web_server import run_web_server

    run_web_server(host=host, port=port, debug=False, config=load_config(), auto_port=False)


def launch_backend() -> tuple[str, threading.Thread]:
    errors = validate_environment(require_webview=False)
    if errors:
        raise RuntimeError("\n".join(errors))

    host, port, url = resolve_web_url()
    logging.info("Resolved web UI URL: %s", url)

    server_thread = threading.Thread(
        target=start_server,
        args=(host, port),
        name="quant-web-server",
        daemon=True,
    )
    server_thread.start()

    if not wait_for_server(url, timeout=35):
        raise RuntimeError(f"Web 服务未在预期时间内就绪: {url}\n日志文件: {LOG_FILE}")

    return url, server_thread


def run_check(require_webview: bool = False) -> int:
    setup_logging()
    errors = validate_environment(require_webview=require_webview)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1

    host, port, url = resolve_web_url()
    print(f"OK project_root={PROJECT_ROOT}")
    print(f"OK python={PROJECT_ROOT / '.venv' / 'bin' / 'python'}")
    print(f"OK config={DEFAULT_CONFIG}")
    print(f"OK resolved_host={host}")
    print(f"OK resolved_port={port}")
    print(f"OK url={url}")
    print(f"OK log_file={LOG_FILE}")
    return 0


def run_smoke_test() -> int:
    setup_logging()
    try:
        url, _thread = launch_backend()
        print(f"OK server_ready={url}")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


def run_gui() -> int:
    setup_logging()
    logging.info("Starting %s desktop launcher at %s", APP_NAME, datetime.now().isoformat())

    try:
        import webview
    except Exception as exc:
        setup_logging()
        print(f"pywebview 无法导入: {exc}")
        return 1

    api = DesktopApi()
    window = webview.create_window(
        APP_NAME,
        html=status_html(APP_NAME, "正在启动本地 Web 服务，请稍候...", f"日志文件: {LOG_FILE}"),
        js_api=api,
        width=1440,
        height=920,
        min_size=(1100, 720),
    )
    api.bind_window(window)

    def boot() -> None:
        try:
            url, _thread = launch_backend()
            logging.info("Web UI is ready: %s", url)
            window.load_url(url)
        except Exception:
            detail = traceback.format_exc()
            logging.error("Desktop launcher failed\n%s", detail)
            window.load_html(status_html(
                "启动失败",
                "桌面 App 没有改动原系统。你仍然可以回到项目文件夹，用终端方式启动 Web。",
                f"{detail}\n日志文件: {LOG_FILE}",
            ))

    threading.Thread(target=boot, name="desktop-launcher-boot", daemon=True).start()
    webview.start(debug=False)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} 桌面启动器")
    parser.add_argument("--check", action="store_true", help="检查启动环境，不打开 GUI")
    parser.add_argument("--check-webview", action="store_true", help="检查 pywebview 是否可导入")
    parser.add_argument("--smoke-test", action="store_true", help="启动后端并确认首页可访问，不打开 GUI")
    args = parser.parse_args()

    if args.check or args.check_webview:
        return run_check(require_webview=args.check_webview)
    if args.smoke_test:
        return run_smoke_test()
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
