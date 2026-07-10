# Mac 鏂扮増杩佺Щ涓?Windows 妗岄潰鍚姩 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 杩佺Щ 2026-07-10 Mac 鏂扮増鍔熻兘鍒?Windows 椤圭洰锛屽苟鍒涘缓鍙屽嚮鍚庝笉鏄剧ず鍛戒护琛岀殑妗岄潰蹇嵎鏂瑰紡銆?
**Architecture:** 灏嗗帇缂╁寘涓粡杩囩瓫閫夌殑婧愮爜鎻愬彇鍒板伐绋嬪鏆傚瓨鍖猴紝鍏堝鍏ヤ笂娓告祴璇曪紝鍐嶅悓姝ヤ笂娓稿姛鑳芥ā鍧楀苟鎭㈠ Windows 鐨勭紪鐮併€佹棩蹇楀拰骞惰宸紓銆傛闈?`.lnk` 鐩存帴杩愯椤圭洰铏氭嫙鐜鐨?`pythonw.exe` 涓?pywebview 鍚姩鍣ㄣ€?
**Tech Stack:** Python銆丗lask銆乸ywebview銆乸ytest銆丳owerShell銆乄indows Shell shortcut銆?
## Global Constraints

- 鍥哄畾鏉ユ簮锛歚C:\Users\13200\Downloads\a-share-quant-selector.zip`銆?- 淇濈暀 `data/`銆乣logs/`銆乣.venv*/`銆乣.git/`銆佹湰鍦伴厤缃€佸嚟鎹笌鏃㈡湁鏈彁浜ゆ敼鍔ㄣ€?- 绂佹杩愯鍏ㄥ競鍦烘洿鏂般€佸彂閫侀€氱煡鎴栨敼鍐欒偂绁ㄦ暟鎹€?- 妗岄潰鍏ュ彛鍥哄畾涓?`A鑲￠噺鍖栭€夎偂绯荤粺.lnk`锛岀洰鏍囧繀椤绘槸 `.venv\Scripts\pythonw.exe`銆?
---

## File structure

- Temporary: `C:\Users\13200\Documents\Windows_quant_system\.migration_staging\mac-20260710\`锛屽彧淇濆瓨绛涢€夊悗鐨勪笂娓告簮鐮佸拰娴嬭瘯銆?- Create: `utils/tushare_ext_store.py`銆乣utils/tushare_ext_sync.py`銆乣utils/tushare_ext_views.py`銆乣utils/tushare_ext_workflow.py`銆乣utils/kline_chart_utils.py`銆?- Modify: `main.py`銆乣web_server.py`銆乣strategy/`銆乣utils/`銆乣web/`銆乣wyckoff_ai/`銆乣wyckoff-second/`銆乣tests/`銆?- Preserve: `launch_desktop_app.py`銆乣quant.ps1`銆乣Start-Quant-Web.bat`銆乣scripts/start_web_windows.ps1`銆乣sitecustomize.py`銆乣utils/console_encoding.py`銆乣utils/runtime_paths.py`銆佹湰鍦伴厤缃拰鐢ㄦ埛鏁版嵁銆?- Create outside repository: `C:\Users\13200\Desktop\A鑲￠噺鍖栭€夎偂绯荤粺.lnk`銆?
### Task 0: Make the optional C-core test path safe without a compiler

**Files:**
- Modify: `scripts/build_quant_core.py`, `tests/test_quant_core_equivalence.py`

**Interfaces:**
- `resolve_compiler(compiler: str | None) -> str | None` returns `None` when neither `clang`, `gcc`, nor `cc` is available.
- `build()` raises an ASCII `RuntimeError` before spawning a missing compiler, while this Windows machine installs LLVM and compiles `build/quant_core/quant_core.dll`.

- [x] **Step 1: Write the missing-compiler test**

Add a test that replaces `build_quant_core.shutil.which` with `lambda _name: None` and asserts `build_quant_core.resolve_compiler() is None`.

- [x] **Step 2: Verify the test fails**

Run `.\.venv\Scripts\python.exe -m pytest -q tests/test_quant_core_equivalence.py -k missing_compiler`.

Expected before implementation: failure because `resolve_compiler()` returns `"clang"`.

- [x] **Step 3: Implement the guarded compiler resolution**

Return `None` after the candidate search has no hit, and make `build()` raise `RuntimeError("No supported C compiler found; install clang, gcc, or cc.")` before it builds a compiler command. In the fixture, call `subprocess.run(..., text=True, capture_output=True, errors="replace")` and use `result.stderr or "no compiler output"` in its skip message.

- [x] **Step 4: Install and expose LLVM/Clang on Windows**

Run `winget install --id LLVM.LLVM -e --silent --accept-package-agreements --accept-source-agreements`; add `C:\Program Files\LLVM\bin` to the current process `PATH` when required, then confirm `clang --version` succeeds.

- [x] **Step 5: Build and prove the C core is active**

Run `.\.venv\Scripts\python.exe scripts/build_quant_core.py`, then run `.\.venv\Scripts\python.exe -m pytest -q tests/test_quant_core_build.py tests/test_quant_core_equivalence.py`.

Expected: `build/quant_core/quant_core.dll` exists; the missing-compiler unit test passes; all C-core equivalence cases pass with no skips or errors.

### Task 1: Stage and audit the upstream release

**Files:**
- Create outside repository: `C:\Users\13200\Documents\Windows_quant_system\.migration_staging\mac-20260710\`
- Read: `C:\Users\13200\Downloads\a-share-quant-selector.zip`

**Interfaces:**
- Consumes archive entries under `a-share-quant-selector/`.
- Produces a filtered upstream source tree for comparison and copy.

- [x] **Step 1: Extract only source and test entries**

Run PowerShell that iterates `System.IO.Compression.ZipFile` entries and skips paths matching `^(\.git/|\.venv[^/]*/|data/|logs/|build/|\.pytest_cache/|__pycache__/|.*\.DS_Store$|.*\.pyc$)`, while writing every remaining `a-share-quant-selector/` file below the staging directory.

- [x] **Step 2: Verify new units exist in staging**

Run `Test-Path` on `utils/tushare_ext_store.py`, `utils/tushare_ext_sync.py`, `utils/tushare_ext_views.py`, `utils/tushare_ext_workflow.py`, and `tests/test_tushare_extension_workflow.py`.

Expected: all five checks return `True`.

### Task 2: Import upstream Tushare and chart functionality

**Files:**
- Create: `utils/tushare_ext_store.py`, `utils/tushare_ext_sync.py`, `utils/tushare_ext_views.py`, `utils/tushare_ext_workflow.py`, `utils/kline_chart_utils.py`
- Modify: `main.py`, `web_server.py`, `utils/error_logging.py`, `strategy/`, `utils/`, `web/`, `wyckoff_ai/`, `wyckoff-second/`, `tests/`
- Test: `tests/test_tushare_extension_store.py`, `tests/test_tushare_extension_sync.py`, `tests/test_tushare_extension_views.py`, `tests/test_tushare_extension_workflow.py`, `tests/test_tushare_extension_web.py`, `tests/test_tushare_extension_frontend_static.py`, `tests/test_kline_chart_markers.py`

**Interfaces:**
- Consumes the upstream staged code and existing Windows support modules.
- Produces the Tushare extension warehouse, sync workflow, API payloads, dashboard/stock-details UI, chart-range controls and key-candle marks.

- [x] **Step 1: Copy upstream test modules before their missing implementation**

Copy the eight named upstream tests to `tests/`, then run `.\.venv\Scripts\python.exe -m pytest -q tests/test_tushare_extension_store.py`.

Expected before implementation: collection fails with `ModuleNotFoundError` for `utils.tushare_ext_store`.

- [x] **Step 2: Copy the upstream functional tree without protected runtime paths**

Copy staged contents of `strategy/`, `utils/`, `web/`, `wyckoff_ai/`, `wyckoff-second/`, `tests/`, `csrc/`, `assets/`, `benchmarks/` and `prompts/`; also copy `main.py`, `web_server.py`, and `requirements.txt`. Before the copy, save all protected Windows files from the File structure section to a temporary backup; immediately restore them afterwards.

- [x] **Step 3: Reapply Windows entry-point compatibility**

In `main.py` and `web_server.py`, add the following exactly once before Chinese console output or logging:

```python
from utils.console_encoding import configure_utf8_stdio

configure_utf8_stdio()
```

In `utils/error_logging.py`, retain this Windows-safe runtime log binding:

```python
from utils.runtime_paths import runtime_logs_dir

LOG_DIR = runtime_logs_dir()
```

Restore the pre-copy `platform.system() == 'Windows'` process-pool branch in `web_server.py`.

- [x] **Step 4: Verify the imported feature set**

Run `.\.venv\Scripts\python.exe -m pytest -q tests/test_tushare_extension_store.py tests/test_tushare_extension_sync.py tests/test_tushare_extension_views.py tests/test_tushare_extension_workflow.py tests/test_tushare_extension_web.py tests/test_tushare_extension_frontend_static.py tests/test_kline_chart_markers.py tests/test_desktop_launcher.py tests/test_runtime_paths.py`.

Expected: all selected tests pass.

### Task 3: Create the no-console desktop entry

**Files:**
- Read: `launch_desktop_app.py`
- Create outside repository: `C:\Users\13200\Desktop\A鑲￠噺鍖栭€夎偂绯荤粺.lnk`

**Interfaces:**
- Consumes `C:\Users\13200\Documents\Windows_quant_system\a-share-quant-selector\.venv\Scripts\pythonw.exe` and `launch_desktop_app.py`.
- Produces a shortcut whose working directory is the project root and whose argument is the quoted launcher path.

- [x] **Step 1: Verify launcher prerequisites**

Run `.\.venv\Scripts\python.exe launch_desktop_app.py --check --check-webview`.

Expected: `OK` reports for project, interpreter, config, web address, log file and pywebview.

- [x] **Step 2: Create shortcut through Windows Shell**

Use `WScript.Shell.CreateShortcut()` and set: `TargetPath` to `.venv\Scripts\pythonw.exe`; `Arguments` to the quoted `launch_desktop_app.py`; `WorkingDirectory` to the project root; and description to `鎵撳紑 A 鑲￠噺鍖栭€夎偂绯荤粺`.

- [x] **Step 3: Read shortcut properties back**

Use `WScript.Shell.CreateShortcut($desktopPath)` to confirm target ends in `pythonw.exe`, arguments include `launch_desktop_app.py`, and working directory equals the project root.

### Task 4: Verify the migrated desktop system

**Files:**
- Verify all migrated source, tests and the desktop shortcut.

**Interfaces:**
- Consumes the merged project and shortcut.
- Produces a verified Windows-ready desktop system while leaving market data unchanged.

- [x] **Step 1: Check syntax**

Run `.\.venv\Scripts\python.exe -m py_compile main.py web_server.py launch_desktop_app.py utils\tushare_ext_store.py utils\tushare_ext_sync.py utils\tushare_ext_views.py utils\tushare_ext_workflow.py utils\kline_chart_utils.py` and `node --check web\static\js\app.js`.

Expected: both commands exit successfully.

- [x] **Step 2: Run complete regression and repository checks**

Run `.\.venv\Scripts\python.exe -m pytest -q`, then run repository whitespace and status checks.

Expected: full test suite passes; whitespace check has no output; status contains migration changes plus pre-existing user changes.

- [x] **Step 3: Start once via shortcut and confirm local health**

Run the shortcut, wait for startup, and request `http://127.0.0.1:5080/api/system_status`.

Expected: HTTP `200`; close the test instance only through the application Exit control.
