#!/usr/bin/env python3
"""Build the small C quant core shared library with the platform compiler."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "csrc" / "quant_core.c"
BUILD_DIR = ROOT / "build" / "quant_core"


def library_name() -> str:
    system = platform.system()
    if system == "Darwin":
        return "libquant_core.dylib"
    if system == "Windows":
        return "quant_core.dll"
    return "libquant_core.so"


def resolve_compiler(compiler: str | None = None) -> str:
    if compiler:
        if shutil.which(compiler):
            return compiler
        if compiler == "clang" and shutil.which("gcc"):
            return "gcc"
        return compiler

    for candidate in ("clang", "gcc", "cc"):
        if shutil.which(candidate):
            return candidate
    return "clang"


def build(compiler: str | None = None, extra_cflags: list[str] | None = None) -> Path:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    output = BUILD_DIR / library_name()
    system = platform.system()
    compiler = resolve_compiler(compiler)
    command = [compiler, "-O3", "-std=c11", "-Wall", "-Wextra", "-fPIC", "-I", str(ROOT / "csrc")]
    if extra_cflags:
        command.extend(extra_cflags)
    if system == "Darwin":
        command.extend(["-dynamiclib", str(SOURCE), "-o", str(output)])
    elif system == "Windows":
        command.extend(["-shared", str(SOURCE), "-o", str(output)])
    else:
        command.extend(["-shared", str(SOURCE), "-lm", "-o", str(output)])

    subprocess.run(command, cwd=ROOT, check=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the quant_core shared library.")
    parser.add_argument("--compiler", default=None, help="C compiler executable to use. Defaults to clang, then gcc.")
    parser.add_argument("--extra-cflag", action="append", default=[], help="Additional C compiler flag.")
    args = parser.parse_args()
    output = build(args.compiler, args.extra_cflag)
    print(output)


if __name__ == "__main__":
    main()
