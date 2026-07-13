# Multi-Market Domain Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement bounded local synchronization and read APIs for Hong Kong equities, futures, macro series, and industry data.

**Architecture:** Each domain owns a catalog, normalization adapter, service, and SQLite store. All domains share the foundation plan's sync engine and event contract. API blueprints expose bounded read models and explicit sync state.

**Tech Stack:** Python 3, pandas, Tushare, SQLite, Flask, pytest.

## Global Constraints

- Complete and verify plan 01 first.
- Do not commit or stage changes.
- Required provider interfaces must be probed in bounded temporary stores.
- Permission failures for optional datasets remain warnings.
- Do not label Hong Kong `market` values as industry classifications.
- Do not run minute, tick, Level 2, or real-time syncs.

---

## File Structure

- Create `market_data/tushare_client.py`: in-memory token client and classified errors.
- Create `market_data/hong_kong.py`, `futures.py`, `economy.py`, `industry.py`: domain catalogs and normalization.
- Create `market_data/services.py`: bounded domain query services.
- Create `web_api/domain_data.py`: new read and sync routes.
- Modify `web_server.py`: inject stores, client factory, sync engine, and blueprint.
- Create one focused test file per domain plus API integration tests.

### Task 1: Tushare Client Factory And Error Classification

**Files:**
- Create: `market_data/tushare_client.py`
- Test: `tests/test_market_data_tushare_client.py`

**Interfaces:**
- Produces: `TushareClientFactory.from_config(config, environ) -> TushareClientFactory`
- Produces: `client() -> object`
- Produces: `classify_provider_error(exc) -> ProviderIssue`

- [ ] **Step 1: Write failing token-source and redaction tests**

```python
def test_client_factory_prefers_environment_without_persisting_token(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "env-token")
    factory = TushareClientFactory.from_config({}, os.environ)
    assert factory.token_source == "environment"
    assert "env-token" not in repr(factory)


def test_permission_error_is_non_retryable():
    issue = classify_provider_error(RuntimeError("没有访问该接口的权限"))
    assert issue.code == "PERMISSION_DENIED"
    assert issue.retryable is False
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_market_data_tushare_client.py`

Expected: FAIL because the factory is missing.

- [ ] **Step 3: Implement in-memory token handling**

Reuse the repository's current config precedence and never call
`tushare.set_token()`. The factory creates `ts.pro_api(token)` and exposes only
`token_present` and `token_source` to diagnostics.

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/pytest -q tests/test_market_data_tushare_client.py tests/test_update_diagnostics.py`

Expected: PASS.

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 2: Hong Kong Catalog, Sync, And Read Service

**Files:**
- Create: `market_data/hong_kong.py`
- Modify: `market_data/services.py`
- Test: `tests/test_hong_kong_market.py`

**Interfaces:**
- Produces: `hong_kong_catalog() -> DatasetCatalog`
- Produces: `HongKongService.search(query, limit, offset)`, `kline(symbol, limit, adjustment)`, `finance(symbol)`, `overview()`

- [ ] **Step 1: Write failing normalization and coverage tests**

```python
def test_hong_kong_daily_normalizes_symbol_date_and_currency(tmp_path):
    rows = normalize_hk_daily(pd.DataFrame([{"ts_code": "00700.HK", "trade_date": "20260710", "close": 420.0}]))
    assert rows[0]["symbol"] == "00700.HK"
    assert rows[0]["currency"] == "HKD"


def test_hong_kong_overview_does_not_claim_industry_without_mapping(service):
    payload = service.overview()
    assert payload["grouping_mode"] in {"industry", "market_segment", "performance_distribution"}
    if payload["grouping_mode"] != "industry":
        assert payload["industry_coverage"] == 0
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_hong_kong_market.py`

Expected: FAIL because the domain module is missing.

- [ ] **Step 3: Implement catalog and service**

Register `hk_basic`, `hk_tradecal`, `hk_daily`, adjustment factors when
available, and Hong Kong finance datasets. Store all symbols in canonical
Tushare format. Derive K-line change percentage from previous close. Use raw
daily data when adjusted coverage does not cover every requested chart date.

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/pytest -q tests/test_hong_kong_market.py`

Expected: PASS for search, pagination, raw/adjusted fallback, finance, and
industry-coverage behavior.

- [ ] **Step 5: Run a bounded temporary-store provider probe**

Run a script that fetches `hk_basic` and no more than one symbol's recent
`hk_daily` rows into a temporary directory. Expected: success or a classified
permission/network result; the repository `data/` tree remains unchanged.

### Task 3: Futures Catalog, Continuous Mapping, And K-Line

**Files:**
- Create: `market_data/futures.py`
- Modify: `market_data/services.py`
- Test: `tests/test_futures_market.py`

**Interfaces:**
- Produces: `futures_catalog() -> DatasetCatalog`
- Produces: `FuturesService.contracts(...)`, `continuous_symbols(...)`, `kline(symbol, limit)`

- [ ] **Step 1: Write failing contract and series tests**

```python
def test_active_contracts_exclude_expired_contracts(service):
    contracts = service.contracts(active_on="20260713", exchange="CFFEX", limit=100)
    assert all(row["delist_date"] >= "20260713" for row in contracts["items"])


def test_futures_kline_includes_settlement_fields(service):
    payload = service.kline("IF2607.CFX", limit=260)
    assert {"pre_settle", "settle", "oi"}.issubset(payload["candles"][0])
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_futures_market.py`

- [ ] **Step 3: Implement futures catalog and services**

Register `fut_basic`, `fut_daily`, and verified continuous mapping. Normalize
exchange, product, contract month, active range, settlement, volume, amount, and
open interest. Keep futures and equity K-line schemas separate at storage level;
adapt them only in the chart read model.

- [ ] **Step 4: Run GREEN and bounded provider probe**

Run: `.venv/bin/pytest -q tests/test_futures_market.py`

Then fetch CFFEX metadata and one active contract's recent daily rows into a
temporary store. Expected: repository data unchanged.

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 4: Macro Series Registry And Comparison Read Model

**Files:**
- Create: `market_data/economy.py`
- Modify: `market_data/services.py`
- Test: `tests/test_economy_market.py`

**Interfaces:**
- Produces: `economy_catalog() -> DatasetCatalog`
- Produces: `EconomyService.series_catalog(family)`, `series(series_ids, start, end, max_points)`

- [ ] **Step 1: Write failing unit/frequency and downsampling tests**

```python
def test_macro_series_always_declares_unit_and_frequency(service):
    items = service.series_catalog("inflation")
    assert all(item["unit"] and item["frequency"] for item in items)


def test_macro_overview_downsamples_but_detail_preserves_rows(service):
    overview = service.series(["cn_cpi.nt_yoy"], max_points=120)
    detail = service.series(["cn_cpi.nt_yoy"], max_points=2000)
    assert len(overview["series"][0]["points"]) <= 120
    assert len(detail["series"][0]["points"]) >= len(overview["series"][0]["points"])
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_economy_market.py`

- [ ] **Step 3: Implement registered macro series**

Register GDP, CPI, PPI, money supply, PMI, and Shibor series with exact field,
unit, frequency, precision, and family metadata. Use release-aware cursors and
stable largest-triangle or bucket sampling for chart overviews; table/detail
queries return exact stored rows.

- [ ] **Step 4: Run GREEN and bounded provider probe**

Run: `.venv/bin/pytest -q tests/test_economy_market.py`

Probe each initial endpoint over a short date range into a temporary store.

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 5: Market Industry And Industrial Cycle Services

**Files:**
- Create: `market_data/industry.py`
- Modify: `market_data/services.py`
- Test: `tests/test_industry_market.py`

**Interfaces:**
- Produces: `industry_catalog() -> DatasetCatalog`
- Produces: `IndustryService.classifications(level)`, `detail(industry_id)`, `cycle_series(...)`

- [ ] **Step 1: Write failing hierarchy and instrument-link tests**

```python
def test_industry_detail_returns_canonical_instrument_links(service):
    payload = service.detail("801880.SI")
    assert payload["members"]
    assert payload["members"][0]["route"].startswith("#/equities/a-share/instrument/")


def test_industry_cycle_does_not_mix_units_without_normalization(service):
    payload = service.cycle_series(["cn_pmi.headline", "cn_ppi.ppi_yoy"])
    assert payload["axis_mode"] in {"separate", "normalized"}
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_industry_market.py`

- [ ] **Step 3: Implement classification, members, index data, and cycle views**

Register `index_classify`, `index_member_all`, verified SW daily index data, PMI
components, and PPI components. Calculate mapping coverage and retain an
unclassified bucket. Treat optional flow/valuation datasets as warning-capable
catalog entries only after permission and units are verified.

- [ ] **Step 4: Run GREEN and bounded provider probe**

Run: `.venv/bin/pytest -q tests/test_industry_market.py`

Probe SW2021 L1 classification and one industry's current members in a temporary
store.

- [ ] **Step 5: Uncommitted checkpoint**

Run: `git diff --check && git status --short`

### Task 6: Domain Data APIs And Shared Sync Entry

**Files:**
- Create: `web_api/domain_data.py`
- Modify: `web_server.py`
- Test: `tests/test_domain_data_web.py`
- Test: `tests/test_web_security.py`

**Interfaces:**
- Produces read routes specified in the approved design.
- Produces: `POST /api/sync/start`
- Produces: `POST /api/sync/cancel/<job_id>`

- [ ] **Step 1: Write failing pagination, validation, and CSRF tests**

```python
def test_domain_list_rejects_unbounded_limit(client):
    response = client.get("/api/futures/contracts?limit=999999")
    assert response.status_code == 400


def test_sync_start_requires_session_token(client):
    response = client.post("/api/sync/start", json={"domain": "macro"})
    assert response.status_code == 403
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest -q tests/test_domain_data_web.py tests/test_web_security.py`

- [ ] **Step 3: Implement blueprints and service injection**

Validate domain, dataset, symbol, date, limit, offset, sort, and job IDs. Reuse
the current job admission/cancellation conventions. Return missing data as empty
values plus state, never invented zeroes.

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/pytest -q tests/test_domain_data_web.py tests/test_web_security.py tests/test_tushare_extension_web.py`

- [ ] **Step 5: Plan-level verification**

Run:

```bash
.venv/bin/python -m py_compile market_data/tushare_client.py market_data/hong_kong.py market_data/futures.py market_data/economy.py market_data/industry.py market_data/services.py web_api/domain_data.py
.venv/bin/pytest -q tests/test_market_data_tushare_client.py tests/test_hong_kong_market.py tests/test_futures_market.py tests/test_economy_market.py tests/test_industry_market.py tests/test_domain_data_web.py tests/test_web_security.py
git diff --check
git status --short --branch
```

Expected: all focused tests pass and no formal repository warehouse was changed by provider probes.

