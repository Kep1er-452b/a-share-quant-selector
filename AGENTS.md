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

- This document was created against commit:
  `b03832462a0c4ec778e6c54910e21187686b1eb3`
  (`Add B1 V2.42.61 strategy and grouped selection UI`, 2026-06-07).
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

- B1: 242B, 242P, V2.42.61, Min J Simple, Min J Complex.
- B2: Beta.
- Bowl: Rebound.

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
- `outputs/`
- `logs/`
- `stock-selected/`
- caches, provider state, watchlists, exported CSVs, generated charts

Do not delete or rewrite them casually. Do not commit newly generated runtime
artifacts unless the user explicitly wants them versioned.

## 13. Current Handoff

Baseline commit: `b038324` on `main`, matching `origin/main` when this document
was created.

State at handoff:

- B1 V2.42.61 is implemented as `B1V24261Strategy`.
- Selection strategies are grouped under collapsible B1, B2, and Bowl families.
- Child strategies remain individually selectable and retain parameter editing.
- The B1 V2.42.61 formula and Selection API grouping have focused tests.
- Full test suite passed: `54 passed`.
- No known implementation work was pending before adding this document.

Always run `git status` again. This section is a handoff snapshot, not proof of
the current worktree state.

## 14. Decision Index By Commit

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

