# Market-Aware Equity Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the entire Equities workspace follow one validated A-share/Hong Kong context and provide Hong Kong-compatible overview, instruments, selection, watchlist, heatmap, finance, and Wyckoff flows.

**Architecture:** A server-owned equity policy registry supplies instrument readers, filters, strategy capabilities, and display semantics. The browser stores only the selected market ID and routes all equity reads through market-explicit APIs. Hash routes provide reversible instrument deep links.

**Tech Stack:** Python, pandas, Flask, vanilla JavaScript, ECharts, CSS, pytest.

## Global Constraints

- Complete plans 01 and 02 first.
- Do not commit or stage changes.
- Existing A-share endpoints remain compatible during migration.
- Hong Kong never inherits A-share ST, board, IPO, or price-limit policy.
- Only market-neutral calculations may be shared.
- All UI uses the approved pure-black industrial token system.

---

### Task 1: Equity Policy Registry And Data Readers

**Files:**
- Create: `market_data/equity_policy.py`
- Modify: `utils/selection_worker.py`
- Modify: `utils/strategy_labels.py`
- Test: `tests/test_equity_market_policy.py`

**Interfaces:**
- Produces: `equity_policy(market_id) -> EquityPolicy`
- Produces: `EquityPolicy.reader`, `instrument_filter`, `strategy_capabilities`, `display_rules`
- Modifies: selection worker accepts explicit `market_id` and `reader`

- [ ] **Step 1: Write failing policy-isolation tests**

```python
def test_hong_kong_policy_does_not_apply_a_share_st_filter():
    policy = equity_policy("hong_kong")
    assert policy.is_instrument_allowed({"symbol": "00005.HK", "name": "汇丰控股"})
    assert policy.display_rules.price_limit_model is None


def test_a_share_policy_retains_existing_limit_model():
    assert equity_policy("a_share").display_rules.price_limit_model == "a_share_board_rules"
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_equity_market_policy.py`

- [ ] **Step 3: Implement explicit policies and reader protocol**

Define a reader protocol with `list_instruments`, `read_analysis_frame`, and
`instrument_metadata`. Wrap the current CSV manager for A-share and the Hong
Kong service for Hong Kong. Strategy capabilities declare `market_neutral`,
`a_share_only`, or `hong_kong` plus parameter overrides.

- [ ] **Step 4: Run GREEN and selection regression**

Run: `.venv/bin/pytest -q tests/test_equity_market_policy.py tests/test_selection_equivalence.py`

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 2: Market-Explicit Equity APIs

**Files:**
- Create: `web_api/equities.py`
- Modify: `web_server.py`
- Test: `tests/test_equities_web.py`

**Interfaces:**
- Produces: overview, instruments, instrument detail, heatmap, selection options/start/status, watchlist, and Wyckoff market-explicit routes.

- [ ] **Step 1: Write failing cross-market API tests**

```python
def test_hong_kong_stock_detail_formats_hkd(client):
    payload = client.get("/api/equities/hong_kong/instrument/00700.HK").get_json()
    assert payload["market"] == "hong_kong"
    assert payload["currency"] == "HKD"


def test_selection_rejects_a_share_only_strategy_for_hong_kong(client, session_headers):
    response = client.post(
        "/api/equities/hong_kong/selection/start",
        json={"strategies": ["B1 V2.42.61"]},
        headers=session_headers,
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_equities_web.py`

- [ ] **Step 3: Implement market-explicit read and job routes**

Use capability checks before each route. Preserve the existing job lifecycle,
selection warning reporting, and security validation. Keep legacy A-share routes
as adapters until the front end is migrated.

- [ ] **Step 4: Run GREEN and legacy Web regression**

Run: `.venv/bin/pytest -q tests/test_equities_web.py tests/test_tushare_extension_web.py tests/test_web_security.py`

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 3: Hong Kong Selection, Watchlist, Heatmap, And Wyckoff

**Files:**
- Modify: `utils/selection_worker.py`
- Modify: `wyckoff_ai/data.py`
- Modify: `wyckoff_ai/pipeline.py`
- Modify: `web_server.py`
- Test: `tests/test_hong_kong_equity_flows.py`

**Interfaces:**
- Selection results include `market` and canonical symbol.
- Watchlist entries use composite identity `(market, symbol)`.
- Wyckoff requests accept explicit market and reader.

- [ ] **Step 1: Write failing flow tests**

```python
def test_watchlist_allows_same_numeric_code_in_different_markets(watchlist):
    watchlist.add("a_share", "000001.SZ")
    watchlist.add("hong_kong", "00001.HK")
    assert len(watchlist.list_all()) == 2


def test_hong_kong_wyckoff_uses_hong_kong_reader(fake_hk_reader):
    payload = build_wyckoff_input("00700.HK", market="hong_kong", reader=fake_hk_reader)
    assert payload["market"] == "hong_kong"
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_hong_kong_equity_flows.py`

- [ ] **Step 3: Implement market-aware flows**

Add market to persisted watchlist entries with backward-compatible A-share
migration. Heatmap uses validated industry grouping or honest segment/performance
grouping. Wyckoff shares supply-demand analysis but uses market-aware metadata,
currency, and source labels.

- [ ] **Step 4: Run GREEN and existing Wyckoff/watchlist regressions**

Run: `.venv/bin/pytest -q tests/test_hong_kong_equity_flows.py tests/test_wyckoff_data.py tests/test_wyckoff_pipeline.py tests/test_market_pulse.py`

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 4: Equity Market Context And Hash Router

**Files:**
- Create: `web/static/js/market_context.js`
- Create: `web/static/js/equity_router.js`
- Modify: `web/templates/index.html`
- Modify: `web/static/js/app.js`
- Test: `tests/test_multimarket_frontend_static.py`

**Interfaces:**
- Produces global: `window.quantMarketContext`
- Produces: `setMarket(marketId)`, `currentMarket()`, `openInstrument(market, symbol, sourceState)`, `restoreSourceState()`

- [ ] **Step 1: Write failing static-contract tests**

```python
def test_equity_market_switch_and_router_are_loaded():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "market_context.js" in html
    assert "equity_router.js" in html
    assert 'id="equity-market-switch"' in html
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_multimarket_frontend_static.py`

- [ ] **Step 3: Implement versioned market context and hash routes**

Validate stored market against `/api/markets/capabilities`. On switch, cancel
stale requests, clear market-scoped view caches, update copy/currency, and load
only the active page. Use `history.pushState` with hash routes and restore source
state on `popstate`.

- [ ] **Step 4: Run GREEN and JavaScript syntax checks**

Run:

```bash
node --check web/static/js/market_context.js
node --check web/static/js/equity_router.js
node --check web/static/js/app.js
.venv/bin/pytest -q tests/test_multimarket_frontend_static.py tests/test_tushare_extension_frontend_static.py
```

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 5: Equity Workspace Visual Migration

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/static/css/style.css`
- Modify: `web/static/js/app.js`
- Test: `tests/test_multimarket_frontend_static.py`

**Interfaces:**
- Primary navigation: Equities, Futures, Macro, Industry, System.
- Equities secondary navigation: Overview, Heatmap, Instruments, Selection, Strategies, Watchlist, Wyckoff.

- [ ] **Step 1: Extend failing visual-token tests**

```python
def test_terminal_uses_approved_semantic_tokens():
    css = STYLE_CSS.read_text(encoding="utf-8").lower()
    assert "--bg-void: #000000" in css
    assert "--accent: #ff6900" in css
    assert "--price-up: #ff3131" in css
    assert "--price-down: #00ff41" in css
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_multimarket_frontend_static.py`

- [ ] **Step 3: Implement the approved industrial layout**

Keep continuous pure black, square corners, 1 px structural lines, compact grid,
vivid semantic red/green, and orange actions. Remove large blue-gray surfaces.
Provide skeleton, empty, warning, error, and syncing states for both markets.

- [ ] **Step 4: Run GREEN and browser smoke**

Run static tests and `node --check`, then start the local server. In a browser,
verify A-share/Hong Kong switching, industry-to-instrument deep link, browser
back restoration, keyboard focus, and no console errors.

- [ ] **Step 5: Plan-level verification**

Run:

```bash
.venv/bin/python -m py_compile market_data/equity_policy.py web_api/equities.py wyckoff_ai/data.py wyckoff_ai/pipeline.py utils/selection_worker.py
node --check web/static/js/market_context.js
node --check web/static/js/equity_router.js
node --check web/static/js/app.js
.venv/bin/pytest -q tests/test_equity_market_policy.py tests/test_equities_web.py tests/test_hong_kong_equity_flows.py tests/test_multimarket_frontend_static.py tests/test_selection_equivalence.py tests/test_wyckoff_data.py tests/test_wyckoff_pipeline.py
git diff --check
git status --short --branch
```

Expected: focused suite passes; existing A-share interaction remains usable.

