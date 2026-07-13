# Multi-Market Industrial Terminal Design

**Date:** 2026-07-13

**Status:** Approved in interactive design review, pending written review

**Repository baseline:** `a45a43f`

## 1. Objective

Extend the current A-share desktop quant system into a multi-domain local market
terminal while preserving the existing A-share warehouse and workflows.

The application will provide five top-level workspaces:

1. Equities
2. Futures
3. Macro
4. Industry
5. System

The Equities workspace has one persistent market context switch: A-share or Hong
Kong. Dashboard, heatmap, instruments, selection, strategies, watchlist, and
Wyckoff features follow the selected market. A-share rules remain exclusive to
A-share data. Hong Kong behavior follows Hong Kong market conventions.

Futures, Macro, Industry, and System remain independent pages. They must not be
compressed into a single catch-all data page.

## 2. Confirmed Product Decisions

- Use a global A-share/Hong Kong switch inside the Equities workspace.
- Switching the equity market switches every equity feature and visible data
  source together.
- Keep futures, macro, industry, and system monitoring as separate top-level
  pages.
- Include both stock-market industry analysis and industrial-cycle data.
- Allow industry components, financial highlights, search results, and log
  references to open the corresponding instrument K-line.
- Restore industry filters, sort order, and scroll position when returning from
  an instrument detail.
- Reuse only market-neutral technical calculations across A-share and Hong Kong
  workflows. Market policy, filters, parameters, and explanations remain
  market-specific.
- Upgrade logs into a real-time task center, searchable event history, health
  diagnostics, and sanitized diagnostic export.
- Prepare the runtime for future macOS and Windows packaging.
- Packaged builds must not contain existing market data, generated caches,
  logs, selection outputs, Wyckoff outputs, Tushare tokens, or DeepSeek tokens.
- Packaged builds create empty repository paths. A new machine performs a fresh
  full synchronization after the user provides credentials.
- Do not move or rewrite the current source checkout's A-share warehouse as part
  of this feature.
- Do not commit implementation automatically.

## 3. Scope

### 3.1 Included

- Market-aware Equities workspace.
- Hong Kong local data warehouse, synchronization, search, K-line, selection,
  watchlist, heatmap-compatible market view, finance panels, and Wyckoff entry.
- Futures local data warehouse, contract search, active/continuous contract
  views, daily K-line, sync state, and warnings.
- Macro local data warehouse, categorized series browser, trend charts, release
  metadata, sync state, and warnings.
- Industry local data warehouse with two modes:
  - Market Industry: classifications, members, index K-lines, ranking, valuation,
    capital-flow data when available, and instrument deep links.
  - Industrial Cycle: PMI, PPI components, and other supported cycle series.
- Unified sync protocol for new domains.
- Structured operations event store and system diagnostics.
- Cross-platform runtime-path and resource-path abstraction.
- Targeted front-end redesign based on the existing terminal identity.
- Focused performance hardening required by the expanded data surface.

### 3.2 Excluded

- Shipping a Windows installer in this change.
- Shipping a fully standalone macOS installer in this change.
- Copying existing data into a package.
- Persisting real API credentials in tracked files or package resources.
- Treating A-share policy rules as Hong Kong rules.
- Replacing the current A-share CSV compatibility warehouse.
- Intraday, tick, Level 2, or real-time streaming features without separately
  verified permission and product approval.
- Trading execution or brokerage integration.

## 4. Visual Direction

### 4.1 Design Read

This is a dense professional market terminal for a single operator. The visual
language is Bloomberg-terminal-inspired and industrial, not a marketing page,
consumer dashboard, or glassmorphism interface.

Design dials:

- `DESIGN_VARIANCE: 3`
- `MOTION_INTENSITY: 2`
- `VISUAL_DENSITY: 9`

### 4.2 Visual Tokens

- Primary canvas: `#000000`
- Navigation layer: `#080808`
- Raised utility layer, used sparingly: `#101010`
- Structural line: `#303030`
- Primary text: `#F2F2F2`
- Secondary text: `#A0A0A0`
- Muted text: `#707070`
- Action and focus orange: `#FF6900`
- Vivid up/risk red: `#FF3131`
- Vivid down/healthy green: `#00FF41`
- Warning amber: `#FFC44D`

Red and green are reserved for price direction, risk, health, and terminal
states with real semantic meaning. They must not become general decoration.

### 4.3 Layout Rules

- Maintain a continuous pure-black canvas. Do not cover the application in
  blue-gray cards.
- Use 1 px dividers, alignment, type size, and local brightness to communicate
  hierarchy.
- Keep square corners throughout the terminal.
- Use compact spacing and fixed columns where stable comparison is important.
- Use monospaced numeric rendering. Font fallback order must support both
  platforms: IBM Plex Mono when present, SF Mono on macOS, Consolas on Windows,
  then Courier New.
- Preserve visible keyboard focus and WCAG-readable text contrast.
- Use motion only for task feedback and visible state transitions.
- Respect `prefers-reduced-motion`.
- Collapse complex desktop grids into a single readable column below the mobile
  breakpoint. The desktop application remains the primary target.

## 5. Information Architecture

### 5.1 Top-Level Navigation

The primary navigation becomes:

```text
F1 EQUITIES | F2 FUTURES | F3 MACRO | F4 INDUSTRY | F5 SYSTEM
```

The existing command bar, update access, exit, and emergency halt remain global
application controls.

### 5.2 Equities Workspace

The equities header contains the persistent market switch:

```text
A-SHARE | HONG KONG
```

The secondary navigation remains function-oriented:

```text
Overview | Heatmap | Instruments | Selection | Strategies | Watchlist | Wyckoff
```

The selected equity market is stored as versioned preference
`quantEquityMarketContext`. The value is validated against the server capability
response before use.

### 5.3 Dedicated Workspaces

- Futures: Contracts, Continuous, Products, K-line, Sync Status.
- Macro: Growth, Inflation, Money, Rates, Releases.
- Industry: Market Industry, Industrial Cycle.
- System: Tasks, Events, Health, Performance, Configuration.

## 6. Market Context And Capability Model

Create a server-owned market capability registry. The front end must not infer
support from market names.

Each market capability record includes:

- Market identifier.
- Display name and currency.
- Trading calendar source.
- Instrument-code validator.
- Available pages.
- Available strategy families.
- Available finance and valuation fields.
- Available heatmap grouping modes.
- Supported adjustment modes.
- Market-specific policy hooks.
- Warehouse health and last-sync summary.

The browser requests capabilities at startup and renders only supported
functions. Unsupported functions show a factual explanation rather than a
non-functional control.

### 6.1 A-Share Policy

Existing A-share behavior remains intact:

- A-share board classification.
- ST filtering and labels.
- Main-board, ChiNext, and STAR price-limit logic.
- A-share listing-window exemptions.
- Existing total-market-cap convention.
- Existing provider warehouses and active-provider routing.
- Existing selection strategies and parameters.

### 6.2 Hong Kong Policy

Hong Kong uses its own policy adapter:

- Hong Kong code and exchange conventions.
- Hong Kong trading calendar.
- HKD currency formatting.
- Hong Kong list status and delisting state.
- Hong Kong adjustment data when coverage is complete.
- No A-share ST inference.
- No A-share 10 percent or 20 percent price-limit inference.
- No A-share board or IPO exemption rules.
- Hong Kong-specific liquidity filters and defaults.
- Hong Kong-specific strategy enablement and parameter overrides.

Market-neutral indicator implementations may be shared. Strategy registration
must declare whether a strategy is market-neutral, A-share-only, or Hong
Kong-specific. Shared calculations do not imply shared business rules.

## 7. Deep Links And Navigation State

Introduce an internal equity route contract using URL hash navigation so it
works inside Flask, pywebview, macOS, and future Windows packaging without
server rewrite rules.

Examples:

```text
#/equities/a-share/instrument/002594.SZ
#/equities/hong-kong/instrument/00700.HK
#/industry/market/801880.SI
```

Opening an instrument link performs these steps atomically:

1. Validate the market and instrument.
2. Switch the equity market context when required.
3. Open the correct instrument detail and K-line.
4. Store the source page state in `history.state`.
5. Restore source filters, sorting, selected industry, and scroll position on
   browser back.

Industry rows, company names, financial highlights, search results, watchlists,
selection results, and structured log references use the same route helper.

## 8. Data Architecture

### 8.1 Existing A-Share Data

Keep the existing repository structure and contracts:

```text
data/providers/<provider>/
data/providers/tushare/extended/tushare_ext.sqlite
```

Do not merge Hong Kong or futures records into A-share provider CSVs.

### 8.2 New Domain Stores

Under the resolved writable data root, use independent SQLite stores:

```text
data/markets/hong_kong/hong_kong.sqlite
data/markets/futures/futures.sqlite
data/economy/economy.sqlite
data/industry/industry.sqlite
data/ops/ops.sqlite
```

Each market-data store uses WAL mode, explicit schema migrations, indexed date
and symbol columns, and batched transactional upserts.

Shared table concepts:

- Dataset rows or normalized domain tables.
- Dataset sync cursor.
- Dataset permission/status record.
- Schema version.
- Source and update timestamp.

The domain service owns normalization. UI code never reads SQLite directly.

### 8.3 Hong Kong Datasets

Initial Tushare-backed datasets include:

- `hk_basic`
- `hk_tradecal`
- `hk_daily`
- Hong Kong adjustment factors when allowed and complete
- Hong Kong income statement
- Hong Kong balance sheet
- Hong Kong cash flow
- Hong Kong financial indicators

The current configured token was verified to call `hk_basic` and `hk_daily`.
Every additional endpoint must still be preflighted before a full sync.

Tushare `hk_basic` does not expose industry classification. The Hong Kong
heatmap therefore declares grouping capability explicitly:

- Use validated industry enrichment when a supported source is configured.
- Otherwise provide honest market-segment and performance-distribution views.
- Never label the `market` field as an industry classification.
- Preserve an `unclassified` bucket and surface mapping coverage.

### 8.4 Futures Datasets

Initial datasets include:

- `fut_basic`
- `fut_daily`
- Main and continuous contract mapping
- Exchange calendar when supported
- Product and exchange metadata derived from contract records

The current configured token was verified to call `fut_basic` and `fut_daily`.
Minute, tick, warehouse-receipt, position-ranking, and settlement datasets are
not part of the default sync.

### 8.5 Macro Datasets

Initial series families include:

- Growth: `cn_gdp`
- Inflation: `cn_cpi`, `cn_ppi`
- Money: `cn_m`
- Rates: `shibor` and other explicitly enabled rate series
- Cycle: `cn_pmi`

The current configured token was verified to call all listed initial series.
Every series stores a registry record containing frequency, unit, value fields,
release date semantics, display precision, and source.

### 8.6 Industry Datasets

Market Industry initially includes:

- `index_classify` for SW2021 classification
- `index_member_all` for hierarchical constituents
- SW industry daily index series when permission is verified
- Industry valuation, capital-flow, and ranking sources only when their
  endpoint permissions and units are verified

The current configured token was verified to call `index_classify` and
`index_member_all`.

Industrial Cycle initially includes:

- PMI headline and component series
- PPI headline and component series
- Other macro series only after their unit, frequency, and permission are
  registered

The industry page never mixes stock-return percentages with economic index
levels on the same axis without an explicit dual-axis or normalization mode.

## 9. Synchronization Design

### 9.1 Dataset Registry

Every new dataset is registered with:

- Domain and market.
- Tushare method.
- Parameters and field list.
- Primary key.
- Symbol and date fields.
- Full-sync strategy.
- Incremental cursor strategy.
- Expected frequency.
- Batch size.
- Rate-limit policy.
- Permission behavior.
- Freshness threshold.
- Optional status.

This registry is the only source of truth for new-domain sync behavior.

### 9.2 Task Lifecycle

All sync tasks use the same lifecycle:

```text
preflight -> plan -> fetch -> normalize -> transactional write
-> cache refresh -> quality check -> terminal state
```

Terminal states are:

- `completed`
- `completed_with_warnings`
- `failed`
- `cancelled`

Tasks have per-job cancellation events. Timed-out or cancelled work cannot
replace files or commit an incomplete transaction after the terminal state.

### 9.3 Initial And Incremental Sync

- Empty stores show first-run guidance and require an explicit full-sync action.
- Packaged builds never perform a network sync merely by opening the app.
- Subsequent syncs start from the persisted dataset cursor.
- Macro and industry series use release/frequency-aware cursors.
- Futures contract metadata refreshes before daily-price planning.
- Hong Kong basic metadata and calendar refresh before daily-price planning.
- Optional endpoint failures become warnings and do not roll back required
  datasets that completed successfully.

### 9.4 Concurrency

Each domain has a bounded concurrency and rate-limit budget. A large Hong Kong
sync cannot consume the worker budget reserved for an active A-share selection
or system diagnostic.

Admission rules prevent conflicting writes to the same store. Read-only page
queries remain available during safe WAL-backed writes.

## 10. Operations And Logging

### 10.1 Structured Event Contract

Every task event contains:

- Timestamp.
- Severity: debug, info, warning, error, critical.
- Correlation/job ID.
- Domain.
- Market.
- Module.
- Dataset.
- Instrument when applicable.
- Stage.
- Message.
- Error code.
- Duration in milliseconds when applicable.
- Bounded structured details.

Tokens, webhooks, authorization headers, private configuration values, and raw
provider responses that may contain secrets are redacted before persistence.

### 10.2 Storage

- Rotating JSONL keeps crash-readable evidence.
- `ops.sqlite` provides indexed search and aggregation.
- Existing error-report JSON remains readable and can be indexed into the new
  event view without destructive conversion.

Default retention:

- Task and error events: 30 days, configurable.
- Fine-grained performance samples: 7 days, then aggregate.
- Active tasks: never prune.
- Market data: never delete because the log budget is exceeded.

### 10.3 System Workspace

The System workspace has:

- Tasks: live progress, cancellation, duration, warnings, and latest stage.
- Events: filters for time, severity, domain, market, module, dataset, symbol,
  job ID, and error code.
- Health: data freshness, permission, database integrity, disk space, task
  conflicts, cache coverage, and API circuit state.
- Performance: API counts, latency, queue depth, database duration, payload
  size, cache hit rate, and retained job/event counts.
- Configuration: credential-source status and paths without displaying secrets.

Diagnostic export produces a sanitized bundle of selected events, job state,
health summary, schema versions, and environment metadata.

## 11. Front-End States

Every new workspace implements:

- Loading: skeleton matching the final layout.
- Empty store: explains the missing data, credential method, and full-sync
  action.
- Ready: renders cached local data and freshness metadata.
- Warning: keeps available data visible and explains partial failure.
- Error: shows error code, task ID, retry/cancel, and diagnostic navigation.
- Syncing: shows bounded progress and keeps safe reads available.

The UI must not display missing values as zero.

## 12. Cross-Platform Preparation

### 12.1 Path Resolver

Add one platform path authority. No business module builds macOS or Windows
paths directly.

Resolution order:

1. Explicit environment override `AQS_RUNTIME_ROOT`.
2. Existing source-checkout paths when running from source.
3. Platform user-data root when running as a packaged application.

Packaged defaults:

- macOS: `~/Library/Application Support/A股量化选股系统`
- Windows: `%LOCALAPPDATA%\A股量化选股系统`

The existing `A_SHARE_QUANT_OUTPUT_ROOT` override remains supported for output
compatibility.

### 12.2 Runtime Directory Layout

An empty packaged install creates only directories and templates:

```text
config/
data/markets/hong_kong/
data/markets/futures/
data/economy/
data/industry/
data/ops/
logs/
outputs/selection/
outputs/wyckoff/
webview/
```

### 12.3 Credentials

Supported credential methods:

- Environment variables `TUSHARE_TOKEN` and `DEEPSEEK_API_KEY`.
- Ignored machine-local configuration created from committed templates.
- Future settings UI writing only to the resolved local configuration path.

Packages contain empty templates only. Build verification scans packaged
resources for credential patterns.

### 12.4 Resource Boundary

- Application code, templates, CSS, JS, icons, and static reference assets are
  read-only package resources.
- Databases, logs, caches, outputs, local configuration, and webview profiles are
  writable runtime data.
- Resource lookup must work both from source and a frozen executable.
- macOS-specific icon conversion and signing stay in the macOS builder.
- Windows icon, WebView2 profile, and future installer logic stay in a separate
  Windows builder.
- Native C-core loading continues to select `.dylib`, `.dll`, or `.so` by
  platform and falls back to Python.

## 13. API Surface

New APIs use explicit domain names and existing session-token protection for
side effects.

Read APIs:

```text
GET /api/markets/capabilities
GET /api/equities/<market>/overview
GET /api/equities/<market>/instruments
GET /api/equities/<market>/instrument/<symbol>
GET /api/futures/contracts
GET /api/futures/kline/<symbol>
GET /api/macro/series
GET /api/macro/series/<series_id>
GET /api/industry/classifications
GET /api/industry/<industry_id>
GET /api/industry/cycle/series
GET /api/ops/tasks
GET /api/ops/events
GET /api/ops/health
GET /api/ops/performance
```

Side-effect APIs:

```text
POST /api/sync/start
POST /api/sync/cancel/<job_id>
POST /api/ops/diagnostics/export
```

All list APIs enforce server-side pagination and bounded sort/filter fields. All
symbol, market, dataset, job ID, and date parameters are validated at the API
boundary.

## 14. Performance Design

- Initialize only the active workspace.
- Cache market capabilities and small registries.
- Use server-side pagination for instruments, industry constituents, and logs.
- Keep K-line payloads bounded and reuse the existing indicator-lookback model.
- Downsample long macro series for overview charts while preserving exact rows
  for detail/table export.
- Batch SQLite writes and build indexes for actual query patterns.
- Use source signatures for derived caches and exclude a cache from its own
  signature.
- Keep in-memory task and event collections bounded.
- Avoid fetching page data for hidden markets.
- Avoid rendering unbounded table rows in the browser.
- Record enough performance telemetry to identify regressions without logging
  every low-level operation indefinitely.

## 15. Error Handling And Data Quality

- Permission failures become dataset-specific warning states.
- Authentication failure blocks only work that requires that provider.
- Network circuit state is visible in System Health.
- Empty legal responses are distinct from network failures.
- Unit metadata is mandatory for macro and industry-cycle values.
- Price/currency displays are market-aware.
- Partial adjusted-price coverage never replaces a complete raw or compatibility
  payload.
- Industry mapping coverage is shown explicitly.
- Freshness is evaluated per dataset frequency, not against one daily rule.
- Database integrity and migration failures block writes and preserve the old
  store for diagnosis.

## 16. Verification Strategy

### 16.1 Unit Tests

- Platform path matrix for source, macOS package, and Windows package modes.
- Dataset registry validation.
- Store migrations, keys, pagination, and transactions.
- A-share and Hong Kong policy separation.
- Sync planning, cursor updates, permission warnings, cancellation, and timeout
  write guards.
- Event redaction, retention, filtering, and correlation.
- Macro units and industry hierarchy normalization.

### 16.2 Integration Tests

- Empty-store first run.
- Bounded Hong Kong, futures, macro, and industry syncs into temporary stores.
- Incremental rerun after initial data.
- Partial permission failure while required datasets succeed.
- Concurrent safe reads during WAL writes.
- Diagnostic export without secrets.
- Legacy A-share update and selection regression coverage.

### 16.3 Front-End Tests

- A-share/Hong Kong context switches all equity panels.
- Industry component opens the correct market K-line.
- Browser back restores industry state.
- Loading, empty, warning, error, and syncing states.
- System event filters and task cancellation.
- Keyboard focus, compact desktop layout, and responsive collapse.
- JavaScript syntax and browser console errors.

### 16.4 Performance Checks

- Bounded API payload sizes.
- Indexed queries for instruments, events, and industry members.
- Task/event retention bounds.
- Repeated page switches do not multiply listeners or timers.
- Hidden workspaces do not issue repeated data requests.
- Full regression suite after focused tests.

No full-market production update or notification send is required merely to
verify unrelated code. Live provider checks use bounded temporary stores.

## 17. Delivery Decomposition

The implementation is one product change but contains four reviewable tracks:

1. Platform foundation and market-context shell.
2. Domain stores, registries, sync engine, and data APIs.
3. Equities/Hong Kong behavior plus futures, macro, and industry workspaces.
4. Operations center, performance instrumentation, visual polish, and full
   verification.

Each track must remain independently testable. The final handoff is one
uncommitted working tree, as requested by the user.

## 18. Acceptance Criteria

- Top-level navigation contains Equities, Futures, Macro, Industry, and System.
- Equities exposes one persistent A-share/Hong Kong context switch.
- All equity features follow the selected market.
- A-share policies never run silently against Hong Kong instruments.
- Hong Kong instruments can be searched, charted, selected with supported
  strategies, watched, and sent to Wyckoff analysis.
- Futures can be synchronized, searched, and charted locally.
- Macro series can be synchronized, browsed by family, compared, and charted.
- Industry supports both market-industry and industrial-cycle modes.
- Industry instruments deep-link to the correct K-line and back navigation
  restores state.
- All new stores expose freshness, sync state, and permission warnings.
- System exposes live tasks, searchable events, health, performance, and
  sanitized diagnostics.
- The primary canvas remains pure black with vivid semantic red and green,
  orange interaction accents, industrial grid structure, and readable text.
- Existing A-share data and workflows remain compatible.
- Runtime paths are platform-aware and package resources are separated from
  writable data.
- Packaged-build rules exclude all market data, logs, outputs, and credentials.
- Focused tests, the full pytest suite, JavaScript syntax, diff checks, and real
  browser interaction verification pass before completion is claimed.

