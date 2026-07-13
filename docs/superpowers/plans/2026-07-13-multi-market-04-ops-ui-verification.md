# Operations, Domain Workspaces, And Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the Futures, Macro, Industry, and System workspaces; add searchable structured operations data and performance controls; verify the complete multi-market system and update the durable handoff.

**Architecture:** Structured events flow to rotating JSONL and indexed `ops.sqlite`. The System workspace queries bounded APIs for tasks, events, health, and performance. Domain workspaces use isolated JS controllers and shared chart/state components. Final verification covers Python, JavaScript, browser behavior, provider probes, packaging safety, and full regression.

**Tech Stack:** Python, sqlite3, Flask, vanilla JavaScript, ECharts, CSS, pytest, browser interaction.

## Global Constraints

- Complete plans 01-03 first.
- Do not commit, stage, push, or create a PR.
- Redact secrets before every persisted event or diagnostic export.
- Active tasks are never pruned.
- Logs cannot delete or compact market data.
- Keep all page queries and DOM row counts bounded.
- Final success claims require fresh full verification evidence.

---

### Task 1: Structured Event Store And Rotating JSONL

**Files:**
- Create: `ops/__init__.py`
- Create: `ops/events.py`
- Create: `ops/store.py`
- Create: `ops/logging.py`
- Test: `tests/test_ops_events.py`

**Interfaces:**
- Produces: `OpsEvent`
- Produces: `OpsStore.append(event)`, `query(filters, limit, offset)`, `prune(now, policy)`
- Produces: `EventLogger.emit(...)`

- [ ] **Step 1: Write failing redaction, filtering, and retention tests**

```python
def test_ops_event_redacts_tokens_before_storage(tmp_path):
    logger = EventLogger(OpsStore(tmp_path / "ops.sqlite"), tmp_path / "events.jsonl")
    logger.emit(message="request failed", details={"token": "secret-value", "status": 403})
    stored = logger.store.query(limit=1)["items"][0]
    assert "secret-value" not in json.dumps(stored)
    assert stored["details"]["token"] == "[REDACTED]"


def test_active_task_events_survive_pruning(store):
    store.prune(now=NOW, policy=RetentionPolicy(task_days=30, performance_days=7), active_job_ids={"active-1"})
    assert store.query(job_id="active-1", limit=10)["total"] > 0
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_ops_events.py`

- [ ] **Step 3: Implement structured events and storage**

Index time, severity, domain, market, module, job ID, dataset, symbol, and error
code. Rotate JSONL by size/date with bounded files. Redact known secret keys and
token-shaped strings before both sinks.

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/pytest -q tests/test_ops_events.py`

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 2: Task Bridge, Health, Performance, And Diagnostics

**Files:**
- Create: `ops/tasks.py`
- Create: `ops/health.py`
- Create: `ops/performance.py`
- Create: `ops/diagnostics.py`
- Modify: `web_server.py`
- Test: `tests/test_ops_health.py`
- Test: `tests/test_ops_diagnostics.py`

**Interfaces:**
- Produces: `TaskRegistry.snapshot()`, `HealthService.snapshot()`, `PerformanceRecorder.summary()`, `DiagnosticExporter.export(filters) -> Path`

- [ ] **Step 1: Write failing health and sanitized-export tests**

```python
def test_health_reports_frequency_aware_freshness(health_service):
    payload = health_service.snapshot()
    assert payload["datasets"]["cn_gdp"]["frequency"] == "quarterly"
    assert payload["datasets"]["cn_gdp"]["freshness"] != "stale_daily"


def test_diagnostic_export_contains_no_credentials(exporter):
    archive = exporter.export(job_ids=["job-1"])
    assert b"DEEPSEEK_API_KEY" not in archive.read_bytes()
    assert b"TUSHARE_TOKEN" not in archive.read_bytes()
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_ops_health.py tests/test_ops_diagnostics.py`

- [ ] **Step 3: Implement bounded snapshots and diagnostic bundles**

Bridge existing update, selection, Wyckoff, diagnostic, and new-domain sync job
maps into one read-only task snapshot. Record API count/latency, queue depth,
database duration, payload size, cache hit rate, disk usage, and retained event
counts. Export only selected bounded evidence.

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/pytest -q tests/test_ops_health.py tests/test_ops_diagnostics.py tests/test_update_diagnostics.py`

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 3: Operations APIs

**Files:**
- Create: `web_api/ops.py`
- Modify: `web_server.py`
- Test: `tests/test_ops_web.py`
- Test: `tests/test_web_security.py`

**Interfaces:**
- Produces: `GET /api/ops/tasks`, `/events`, `/health`, `/performance`
- Produces: `POST /api/ops/diagnostics/export`

- [ ] **Step 1: Write failing filter and security tests**

```python
def test_ops_events_support_bounded_structured_filters(client):
    response = client.get("/api/ops/events?market=hong_kong&severity=warning&limit=50")
    assert response.status_code == 200
    assert response.get_json()["limit"] == 50


def test_diagnostic_export_requires_session_token(client):
    assert client.post("/api/ops/diagnostics/export", json={}).status_code == 403
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_ops_web.py tests/test_web_security.py`

- [ ] **Step 3: Implement validated bounded APIs**

Reject unknown sort/filter fields, limit `limit` to 500, and return download
metadata without exposing local filesystem paths. Reuse current session-token
validation for export.

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/pytest -q tests/test_ops_web.py tests/test_web_security.py`

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 4: Futures, Macro, And Industry Front-End Workspaces

**Files:**
- Create: `web/static/js/futures_workspace.js`
- Create: `web/static/js/macro_workspace.js`
- Create: `web/static/js/industry_workspace.js`
- Modify: `web/templates/index.html`
- Modify: `web/static/css/style.css`
- Modify: `web/static/js/app.js`
- Test: `tests/test_domain_frontend_static.py`

**Interfaces:**
- Produces workspace controllers with `mount()`, `activate()`, `deactivate()`, `refresh()`.
- Industry controller calls `window.quantEquityRouter.openInstrument(...)`.

- [ ] **Step 1: Write failing workspace and deep-link static tests**

```python
def test_dedicated_domain_pages_exist():
    html = INDEX_HTML.read_text(encoding="utf-8")
    for page in ("futures-page", "macro-page", "industry-page", "system-page"):
        assert f'id="{page}"' in html


def test_industry_workspace_uses_shared_equity_router():
    js = INDUSTRY_JS.read_text(encoding="utf-8")
    assert "quantEquityRouter.openInstrument" in js
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_domain_frontend_static.py`

- [ ] **Step 3: Implement isolated lazy controllers**

Futures provides contract/product filters and K-line. Macro provides family
navigation, series comparison, unit metadata, and exact table view. Industry
provides Market Industry / Industrial Cycle modes, rankings, members, K-lines,
cycle charts, and instrument deep links. Controllers abort stale requests and
stop timers on deactivate.

- [ ] **Step 4: Run GREEN and syntax checks**

Run:

```bash
node --check web/static/js/futures_workspace.js
node --check web/static/js/macro_workspace.js
node --check web/static/js/industry_workspace.js
node --check web/static/js/app.js
.venv/bin/pytest -q tests/test_domain_frontend_static.py
```

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 5: System Workspace

**Files:**
- Create: `web/static/js/system_workspace.js`
- Modify: `web/templates/index.html`
- Modify: `web/static/css/style.css`
- Test: `tests/test_ops_frontend_static.py`

**Interfaces:**
- Produces task/event/health/performance/config tabs.
- Produces event filter serialization and task cancellation hooks.

- [ ] **Step 1: Write failing System UI contract tests**

```python
def test_system_workspace_has_required_views():
    html = INDEX_HTML.read_text(encoding="utf-8")
    for view in ("ops-tasks", "ops-events", "ops-health", "ops-performance", "ops-config"):
        assert f'id="{view}"' in html
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_ops_frontend_static.py`

- [ ] **Step 3: Implement the bounded operations console**

Poll active tasks only while System or a global task indicator is visible.
Paginate events server-side. Make symbols clickable through the equity router.
Display credential source/presence but never credential value. Provide empty,
loading, warning, and error states.

- [ ] **Step 4: Run GREEN and syntax checks**

Run:

```bash
node --check web/static/js/system_workspace.js
.venv/bin/pytest -q tests/test_ops_frontend_static.py
```

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 6: Performance Hardening And Listener/Cache Bounds

**Files:**
- Modify: `market_data/store.py`
- Modify: `market_data/services.py`
- Modify: `web/static/js/app.js`
- Modify: new workspace controllers
- Test: `tests/test_multimarket_performance.py`

**Interfaces:**
- Bounded list APIs and DOM render sizes.
- Workspace controllers expose listener/timer cleanup.

- [ ] **Step 1: Write failing query and lifecycle budget tests**

```python
def test_event_query_uses_index_and_caps_limit(ops_store):
    plan = ops_store.explain_query(severity="warning", market="hong_kong", limit=500)
    assert "USING INDEX" in plan


def test_hidden_workspace_does_not_refetch(browser_contract):
    assert browser_contract.max_hidden_workspace_requests == 0
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_multimarket_performance.py`

- [ ] **Step 3: Add indexes, request aborts, cache signatures, and lifecycle cleanup**

Verify actual query plans. Cap K-lines, macro points, instrument rows, industry
members, and event rows. Ensure repeated activation does not duplicate event
listeners, intervals, ECharts instances, or fetches.

- [ ] **Step 4: Run GREEN and benchmark smoke**

Run: `.venv/bin/pytest -q tests/test_multimarket_performance.py`

Expected: all budgets pass with deterministic fixtures.

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 7: Real Browser Verification And Copy Audit

**Files:**
- Modify only files needed for defects found during verification.

- [ ] **Step 1: Start the local Web app**

Run: `.venv/bin/python main.py web --host 127.0.0.1 --port 5080`

- [ ] **Step 2: Verify the desktop flows in a browser**

Check:

- A-share/Hong Kong switch updates every equity surface.
- Industry member opens correct K-line and browser back restores state.
- Futures contract search and K-line.
- Macro family/series comparison and units.
- Industry dual-mode switch.
- System task/event/health/performance tabs.
- Empty, loading, warning, error, and syncing states.
- Pure-black canvas, vivid `#FF3131` red, vivid `#00FF41` green, and orange actions.
- Keyboard focus and 1280 px desktop layout.
- Narrow viewport single-column collapse.
- No browser console errors.

- [ ] **Step 3: Run the taste-skill preflight only where applicable**

Audit interaction contrast, shape consistency, reduced motion, mobile collapse,
loading/empty/error states, copy quality, and listener cleanup. Do not apply its
landing-page rules to this dense terminal. The user's pure-black Bloomberg-style
direction overrides the skill's general near-black guidance.

- [ ] **Step 4: Fix observed defects through TDD**

For each defect, add a failing focused test, reproduce the failure, implement the
smallest fix, rerun the focused test, and repeat browser verification.

### Task 8: Full Verification And AGENTS Handoff

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Run syntax and focused suites**

Run all `py_compile`, `node --check`, and focused test commands from plans 01-04.

- [ ] **Step 2: Run full regression**

Run: `.venv/bin/pytest -q`

Expected: zero failures.

- [ ] **Step 3: Run repository checks**

Run:

```bash
git diff --check
git status --short --branch
git diff --stat
```

Expected: no whitespace errors, no staged files, no commit created.

- [ ] **Step 4: Update durable handoff**

Update `AGENTS.md` with the current HEAD baseline, new architecture, data
invariants, cross-platform empty-package rule, API-token rule, verification
commands, live-probe limits, and fresh verification evidence. Do not duplicate
the full commit history.

- [ ] **Step 5: Re-run final verification after AGENTS edit**

Run:

```bash
.venv/bin/pytest -q
node --check web/static/js/app.js
git diff --check
git status --short --branch
```

Only after fresh successful output report completion. Leave every file
uncommitted for the user.

