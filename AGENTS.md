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
  `8c18a0948cb5da6cb3caaf96ed9f49249c433f8c`
  (`Harden Tencent fetches against WAF 501 throttling`, 2026-06-11).
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
- Snapshot schema version 2 stores `previous_close` so daily price-limit counts
  can use exchange tick rounding instead of a percentage-only approximation.
- Daily limit counts use 10% for ordinary main-board stocks, 5% for main-board
  ST stocks, and 20% for ChiNext/STAR stocks. The first five stored trading
  days are excluded from limit counts.
- Web update progress around `82%` is the transition from target-stock sync to
  market-cache refresh. Inspect `_refresh_market_caches_for_job()` and the update
  status API before blaming the frontend.

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

The current uncommitted desktop, provider-network, and update-cancellation fixes passed:

```text
75 passed
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

Baseline commit: `8c18a09` on local `main`; `origin/main` is also `8c18a09`.

State at handoff:

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
- The current uncommitted network fix raises Tencent's default request spacing
  to 0.5 seconds. Explicit Tencent jobs still stop immediately on WAF, but an
  AkShare job now disables only its Tencent fallback for the rest of that run
  and continues without propagating the fallback error. AkShare also retries
  Eastmoney directly after a real `requests` network exception before using
  Tencent.
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
- Focused launcher/network/update-cancellation checks, environment validation,
  App launch, Computer Use EXIT verification, and the full suite pass:
  `75 passed`. After EXIT, the process and port 5080 remained stopped and no
  relaunch job appeared.

Always run `git status` again. This section is a handoff snapshot, not proof of
the current worktree state.

## 14. Decision Index By Commit

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
