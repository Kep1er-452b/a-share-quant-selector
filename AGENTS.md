# A-Share Quant Selector: Agent Context

This file is for coding agents working in this repository. It is not product
documentation. Read it before making changes, then verify every potentially
stale statement against the current code and Git history.

## 1. Source Of Truth And Versioning

- Git commits are the only project versions. Do not invent a second semantic
  version for this document.
- At the start of every task, run:

```bash
git status --short --branch
git log -5 --date=short --pretty=format:'%h %ad %s'
```

- This document was last reconciled against commit:
  `380a77f18e7b588f6cb5b245b614c18c83584da9`
  (`Harden review fixes for selection and Tushare sync`, 2026-07-02).
- If `HEAD` differs, trust the code and `git show`, then update the relevant
  parts of this document when the change affects architecture, invariants,
  workflows, or future handoff context.
- Do not duplicate the full commit history here. The decision index near the
  end only records changes that future agents need to understand.

## 2. Working Discipline

The user expects the following engineering discipline:

1. Do not guess interfaces; inspect code and primary documentation.
2. Do not execute vague intent blindly; confirm only genuine product ambiguity.
3. Do not invent business rules; use user confirmation or repository evidence.
4. Do not create unnecessary interfaces; reuse existing project surfaces.
5. Do not skip verification; run focused tests and broaden them with risk.
6. Do not damage architecture; follow local conventions and ownership boundaries.
7. Do not pretend to understand; state uncertainty and investigate it.
8. Do not modify blindly; refactor cautiously and keep scope controlled.

Also:

- Preserve user changes in a dirty worktree. Never revert unrelated work.
- Diagnose from concrete runtime state, logs, API responses, and exact errors.
- Prefer implementation plus verification unless the user asks only for a plan.
- Use `.venv/bin/python` and `.venv/bin/pytest`; the system Python may not have
  project dependencies.

## 3. Project Purpose

This is a local A-share data, selection, visualization, and analysis system.
Its major capabilities are:

- Maintain separate local stock-data warehouses for AkShare, Tushare, and Tencent.
- Run multiple technical selection strategies over local CSV data.
- Provide a Flask plus vanilla JavaScript desktop-oriented Web console.
- Render K-line charts, market heatmaps, stock details, and watchlists.
- Run custom safe formula strategies and Wyckoff AI analysis.
- Optionally notify through DingTalk.
- Use an optional native C acceleration layer while retaining Python fallback
  equivalence.

## 4. Technology Stack

- Python 3 with pandas, NumPy, Flask, PyYAML, requests, matplotlib, scipy.
- Data providers: AkShare, Tushare, Tencent.
- Frontend: server-rendered HTML plus one vanilla JavaScript application and CSS.
- Tests: pytest.
- Optional native acceleration: `csrc/quant_core.c`, built by
  `scripts/build_quant_core.py`.
- Desktop wrapper/build: pywebview, `launch_desktop_app.py`,
  `build_macos_app.py`.
- AI analysis: DeepSeek-compatible OpenAI client under `wyckoff_ai/`.

Dependency declarations live in `requirements.txt`. Local secrets belong in
environment variables or ignored local config files, never in committed docs.

## 5. Main Entry Points

- `main.py`: CLI and `QuantSystem` orchestration.
- `web_server.py`: Flask app, APIs, background jobs, provider activation,
  selection/update state, watchlist, export, and Wyckoff endpoints.
- `strategy/strategy_registry.py`: dynamic strategy discovery and registration.
- `utils/selection_worker.py`: shared batch execution path used by CLI and Web.
- `utils/data_provider.py`: provider abstraction, update planning, and progress.
- `utils/provider_router.py`: provider warehouse paths and active-provider state.
- `utils/csv_manager.py`: validated, locked, atomic CSV reads and writes.
- `utils/runtime_paths.py`: repository-external selection and Wyckoff output paths.
- `utils/technical.py`: Tongdaxin-style indicators and shared feature preparation.
- `utils/market_overview.py`: snapshots, heatmap data, cache health and rebuilds.
- `utils/tushare_ext_store.py`: SQLite repository for the Tushare extension
  warehouse under `data/providers/tushare/extended/`.
- `utils/tushare_ext_sync.py`: Tushare extension dataset sync stages for
  basics, prices, valuations, finance, trading, and index caches.
- `utils/tushare_ext_workflow.py`: shared CLI/Web orchestration for lightweight
  Tushare extension stages, full-backfill flags, trading-date fallback, and
  warning-style stage failures.
- `utils/tushare_ext_views.py`: read-model helpers for index K-lines,
  stock-side valuation/finance payloads, adjusted candles, and Market Pulse
  trading summaries.
- `web/templates/index.html`: application shell.
- `web/static/js/app.js`: frontend state and all page interactions.
- `web/static/css/style.css`: frontend styling.

CLI commands currently include:

```text
init, select, run, web, calendar, doctor, export
```

## 6. Data Architecture And Invariants

### Provider Warehouses

- Provider data lives under `data/providers/<provider>/`.
- The selected warehouse is recorded in `data/active_provider.json`.
- `utils/provider_router.py` is the routing authority.
- Production update, activation, and selection routing supports Tushare,
  AkShare, and Tencent through separate provider warehouses. Tushare remains
  the most thoroughly validated full-market path, while AkShare/Tencent require
  conservative concurrency and bounded smoke tests when network conditions
  change.
- Do not merge provider CSVs into one shared mutable warehouse.
- Provider switching must continue to reject empty warehouses and must not occur
  while update or selection jobs are running.

### CSV Contract

`CSVManager.REQUIRED_COLUMNS` is:

```text
date, open, high, low, close, volume, amount, turnover, market_cap
```

Critical rules:

- Rows are newest-first. Many Tongdaxin helpers depend on this.
- Writes are validated, deduplicated by date, sorted descending, locked, and
  atomic.
- `market_cap` is stored in yuan.
- The repository currently stores total market capitalization, not a separate
  circulating-market-cap field.
- Existing B1/B2 strategies therefore map Tongdaxin `CAPITAL`-derived market
  value conditions to `market_cap`. Keep this explicit when porting formulas.
- Volume units may differ by provider warehouse. Ratio-based formulas remain
  meaningful within one warehouse; do not compare raw volume across providers.
- Selection reads use `read_stock_for_analysis()`, which applies the local
  adjustment-gap repair view without silently rewriting source CSVs.

### Market Caches

- Snapshot and heatmap caches are derived data, not primary stock history.
- Snapshot schema version 3 stores `previous_close`, latest `amount`, and
  previous-row `amount`. Daily price-limit counts use `previous_close`, while
  Market Pulse uses the amount fields for latest-trading-day market turnover
  and previous-day deltas.
- Daily limit counts use 10% for ordinary main-board stocks, 5% for main-board
  ST stocks, and 20% for ChiNext/STAR stocks. The first five stored trading
  days are excluded from limit counts.
- Web update progress around `82%` is the transition from target-stock sync to
  market-cache refresh. Inspect `_refresh_market_caches_for_job()` and the update
  status API before blaming the frontend.

### Tushare Extension Warehouse

- Tushare now has a separate SQLite extension warehouse at
  `data/providers/tushare/extended/tushare_ext.sqlite`.
- The extension warehouse does not replace provider CSVs. CSVs remain the
  compatibility layer for selection, existing stock charts, and Wyckoff.
- Extension rows are stored by dataset and row key in `ext_dataset_rows`.
  Sync state and graceful permission warnings are stored in `ext_sync_state`.
- Permission/VIP failures in optional extension endpoints should become warning
  sync states and visible UI warnings, not hard crashes of the main price sync.
- Startup should only call the lightweight index cache path.
- Default Tushare CLI and Web update paths run the shared lightweight extension
  workflow only after the main provider CSV sync succeeds: basics, index cache,
  latest valuation, and recent trading snapshots. Full-market six-year price
  extension backfill and all-history financial backfill are skipped by default.
  Run them explicitly by setting `AQS_TUSHARE_EXTENSION_FULL_BACKFILL=1` or
  `TUSHARE_EXTENSION_FULL_BACKFILL=1`, or by setting
  `data_source.tushare.extension_full_backfill` / `tushare_extension.full_backfill`
  in local config.
- Price extension datasets include raw `daily`, `weekly`, `monthly`,
  `adj_factor`, and optional `daily_qfq` through Tushare `pro_bar` when the
  provider exposes it. Frontend qfq display can derive adjusted candles from
  raw daily plus `adj_factor` only when the derived candles cover every visible
  chart date, falling back to the current CSV `data` payload otherwise.
- Index detail weekly/monthly views are locally resampled from cached
  `index_daily` rows to save Tushare API quota. Do not make startup or detail
  views fetch `index_weekly` / `index_monthly` unless the user explicitly
  changes this policy.
- Market trading extension datasets currently include `top_list`, `top_inst`,
  `block_trade`, `moneyflow`, `margin`, `margin_detail`, `moneyflow_hsgt`, and
  historical/limited `hk_hold`.
- Market Pulse trading summaries may be cached in `ext_dataset_rows` under the
  `market_trading_summary` dataset. Cache validity depends on a signature of
  the source trading datasets and visible market-turnover inputs; keep this
  cache out of its own source signature.

## 7. Strategy Architecture

- Strategies inherit `strategy.base_strategy.BaseStrategy`.
- Files in `strategy/` are auto-discovered by `StrategyRegistry`.
- A normal strategy implements `calculate_indicators()` and `select_stocks()`.
- `FormulaStrategy` is `runtime_only` and receives formula parameters per job.
- Shared expensive indicators belong in
  `utils.technical.prepare_selection_features()` or
  `prepare_strategy_shared_features()` when multiple strategies reuse them.
- Strategy parameters live in `config/strategy_params.yaml`.
- Editable numeric ranges belong in `utils/config_schema.py`.
- User-facing category labels and Selection grouping metadata belong in
  `utils/strategy_labels.py`.
- Add focused formula-equivalence tests when porting Tongdaxin code. Window
  lengths, threshold operators, reversed-series semantics, and current-bar
  selection are common sources of subtle errors.

Current registered strategy families:

- B1: 242B, 242P, V2.42.61, Min J Simple, Min J Complex, Min J 61 Complex.
- B2: Beta.
- Bowl: Rebound.

`B1MinJSimpleStrategy` is an independent legacy condition based on the Zhixing
short trend, bull/bear line, and dynamic Min J. It is not derived from the
complex B1 formula variants. `B1MinJ61ComplexStrategy` is the full V2.42.61
formula with dynamic Min J replacing the fixed J threshold.

The Selection page receives family metadata from `/api/selection/options`.
Do not hardcode a second independent grouping table in the frontend.

## 8. Selection Execution

- CLI and Web should converge on `utils/selection_worker.py`.
- Each stock is read once, shared features are prepared once, then selected
  strategies run against that frame.
- Supported execution modes are process, thread, and sequential.
- Process/thread equivalence and optional C-core equivalence are tested.
- Results are grouped by strategy name and sorted by stock code.
- Invalid/ST/delisted stock filtering is centralized through
  `utils/strategy_labels.py`.
- Web selection is normally asynchronous:
  `POST /api/select/start`, then `GET /api/select/status/<job_id>`.

## 9. Web Architecture And Safety

- The frontend is a single-page vanilla JavaScript application.
- Pages include dashboard, heatmap, stocks, selection, strategies, watchlist,
  and Wyckoff analysis.
- Side-effect APIs must use appropriate HTTP methods and session-token checks.
- Validate payload shape, bounded string lengths, stock codes, job IDs, strategy
  names, and file paths at API boundaries.
- Keep long update/selection/Wyckoff work outside request handlers using the
  existing job state and polling patterns.
- In-memory Web job maps retain active jobs and the newest terminal jobs only.
  Do not remove this pruning without adding another bounded job-lifecycle
  mechanism; update-job pruning must also remove matching cancel events.
- After significant frontend changes, start the local Web app and verify the
  actual interaction in a browser, not only JavaScript syntax.

## 10. Configuration And Secrets

- Committed templates:
  `config/config.yaml.template`, `config/github.yaml.template`.
- Ignored local files:
  `config/config.yaml`, `config/config_local.yaml`, `config/github.yaml`.
- Prefer environment variables for tokens:
  `TUSHARE_TOKEN`, `DEEPSEEK_API_KEY`, and other provider credentials.
- Never place tokens, webhooks, private logs, or personal data in this file.
- Strategy parameters are intentionally committed in
  `config/strategy_params.yaml`.

## 11. Verification Commands

Minimum focused checks:

```bash
.venv/bin/python -m py_compile <changed-python-files>
node --check web/static/js/app.js
.venv/bin/python -m pytest -q <focused-tests>
git diff --check
```

Full regression suite:

```bash
.venv/bin/python -m pytest -q
```

At commit `b038324`, the full suite result was:

```text
54 passed
```

The latest comprehensive verification on branch
`codex/tushare-comprehensive-upgrade`, including uncommitted closure/job
lifecycle/extension-workflow/technical-performance fixes atop `380a77f`, passed:

```text
171 passed
```

Useful runtime checks:

```bash
.venv/bin/python main.py doctor
.venv/bin/python main.py web --host 127.0.0.1 --port 5080
```

Do not run a full-market update or send notifications merely to validate an
unrelated code change.

## 12. Generated And Runtime Files

Treat these as runtime artifacts unless the task explicitly concerns them:

- `data/`
- `logs/`
- caches, provider state, watchlists, exported CSVs, generated charts

Selection Markdown and Wyckoff analysis artifacts default to:

```text
~/DocumentsData/A股量化选股系统数据/选股结果
~/DocumentsData/A股量化选股系统数据/威科夫分析结果
```

`A_SHARE_QUANT_OUTPUT_ROOT` can override the common parent directory. These
outputs intentionally live outside the repository. Legacy `stock-selected/`,
`outputs/`, and `android-app/` paths remain ignored to prevent accidental
reintroduction. Provider CSV warehouses remain under the ignored `data/`
directory and must not be moved without an explicit data-migration task.

Do not delete or rewrite runtime artifacts casually. Do not commit newly
generated runtime artifacts unless the user explicitly wants them versioned.

## 13. Current Handoff

Baseline commit: `380a77f` on branch
`codex/tushare-comprehensive-upgrade`; `origin/main` remains `46c486d`.

State at handoff:

- Uncommitted fixes on top of `380a77f` cover the current small closure and
  cleanup batch: `utils/tushare_ext_workflow.py` now owns the shared CLI/Web
  Tushare extension stage sequence, skip policy, trading-date fallback, and
  warning-style failure handling; `main.py` calls that workflow after successful
  Tushare CLI provider syncs; Web update jobs wrap the same workflow for job
  logs/progress. In-memory Web job maps now prune old terminal jobs while
  preserving active jobs and update cancel events for retained jobs. K-line key
  candle date normalization is shared by standard and fast chart renderers.
  Technical helper loops for bars-last, backset, and variable-period REF are
  vectorized while preserving existing semantics. Verification passed:
  `py_compile` changed Python files, `node --check web/static/js/app.js`,
  focused related tests `62 passed`, technical/strategy equivalence tests
  `23 passed`, `git diff --check`, and full suite `171 passed`. These changes
  are intentionally not committed yet for user review.
- The Tushare comprehensive upgrade branch adds a SQLite extension
  warehouse, extension sync/read-model modules, Tushare-backed F1 index K-lines,
  `/api/index-detail/<symbol>`, stock extension payloads, Market Pulse trading
  summaries, qfq adjusted candles from `adj_factor`, configurable MA overlays,
  and a MACD panel. Focused extension/market tests currently pass:
  `39 passed`.
- The default update job now runs lightweight Tushare extension stages after
  main CSV sync and market-cache refresh: basics, index, latest valuation, and
  recent trading snapshots for current/previous comparisons. Heavy price and
  financial backfills are explicit opt-in via the full-backfill flags described
  above. Stage failures remain warning-style and preserve the main price-sync
  result.
- Market Pulse trading money metrics are normalized to 亿元. Missing extension
  rows return empty UI values instead of misleading zeroes. The local market
  turnover metric comes from latest-trading-day CSV amounts and ignores stale
  suspended-stock latest rows.
- Stock detail market-value fields from Tushare `daily_basic.total_mv/circ_mv`
  are frontend-formatted from 万元 into 亿/万亿. K-line tooltips include daily
  change percentage with red-up/green-down styling.
- Stock detail adjusted candles use locally derived qfq from Tushare raw
  `daily` plus `adj_factor` only when coverage matches the visible CSV dates;
  partial extension price syncs no longer mix incomplete adjusted candles into
  the chart. A 000001 spot check showed 2026-04-24 and 2026-04-27 have the same
  `adj_factor`, so the visible gap there is not an adjustment-factor jump.
- Stock and index detail charts support an adjustable visible range stored in
  `localStorage` under `quantStockChartLimit`. UI options are 260, 520, 1000,
  and all available rows up to the server cap. The stock detail API and
  `/api/index-detail/<symbol>` accept `limit`, return `limit`, `total_bars`,
  and `max_limit`, and clamp requests to avoid rendering runaway chart payloads.
- Stock detail charts have a compact `13` toggle stored in `localStorage` under
  `quantShowSequenceMarkers`. It hides only the Tongdaxin-style thirteen-turn
  sequence number scatter labels (`UP_SEQ`/`DOWN_SEQ`) and keeps violent-K
  stars, moving averages, KDJ, and MACD visible. The toggle re-renders from the
  in-memory chart payload instead of refetching long chart ranges.
- Tushare industry-cache rebuilds now merge local `tushare_stock_map.json`
  industry values before taking the high-coverage previous-cache fast path, so
  newly listed stocks with local metadata do not remain in the `未分类` bucket.
  The local active Tushare cache was refreshed after this change: 5,206 mapped,
  0 unmapped, and 16 heatmap payloads regenerated.
- The F1 compact index chart reserves a wider right grid margin so right-side
  y-axis labels are not clipped by the chart border or adjacent layout.
- F1 index cache warm-up runs at Web startup and should remain index-only.
  Do not add full-market stock/finance sync to the startup path.
- The stock detail modal now uses a chart-left/info-right layout. It removes
  the old bottom KV strip and uses right-side panels for crosshair snapshot,
  valuation, financial, and company information.
- B1 V2.42.61 is implemented as `B1V24261Strategy`.
- `B1MinJSimpleStrategy` retains its independent legacy Zhixing conditions.
- `B1MinJ61ComplexStrategy` is added as a separate full V2.42.61 + dynamic
  Min J strategy; the historical Min J Complex strategy remains unchanged.
- Selection strategies are grouped under collapsible B1, B2, and Bowl families.
- Child strategies remain individually selectable and retain parameter editing.
- F1 Market Pulse now includes price-limit counts, a Tongdaxin-style breadth
  distribution, and a sortable all-industry ranking modal.
- F2 uses its local ticker for the top ten and bottom ten industry returns,
  rendered red-up and green-down, while the global ticker keeps market breadth.
- The F1 breadth columns are centered in a compact 650px group rather than
  stretched across the full panel.
- Selection and Wyckoff outputs now live under
  `~/DocumentsData/A股量化选股系统数据`; existing local history was migrated
  there. Provider CSV data remains in the ignored repository-local `data/`.
- The repository Android subtree and Android GitHub Actions workflow are
  removed. The independent project remains at
  `~/Downloads/android-app`, and `android-app/` is ignored here.
- Focused strategy/cache/web/runtime-path tests and the full suite passed:
  `63 passed`.
- Browser verification covered the compact F1 chart, industry sort modal, F2
  ticker, and serving a historical Wyckoff chart from the external directory.
- On 2026-06-11, repeated Tencent updates began receiving Tencent WAF HTTP 501
  HTML instead of market-data JSON after roughly 1,350 incremental requests.
  With the previous 24-worker default and route retry loop, each failure became
  a full refresh retry and the planned total expanded by thousands.
- The committed Tencent hardening limits Tencent to four workers, spaces
  Tencent requests by at least 0.2 seconds, detects the WAF 501 page, and
  propagates it as a batch-fatal `DataProviderError` instead of retrying every
  stock.
- Three captured failures on 2026-06-12 show Tencent WAF 501 both without VPN
  and through an AkShare job. The AkShare traceback proves its primary request
  failed first, then its optional Tencent fallback raised WAF and incorrectly
  aborted the entire AkShare batch. The failed jobs had already written about
  662, 592, and 6 stock CSVs, while provider state remained stale because the
  fatal exception bypassed final state persistence.
- The committed network fix raised Tencent's default request spacing to 0.5
  seconds. Explicit Tencent jobs still stop immediately on WAF, but an
  AkShare job now disables only its Tencent fallback for the rest of that run
  and continues without propagating the fallback error. AkShare also retries
  Eastmoney directly after a real `requests` network exception before using
  Tencent.
- The committed throughput adjustment starts Tencent at 0.35 seconds
  plus up to 0.05 seconds of jitter, pauses 8 seconds after each 400 requests,
  backs off toward 1.2 seconds on 403/429/5xx responses, and cautiously recovers
  after sustained success. WAF 501 remains batch-fatal.
- AkShare now treats primary and direct connectivity as run-scoped route
  states. A confirmed primary network failure avoids repeating that failed
  route for every stock; the direct route uses a four-second probe and is
  reused on success or disabled for the rest of the run on failure.
- A 2026-06-14 VPN-connected probe fetched 20 Tencent stocks successfully in
  7.21 seconds (2.77 requests/second) with no throttling response. Two AkShare
  updates completed through the primary route in 0.37 and 0.35 seconds. This is
  a bounded probe, not proof that the user's no-VPN route will behave exactly
  the same during a full-market update.
- Provider state and update error reports now retain bounded primary/fallback
  exception samples, request policy, assessment counts, and interrupted
  progress. Low-coverage failures also create a report under `logs/errors/`.
  This is the primary evidence path when the user's no-VPN environment cannot
  be reproduced during a Codex session.
- The Web update modal previously handled `error` but not the low-coverage
  terminal status `failed`, so polling continued forever and kept reopening the
  modal. The frontend now treats `failed`, `error`, and `cancelled` as terminal
  failure states and exposes `停止此次更新并保留日志` for both active and
  failed tasks.
- `POST /api/update/cancel/<job_id>` sets a per-update cancellation event
  instead of the global HALT event. Active cancellation preserves already
  written CSVs, job feed entries, and a persisted error-report snapshot; a
  task that already failed keeps its original report. Browser verification used
  an isolated simulated `failed` job and confirmed the button releases the
  modal, leaves UPDATE enabled, and produces no browser console errors.
- On 2026-06-13 with VPN connected, a one-stock AkShare probe saw the system
  proxy close the Eastmoney request and the direct retry also disconnect;
  Tencent fallback succeeded. This confirms the failure is route/provider
  dependent, not simply "VPN on versus off." Do not run a full-market provider
  update merely to validate this fix.
- The desktop App restart-after-EXIT incident was caused by an external
  `launchctl submit` job named
  `com.openai.codex.a-share-quant-selector`, not by the shutdown endpoint. The
  job was removed and must not be recreated for ordinary App restarts.
- The source `assets/app_icon.icns` has opaque black corners. The App bundle
  masks them normally, but passing that file directly to `webview.start()`
  exposes a black square in the Dock. `build_macos_app.py` now signs the bundle,
  exports macOS's masked icon as transparent `runtime_icon.png`, then signs the
  finished App again. The launcher only passes that generated PNG to pywebview.
- Focused launcher/network/update-cancellation/throttling checks, environment
  validation, App launch, Computer Use EXIT verification, and the full suite
  pass: `78 passed`. After EXIT, the process and port 5080 remained stopped and
  no relaunch job appeared.
- The committed data-integrity hardening makes update and selection starts
  mutually exclusive under one admission lock, and selection jobs now persist
  bounded strategy error counts/details with a `completed_with_warnings`
  terminal state instead of presenting calculation failures as clean zero-hit
  runs.
- Tushare refreshes `stock_basic` at the start of each update with cached
  fallback, atomically persists stock names/metadata plus a refresh timestamp,
  and exposes `list_date` to the sync pipeline.
- Tushare qfq responses retain optional `adj_factor`. For legacy CSVs without
  the factor, the migration logic compares overlapping OHLC rows: a
  matching overlap migrates incrementally, while a mismatch or missing overlap
  still forces a full refresh. Files whose stored latest factor anchor changed
  also continue to require a full refresh.
- Tushare `daily_basic` trade-date cache misses are serialized so concurrent
  stock workers cannot duplicate the same API request. Incremental-to-full
  fallbacks are counted as retries rather than live failures in update progress.
- Adjustment-gap validation exempts actual stored trading rows in the listing
  no-limit window: five rows for ChiNext/STAR and the listing row for main
  board stocks. It does not infer listing age from the first row of a truncated
  non-Tushare history.
- Timed-out sync batches set a write guard checked inside the CSV file lock and
  immediately before atomic replace. Incremental timeouts are explicitly
  queued for full refresh; full-refresh timeouts are persisted as failures, and
  stale statuses can no longer produce a completed provider state.
- On 2026-06-18, a real full-market Tushare update completed in 34m53s:
  4,816 incremental updates, 379 full refreshes, 12 adjustment warnings, and
  zero failures. Coverage reached 5,204/5,207 (99.7695%), market caches were
  rebuilt, and active provider switched to Tushare. An immediate second full
  update handled only 54 work items, finished data sync in six seconds, and
  completed with zero failures, confirming the migration is one-time.
- Focused provider/Web tests pass: `26 passed`. Python syntax checks,
  `git diff --check`, and the full suite pass: `90 passed`.
- The committed provider-availability repair restores AkShare and
  Tencent as production update, activation, and selection providers instead of
  treating them as read-only archives. The provider router again lists all three
  providers as updateable/activatable, Web update options expose all three, and
  CLI `--provider akshare/tencent` no longer exits with `PROVIDER_DEPRECATED`.
- Tencent limited updates now avoid the slow full-market code-space scan when
  `max_stocks` is provided, using local/shared/default stock-name bootstrap
  before falling back to full discovery. AkShare small-batch market-cap lookup
  tries Tencent quote batches before full-market AkShare spot data, so one-stock
  update probes no longer spend about a minute on market-cap pagination.
- Tushare update now performs a reusable preflight before provider CSV writes:
  runtime/token source, `stock_basic`, `trade_cal`, the latest non-empty
  `daily_basic` within three open days, and qfq `pro_bar` with `adj_factor`.
  Authentication/permission/schema failures abort immediately. Network failures
  open a shared run circuit after eight consecutive failures or an 80% failure
  rate in twenty outcomes; legal empty stock responses do not count as network
  failures.
- Failed updates receive a zero-network automatic snapshot in the same atomic
  error JSON. Standard and extended diagnostics append reproducible runs under
  `diagnostics.runs` through Web APIs or `main.py doctor --update-report ...`.
  Diagnostic tasks are mutually exclusive with updates and selections, and a
  temporary Token remains in task memory because `tushare.set_token()` is no
  longer called.
- A real bounded standard diagnostic passed in 4.58 seconds with 7 logical API
  calls: all four core interfaces passed, three board samples overlapped local
  dates, and 200 deterministic CSV samples passed. A one-stock incremental
  fetch/write smoke test used `/tmp`, returned 24 rows through 2026-06-18, and
  did not modify the formal provider warehouse. No full-market update ran.
- Focused provider/Web/diagnostic tests, Python/JavaScript syntax checks, and the
  full suite pass: `111 passed`. The local Web server starts successfully.
- On 2026-06-29, AkShare and Tencent bounded one-stock update probes wrote CSVs
  successfully in temporary data directories: AkShare completed in 7.18s via
  Tencent history fallback after the local Eastmoney proxy route failed, and
  Tencent completed in 0.45s through its native path. Browser-plugin and
  Computer Use validation against a temporary same-origin smoke page confirmed
  Web `/api/update/start` with `max_stocks=1` completed for both providers,
  with `current_step="更新完成"` and `error=null`, without modifying the formal
  repository `data/` warehouse.
- The committed Wyckoff upgrade aligns the Web/DeepSeek prompt and
  output contract with the newer "威科夫二世" skill: prompt reading order is
  background-first, `book_judgment` is validated and backfilled, formatted
  analysis text now surfaces current bias, next scenarios, invalidation, and
  limitations, the bundled chart script adds `book_judgment` plus denser
  Phase-label collision avoidance, and the repository-local `wyckoff-second/`
  skill docs were synchronized with the current built-in skill. Focused
  Wyckoff/runtime tests pass
  (`13 passed`), Python syntax checks pass, the provided
  `/Users/chenxingyu/Downloads/600150_中国船舶.csv` generates local script output
  under `/tmp/wyckoff-600150-local.*`, and a real DeepSeek run on the same CSV
  validated JSON plus rendered `/tmp/wyckoff-600150-deepseek-rerender2.png`.
  No known blocker remains; the full suite was not rerun because the change is
  scoped to the Wyckoff module and its runtime-path integration tests.

Always run `git status` again. This section is a handoff snapshot, not proof of
the current worktree state.

## 14. Decision Index By Commit

- `7867558` (2026-07-01): added a stock-detail thirteen-turn marker visibility
  toggle and fixed Tushare industry-cache reuse so recent stock metadata can
  fill `未分类` gaps before rebuilding heatmap payloads.
- `a4a4b70` (2026-07-01): added adjustable stock/index detail chart ranges
  with `limit=260|520|1000|all`, persisted the range in `localStorage`, raised
  the default daily preview to 260 bars, and widened the F1 index chart's right
  grid margin to keep y-axis labels visible.
- `72cb7c4` (2026-07-01): made Tushare extension price/financial backfills
  explicit opt-in, normalized Market Pulse money metrics to 亿元 with missing
  data shown empty, guarded adjusted-candle overlays against partial coverage,
  and added tooltip涨跌幅 plus market-value 亿/万亿 formatting.
- `3b3ac3b` (2026-07-01): upgraded F1 index controls and stock detail UI with
  Tushare MA50/MA200 index overlays, MA/MACD chart controls, market trading
  cards, and a chart-left/info-right detail modal.
- `18587ef` (2026-07-01): wired the Tushare extension warehouse into Web APIs,
  startup index cache warm-up, stock detail extension payloads, Market Pulse
  trading summaries, and post-price-sync update stages.
- `39b1f07` (2026-07-01): added the Tushare SQLite extension warehouse,
  resumable sync-state storage, optional permission warnings, price/finance/
  trading/index sync services, adjusted-candle derivation, and read models.
- `08fa2a1` (2026-06-18): serialized Tushare `daily_basic` cache misses and
  reconciled the post-migration update handoff.
- `8875a11` (2026-06-14): made update/selection admission mutually exclusive
  and hardened Tushare metadata, adjustment, timeout, and state integrity.
- `1b350aa` (2026-06-14): made AkShare route selection run-scoped and added
  adaptive Tencent throttling/diagnostics.
- `8c18a09` (2026-06-11): throttled Tencent requests and converted WAF HTTP 501
  responses into batch-fatal update errors instead of per-stock retry storms.
- `26bfddf` (2026-06-10): moved selection/Wyckoff runtime outputs outside the
  repository and removed the legacy Android subtree after preserving its
  independent checkout.
- `1bd8a68` (2026-06-09): added the Android app subtree and GitHub Actions
  workflow.
- `b038324` (2026-06-07): added B1 V2.42.61, strategy-family metadata, and
  collapsible Selection family UI.
- `72685d5` (2026-06-02): added manual provider switching through the existing
  Data Watch/provider-router surfaces.
- `f8c728d` (2026-06-01): made selection result strategy panels collapsible.
- `4240e49` (2026-06-01): added safe custom formula evaluation and runtime
  formula strategy execution.
- `dd0c728` (2026-05-31): added optional C quant core and shared feature caching;
  Python fallback equivalence remains mandatory.
- `479a5f2` (2026-05-30): hardened selection APIs and data handling.

Use `git show <commit>` for details rather than expanding this list into a
parallel changelog.

## 15. Handoff Update Protocol

After a meaningful task:

1. Verify the change with focused tests and the full suite when risk warrants it.
2. If work remains uncommitted, update `Current Handoff` with:
   task intent, changed surfaces, tests run, and the exact remaining blocker.
3. After a commit, replace the baseline SHA and add one concise Decision Index
   entry only if future agents need the architectural or behavioral decision.
4. Remove resolved temporary handoff notes. Git remains the detailed history.
5. Keep this file concise enough to read at the beginning of every new session.
