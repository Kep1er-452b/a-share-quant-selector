from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "web" / "templates" / "index.html"
STYLE_CSS = PROJECT_ROOT / "web" / "static" / "css" / "style.css"
APP_JS = PROJECT_ROOT / "web" / "static" / "js" / "app.js"
FUTURES_JS = PROJECT_ROOT / "web" / "static" / "js" / "futures_workspace.js"
MACRO_JS = PROJECT_ROOT / "web" / "static" / "js" / "macro_workspace.js"
INDUSTRY_JS = PROJECT_ROOT / "web" / "static" / "js" / "industry_workspace.js"
DOMAIN_SYNC_JS = PROJECT_ROOT / "web" / "static" / "js" / "domain_sync.js"


def test_dedicated_domain_pages_and_navigation_exist():
    html = INDEX_HTML.read_text(encoding="utf-8")

    for page in ("futures-page", "macro-page", "industry-page", "system-page"):
        assert f'id="{page}"' in html
    for page in ("futures", "macro", "industry", "system"):
        assert f'data-page="{page}"' in html


def test_domain_workspace_scripts_load_before_the_application_shell():
    html = INDEX_HTML.read_text(encoding="utf-8")

    for script in (
        "futures_workspace.js",
        "macro_workspace.js",
        "industry_workspace.js",
    ):
        assert script in html
        assert html.index(script) < html.index("app.js")


def test_domain_workspaces_offer_sync_status_cancel_and_retry_controls():
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = DOMAIN_SYNC_JS.read_text(encoding="utf-8")

    for domain in ("hong_kong", "futures", "macro", "industry"):
        assert f'data-sync-domain="{domain}"' in html
    assert "/api/sync/start" in js
    assert "/api/sync/status/" in js
    assert "/api/sync/cancel/" in js
    assert "RETRY" in html
    assert "TOKEN_MISSING" in js


def test_domain_sync_renders_visible_structured_job_details_and_diagnostic_entry():
    js = DOMAIN_SYNC_JS.read_text(encoding="utf-8")

    for detail in (
        "domain-sync-details",
        "JOB",
        "DATASET",
        "PHASE",
        "WRITTEN",
        "WARNING",
        "ERROR CODE",
        "ERROR",
        "DIAGNOSTIC",
    ):
        assert detail in js
    assert "quantSystemWorkspace.openJob" in js
    assert "replaceChildren" in js


def test_domain_sync_button_visibility_and_permission_failures_have_explicit_contracts():
    css = STYLE_CSS.read_text(encoding="utf-8")
    js = DOMAIN_SYNC_JS.read_text(encoding="utf-8")

    assert ".domain-sync-controls [hidden]" in css
    assert "display: none !important" in css
    assert "permission_denied" in js
    assert "isPermissionDenied" in js
    assert "control.start.hidden = active || permissionDenied" in js
    assert "control.cancel.hidden = !active" in js
    assert "detail.retryable !== false" in js
    assert "control.retry.hidden = !canRetry || permissionDenied" in js
    assert "job.status !== 'cancelled'" in js
    assert "refreshWorkspace(control.domain)" in js
    assert "datasets: ['fut_basic']" in js
    assert "start: (domain, options = {})" in js


def test_each_domain_controller_has_a_bounded_abortable_lifecycle():
    for path in (FUTURES_JS, MACRO_JS, INDUSTRY_JS):
        js = path.read_text(encoding="utf-8")
        for method in ("mount()", "activate()", "deactivate()", "refresh()"):
            assert method in js
        assert "AbortController" in js
        assert ".abort()" in js
        assert "removeEventListener" in js


def test_futures_workspace_supports_contract_search_and_kline():
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = FUTURES_JS.read_text(encoding="utf-8")

    for control in (
        "futures-search",
        "futures-exchange",
        "futures-product",
        "futures-contracts-body",
        "futures-kline",
        "futures-sync-contract",
    ):
        assert f'id="{control}"' in html
    assert "/api/futures/contracts" in js
    assert "/api/futures/kline/" in js
    assert "renderChartMessage" in js
    assert "NO LOCAL DAILY DATA" in js
    assert "CACHED CONTRACT LIST:" in js
    assert "hasCandles ? 'KLINE READY' : 'NO LOCAL DAILY DATA'" in js
    assert "datasets: ['fut_daily']" in js
    assert "scope: `contract:${symbol}`" in js


def test_macro_workspace_exposes_family_series_units_and_exact_table():
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = MACRO_JS.read_text(encoding="utf-8")

    for control in (
        "macro-family-nav",
        "macro-series-list",
        "macro-chart",
        "macro-unit-legend",
        "macro-exact-body",
    ):
        assert f'id="{control}"' in html
    assert "/api/macro/catalog" in js
    assert "/api/macro/series" in js
    assert "item.unit" in js
    assert "family" in js
    assert "type: 'category'" in js
    assert "yAxisIndex" in js
    assert "payload.axis_mode" in js
    assert "item.chart_type === 'bar'" in js
    assert "connectNulls: false" in js
    assert "item.display_note || item.field" in js
    assert "clearSeriesState" in js
    assert "NO LOCAL SERIES DATA" in js
    assert "NO SERIES SELECTED" in js
    assert "Math.max(1, series.length + 1)" in js
    assert "增长默认使用单季度值" in html
    assert "累计值使用柱形" in html


def test_industry_workspace_has_two_layers_and_shared_instrument_deep_links():
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = INDUSTRY_JS.read_text(encoding="utf-8")

    assert 'data-industry-mode="market"' in html
    assert 'data-industry-mode="cycle"' in html
    assert 'id="industry-members-body"' in html
    assert "/api/industry/classifications" in js
    assert "/api/industry/detail/" in js
    assert "/api/industry/cycle-series" in js
    assert "quantEquityRouter.openInstrument" in js
    assert "yAxisIndex" in js
    assert "payload.axis_mode" in js
    assert "clearIndustryDetail" in js
    assert "PARTIAL LOCAL DETAIL" in js
    assert "NO LOCAL CYCLE DATA" in js
    assert "state.industryId = null" in js


def test_application_shell_activates_and_deactivates_domain_controllers():
    js = APP_JS.read_text(encoding="utf-8")

    assert "quantDomainWorkspaces" in js
    assert ".deactivate()" in js
    assert ".activate()" in js
    for page in ("futures", "macro", "industry", "system"):
        assert f"{page}:" in js
    assert "workspacePageFromLocation" in js
    assert "window.addEventListener('hashchange', restoreWorkspaceRoute)" in js
    assert "window.addEventListener('popstate', restoreWorkspaceRoute)" in js
    assert "switchPage(workspaceRoute, { syncRoute: false })" in js


def test_domain_workspaces_follow_approved_terminal_tokens_and_square_geometry():
    css = STYLE_CSS.read_text(encoding="utf-8").lower()

    assert ".domain-workspace" in css
    assert "background: var(--bg-void)" in css
    assert "border: 1px solid var(--grid-line)" in css
    assert "border-radius: 0" in css
    assert "color: var(--price-up)" in css
    assert "color: var(--price-down)" in css
