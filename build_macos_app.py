#!/usr/bin/env python3
"""
Build a lightweight macOS .app launcher for the existing project folder.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

from utils.package_manifest import assert_package_safe, package_resource_files


APP_NAME = "A股量化选股系统"
PROJECT_ROOT = Path(__file__).resolve().parent
APP_PATH = Path("/Applications") / f"{APP_NAME}.app"
EXECUTABLE_NAME = "AStockQuantSelector"
PYTHON_PATH = PROJECT_ROOT / ".venv" / "bin" / "python"
LAUNCHER_PATH = PROJECT_ROOT / "launch_desktop_app.py"
ICON_PATH = PROJECT_ROOT / "assets" / "app_icon.icns"
RUNTIME_ICON_NAME = "runtime_icon.png"


def resolve_build_layout(environ=None) -> dict[str, Path]:
    """Resolve the current checkout, interpreter and output without user-specific paths."""

    environ = os.environ if environ is None else environ
    project_root = Path(environ.get("AQS_PROJECT_ROOT") or PROJECT_ROOT).expanduser()
    python = Path(environ.get("AQS_PYTHON") or (project_root / ".venv" / "bin" / "python")).expanduser()
    app_dir = Path(environ.get("AQS_MACOS_APP_DIR") or "/Applications").expanduser()
    return {
        "project_root": project_root,
        "python": python,
        "app_path": app_dir / f"{APP_NAME}.app",
    }


def render_launcher_script(project_root: Path) -> str:
    fallback = str(Path(project_root))
    return f'''#!/bin/bash
set -euo pipefail
APP_BUNDLE="$(cd "$(dirname "$0")/../.." && pwd)"
PROJECT_ROOT="${{AQS_PROJECT_ROOT:-{fallback}}}"
PYTHON="${{AQS_PYTHON:-$PROJECT_ROOT/.venv/bin/python}}"
LAUNCHER="$PROJECT_ROOT/launch_desktop_app.py"
export A_SHARE_QUANT_APP_BUNDLE="$APP_BUNDLE"
cd "$PROJECT_ROOT"
exec "$PYTHON" "$LAUNCHER"
'''


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


def write_runtime_icon(target_path: Path, app_path: Path = APP_PATH) -> None:
    from AppKit import NSBitmapImageRep, NSPNGFileType, NSWorkspace

    workspace = NSWorkspace.sharedWorkspace()
    workspace.noteFileSystemChanged_(str(app_path))
    image = workspace.iconForFile_(str(app_path))
    image.setSize_((1024, 1024))
    representation = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    png_data = representation.representationUsingType_properties_(NSPNGFileType, {})
    if png_data is None:
        raise RuntimeError("无法生成带透明圆角的运行时 App 图标")
    target_path.write_bytes(bytes(png_data))


def sign_app(app_path: Path = APP_PATH) -> None:
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app_path)],
        check=True,
    )


def build() -> None:
    layout = resolve_build_layout()
    project_root = layout["project_root"]
    python_path = layout["python"]
    app_path = layout["app_path"]
    launcher_path = project_root / "launch_desktop_app.py"
    icon_path = project_root / "assets" / "app_icon.icns"
    if not python_path.exists():
        raise FileNotFoundError(f"未找到项目 Python: {python_path}")
    if not launcher_path.exists():
        raise FileNotFoundError(f"未找到桌面启动器: {launcher_path}")
    if not icon_path.exists():
        raise FileNotFoundError(f"未找到 App 图标: {icon_path}")

    package_files = package_resource_files(project_root)
    assert_package_safe(package_files)

    if app_path.exists():
        shutil.rmtree(app_path)

    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    (contents / "Info.plist").write_text(INFO_PLIST, encoding="utf-8")
    shutil.copy2(icon_path, resources / "app_icon.icns")

    executable = macos / EXECUTABLE_NAME
    executable.write_text(render_launcher_script(project_root), encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # NSWorkspace adds a disabled badge to unsigned app icons.
    sign_app(app_path)
    write_runtime_icon(resources / RUNTIME_ICON_NAME, app_path)
    assert_package_safe(
        (path for path in app_path.rglob("*") if path.is_file()),
        root=app_path,
    )
    sign_app(app_path)

    # Helps Finder refresh metadata after replacing an app bundle.
    os.utime(app_path, None)
    print(f"OK built {app_path}")
    print(f"OK project_root={project_root}")
    print(f"OK executable={executable}")


if __name__ == "__main__":
    build()
