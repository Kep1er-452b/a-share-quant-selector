# Multi-Market Platform Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cross-platform runtime paths, generic SQLite domain storage, dataset registry/sync primitives, and server-owned market capabilities without changing existing A-share behavior.

**Architecture:** A platform-path authority separates read-only resources from writable runtime data. New domain stores share a small transactional SQLite layer and registry-driven sync protocol. Flask registers focused blueprints while legacy routes remain in `web_server.py`.

**Tech Stack:** Python 3, pathlib, platformdirs, sqlite3, pandas, Flask, pytest.

## Global Constraints

- Do not commit or stage changes.
- Source mode keeps the current repository `data/` behavior.
- Packaged mode creates empty platform runtime directories only.
- Never expose or persist credential values in capability payloads.
- No production code before its focused test fails for the expected reason.

---

## File Structure

- Create `utils/platform_paths.py`: one authority for source/package resource and runtime paths.
- Modify `utils/runtime_paths.py`: delegate outputs to the new authority while preserving `A_SHARE_QUANT_OUTPUT_ROOT`.
- Modify `launch_desktop_app.py`: obtain the pywebview profile from the platform authority.
- Modify `requirements.txt`: add `platformdirs` with a bounded minimum version.
- Create `market_data/models.py`: immutable dataset and capability records.
- Create `market_data/store.py`: schema-versioned SQLite row/state store.
- Create `market_data/catalog.py`: registry validation and lookup.
- Create `market_data/sync_engine.py`: generic job lifecycle and cancellation-aware dataset execution.
- Create `market_data/capabilities.py`: server-owned A-share/Hong Kong capability records.
- Create `web_api/markets.py`: capability and sync-status read routes.
- Modify `web_server.py`: register the new blueprint and shared service container.
- Create focused tests listed in each task.

### Task 1: Cross-Platform Runtime Path Authority

**Files:**
- Create: `utils/platform_paths.py`
- Modify: `utils/runtime_paths.py`
- Modify: `launch_desktop_app.py`
- Modify: `requirements.txt`
- Test: `tests/test_platform_paths.py`
- Test: `tests/test_desktop_launcher.py`

**Interfaces:**
- Produces: `RuntimePaths.for_environment(project_root, packaged, system, environ) -> RuntimePaths`
- Produces: `runtime_paths() -> RuntimePaths`
- Produces fields: `resource_root`, `runtime_root`, `data_root`, `logs_root`, `outputs_root`, `config_root`, `webview_root`

- [ ] **Step 1: Write failing path-matrix tests**

```python
def test_packaged_windows_paths_use_local_app_data(tmp_path):
    paths = RuntimePaths.for_environment(
        project_root=tmp_path / "bundle",
        packaged=True,
        system="Windows",
        environ={"LOCALAPPDATA": str(tmp_path / "LocalAppData")},
    )
    assert paths.runtime_root == tmp_path / "LocalAppData" / "A股量化选股系统"
    assert paths.data_root == paths.runtime_root / "data"
    assert paths.resource_root == tmp_path / "bundle"


def test_source_mode_preserves_repository_data(tmp_path):
    paths = RuntimePaths.for_environment(
        project_root=tmp_path,
        packaged=False,
        system="Darwin",
        environ={},
    )
    assert paths.data_root == tmp_path / "data"
```

- [ ] **Step 2: Run RED verification**

Run: `.venv/bin/pytest -q tests/test_platform_paths.py tests/test_desktop_launcher.py`

Expected: FAIL because `RuntimePaths` and `utils.platform_paths` do not exist.

- [ ] **Step 3: Implement the immutable path authority**

```python
@dataclass(frozen=True)
class RuntimePaths:
    resource_root: Path
    runtime_root: Path
    data_root: Path
    logs_root: Path
    outputs_root: Path
    config_root: Path
    webview_root: Path

    @classmethod
    def for_environment(cls, *, project_root, packaged, system, environ):
        override = str(environ.get("AQS_RUNTIME_ROOT") or "").strip()
        if override:
            runtime_root = Path(override).expanduser()
        elif not packaged:
            runtime_root = Path(project_root)
        elif system == "Windows":
            runtime_root = Path(environ["LOCALAPPDATA"]) / "A股量化选股系统"
        else:
            runtime_root = Path.home() / "Library" / "Application Support" / "A股量化选股系统"
        return cls(
            resource_root=Path(project_root),
            runtime_root=runtime_root,
            data_root=(Path(project_root) / "data") if not packaged else runtime_root / "data",
            logs_root=(Path(project_root) / "logs") if not packaged else runtime_root / "logs",
            outputs_root=runtime_root / "outputs",
            config_root=runtime_root / "config",
            webview_root=runtime_root / "webview",
        )
```

Preserve the existing output override and update the launcher to call
`runtime_paths().webview_root` instead of constructing a macOS path directly.

- [ ] **Step 4: Add `platformdirs>=4.0` and run GREEN verification**

Run: `.venv/bin/pytest -q tests/test_platform_paths.py tests/test_desktop_launcher.py tests/test_runtime_paths.py`

Expected: PASS.

- [ ] **Step 5: Record an uncommitted checkpoint**

Run: `git diff --check && git status --short`

Expected: only intended unstaged files; no staged or committed changes.

### Task 2: Generic Schema-Versioned Domain Store

**Files:**
- Create: `market_data/__init__.py`
- Create: `market_data/store.py`
- Test: `tests/test_market_data_store.py`

**Interfaces:**
- Produces: `DomainStore(db_path: Path)`
- Produces: `upsert_rows(dataset, rows, key_fields, symbol_field=None, date_field=None) -> int`
- Produces: `query_rows(dataset, *, symbol=None, start_date=None, end_date=None, limit=200, offset=0, descending=True) -> list[dict]`
- Produces: `set_sync_state(...)`, `get_sync_state(dataset, scope)`, `health() -> dict`

- [ ] **Step 1: Write failing transactional-store tests**

```python
def test_domain_store_upserts_and_pages_rows(tmp_path):
    store = DomainStore(tmp_path / "domain.sqlite")
    store.upsert_rows(
        "daily",
        [{"ts_code": "00700.HK", "trade_date": "20260710", "close": 420.0}],
        key_fields=("ts_code", "trade_date"),
        symbol_field="ts_code",
        date_field="trade_date",
    )
    assert store.query_rows("daily", symbol="00700.HK", limit=1)[0]["close"] == 420.0
    assert store.health()["integrity"] == "ok"
```

- [ ] **Step 2: Run RED verification**

Run: `.venv/bin/pytest -q tests/test_market_data_store.py`

Expected: FAIL because `DomainStore` does not exist.

- [ ] **Step 3: Implement migrations and WAL-backed storage**

Create schema version 1 with `dataset_rows`, `sync_state`, and `schema_meta`.
Store JSON payloads with indexed `(dataset, symbol, data_date)` columns. Enforce
`limit <= 2000` and `offset >= 0`. Use one transaction per batch.

```python
SCHEMA_VERSION = 1

def _row_key(row, key_fields):
    return "|".join(str(row.get(field) or "") for field in key_fields)
```

- [ ] **Step 4: Run GREEN verification**

Run: `.venv/bin/pytest -q tests/test_market_data_store.py`

Expected: PASS, including rollback, pagination, sync-state, and integrity cases.

- [ ] **Step 5: Record an uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 3: Dataset Registry And Sync Engine

**Files:**
- Create: `market_data/models.py`
- Create: `market_data/catalog.py`
- Create: `market_data/sync_engine.py`
- Test: `tests/test_market_data_sync.py`

**Interfaces:**
- Produces: `DatasetSpec`
- Produces: `DatasetCatalog.register(spec)`, `get(dataset_id)`, `for_domain(domain)`
- Produces: `SyncEngine.run(request, *, cancel_event, emit) -> SyncResult`

- [ ] **Step 1: Write failing catalog and cancellation tests**

```python
def test_catalog_rejects_duplicate_dataset_ids():
    catalog = DatasetCatalog()
    catalog.register(DatasetSpec(dataset_id="hk_basic", domain="hong_kong", method="hk_basic", key_fields=("ts_code",)))
    with pytest.raises(ValueError, match="duplicate dataset"):
        catalog.register(DatasetSpec(dataset_id="hk_basic", domain="hong_kong", method="hk_basic", key_fields=("ts_code",)))


def test_sync_engine_does_not_write_after_cancel(tmp_path):
    cancel = Event()
    cancel.set()
    result = engine.run(SyncRequest(domain="hong_kong", datasets=("hk_basic",)), cancel_event=cancel, emit=events.append)
    assert result.status == "cancelled"
    assert store.query_rows("hk_basic") == []
```

- [ ] **Step 2: Run RED verification**

Run: `.venv/bin/pytest -q tests/test_market_data_sync.py`

Expected: FAIL because the registry and engine do not exist.

- [ ] **Step 3: Implement validated specifications and lifecycle**

`DatasetSpec` must contain dataset ID, domain, method, key fields, optional symbol/date fields, required flag, batch size, freshness, and parameter builder. The engine emits `preflight`, `fetch`, `write`, `quality`, and terminal events. Permission errors on optional specs produce warnings; required failures produce `failed`.

- [ ] **Step 4: Run GREEN verification**

Run: `.venv/bin/pytest -q tests/test_market_data_sync.py`

Expected: PASS for success, warning, failure, cancellation, and cursor update.

- [ ] **Step 5: Record an uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 4: Server-Owned Market Capabilities

**Files:**
- Create: `market_data/capabilities.py`
- Create: `web_api/__init__.py`
- Create: `web_api/markets.py`
- Modify: `web_server.py`
- Test: `tests/test_market_capabilities.py`
- Test: `tests/test_multimarket_web.py`

**Interfaces:**
- Produces: `market_capabilities(store_health) -> dict[str, MarketCapability]`
- Produces API: `GET /api/markets/capabilities`

- [ ] **Step 1: Write failing capability API tests**

```python
def test_market_capabilities_keep_policy_separate(client):
    payload = client.get("/api/markets/capabilities").get_json()
    assert payload["markets"]["a_share"]["policy"] == "a_share"
    assert payload["markets"]["hong_kong"]["policy"] == "hong_kong"
    assert "price_limits" in payload["markets"]["a_share"]["features"]
    assert "price_limits" not in payload["markets"]["hong_kong"]["features"]
```

- [ ] **Step 2: Run RED verification**

Run: `.venv/bin/pytest -q tests/test_market_capabilities.py tests/test_multimarket_web.py`

Expected: 404 or import failure for the new endpoint.

- [ ] **Step 3: Implement immutable capabilities and blueprint registration**

Return display name, currency, pages, grouping modes, strategy support, adjustment support, policy ID, and sanitized health. Register the blueprint once in `web_server.py`; do not move legacy endpoints yet.

- [ ] **Step 4: Run GREEN plus legacy Web regression**

Run: `.venv/bin/pytest -q tests/test_market_capabilities.py tests/test_multimarket_web.py tests/test_web_security.py tests/test_tushare_extension_web.py`

Expected: PASS.

- [ ] **Step 5: Record an uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 5: Packaging Safety Contract

**Files:**
- Create: `utils/package_manifest.py`
- Modify: `build_macos_app.py`
- Test: `tests/test_packaging_safety.py`

**Interfaces:**
- Produces: `package_resource_files(project_root) -> list[Path]`
- Produces: `assert_package_safe(paths) -> None`

- [ ] **Step 1: Write failing exclusion and secret-scan tests**

```python
def test_package_manifest_excludes_runtime_and_secrets(tmp_path):
    files = package_resource_files(PROJECT_ROOT)
    relative = {path.relative_to(PROJECT_ROOT).as_posix() for path in files}
    assert not any(item.startswith("data/") for item in relative)
    assert not any(item.startswith("logs/") for item in relative)
    assert "config/config_local.yaml" not in relative
```

- [ ] **Step 2: Run RED verification**

Run: `.venv/bin/pytest -q tests/test_packaging_safety.py`

Expected: FAIL because the manifest module does not exist.

- [ ] **Step 3: Implement allowlist-based package resources**

Allow code, templates, static assets, icons, and committed config templates. Explicitly reject `data`, `logs`, outputs, ignored config, SQLite, CSV, JSONL, and detected token-like values. Make the macOS builder consume this contract without changing its current bundle-icon behavior.

- [ ] **Step 4: Run GREEN and launcher regression**

Run: `.venv/bin/pytest -q tests/test_packaging_safety.py tests/test_desktop_launcher.py`

Expected: PASS.

- [ ] **Step 5: Plan-level verification**

Run:

```bash
.venv/bin/python -m py_compile utils/platform_paths.py utils/runtime_paths.py market_data/models.py market_data/store.py market_data/catalog.py market_data/sync_engine.py market_data/capabilities.py web_api/markets.py
.venv/bin/pytest -q tests/test_platform_paths.py tests/test_market_data_store.py tests/test_market_data_sync.py tests/test_market_capabilities.py tests/test_multimarket_web.py tests/test_packaging_safety.py tests/test_desktop_launcher.py tests/test_runtime_paths.py
git diff --check
git status --short --branch
```

Expected: all focused tests pass, syntax checks exit 0, no staged changes.

