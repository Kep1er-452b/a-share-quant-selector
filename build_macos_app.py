#!/usr/bin/env python3
"""
Build a lightweight macOS .app launcher for the existing project folder.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path


APP_NAME = "A股量化选股系统"
PROJECT_ROOT = Path(__file__).resolve().parent
APP_PATH = Path("/Applications") / f"{APP_NAME}.app"
EXECUTABLE_NAME = "AStockQuantSelector"
PYTHON_PATH = PROJECT_ROOT / ".venv" / "bin" / "python"
LAUNCHER_PATH = PROJECT_ROOT / "launch_desktop_app.py"
ICON_PATH = PROJECT_ROOT / "assets" / "app_icon.icns"


INFO_PLIST = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>zh_CN</string>
  <key>CFBundleDisplayName</key>
  <string>{APP_NAME}</string>
  <key>CFBundleExecutable</key>
  <string>{EXECUTABLE_NAME}</string>
  <key>CFBundleIdentifier</key>
  <string>local.a-share-quant-selector.launcher</string>
  <key>CFBundleIconFile</key>
  <string>app_icon</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>{APP_NAME}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
"""


def build() -> None:
    if not PYTHON_PATH.exists():
        raise FileNotFoundError(f"未找到项目 Python: {PYTHON_PATH}")
    if not LAUNCHER_PATH.exists():
        raise FileNotFoundError(f"未找到桌面启动器: {LAUNCHER_PATH}")
    if not ICON_PATH.exists():
        raise FileNotFoundError(f"未找到 App 图标: {ICON_PATH}")

    if APP_PATH.exists():
        shutil.rmtree(APP_PATH)

    contents = APP_PATH / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    (contents / "Info.plist").write_text(INFO_PLIST, encoding="utf-8")
    shutil.copy2(ICON_PATH, resources / "app_icon.icns")

    executable = macos / EXECUTABLE_NAME
    executable.write_text(
        f"""#!/bin/bash
set -euo pipefail
PROJECT_ROOT="{PROJECT_ROOT}"
PYTHON="{PYTHON_PATH}"
LAUNCHER="{LAUNCHER_PATH}"
cd "$PROJECT_ROOT"
exec "$PYTHON" "$LAUNCHER"
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Helps Finder refresh metadata after replacing an app bundle.
    os.utime(APP_PATH, None)
    print(f"OK built {APP_PATH}")
    print(f"OK project_root={PROJECT_ROOT}")
    print(f"OK executable={executable}")


if __name__ == "__main__":
    build()
