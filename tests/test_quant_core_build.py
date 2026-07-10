from scripts import build_quant_core


ROOT = build_quant_core.ROOT


def test_resolve_compiler_returns_none_when_no_supported_compiler_exists(monkeypatch):
    monkeypatch.setattr(build_quant_core.shutil, "which", lambda _name: None)

    assert build_quant_core.resolve_compiler() is None


def test_windows_build_command_omits_posix_pic_flag(monkeypatch, tmp_path):
    commands = []

    monkeypatch.setattr(build_quant_core, "BUILD_DIR", tmp_path)
    monkeypatch.setattr(build_quant_core.platform, "system", lambda: "Windows")
    monkeypatch.setattr(build_quant_core, "resolve_compiler", lambda _compiler: "clang")
    monkeypatch.setattr(
        build_quant_core.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    build_quant_core.build()

    assert "-fPIC" not in commands[0]


def test_quant_core_header_marks_public_functions_for_windows_export():
    header = (ROOT / "csrc" / "quant_core.h").read_text(encoding="utf-8")

    assert "#define QC_API __declspec(dllexport)" in header
    assert "QC_API int qc_rolling_mean_forward" in header
