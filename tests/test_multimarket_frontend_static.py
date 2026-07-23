from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "web" / "templates" / "index.html"
STYLE_CSS = PROJECT_ROOT / "web" / "static" / "css" / "style.css"
APP_JS = PROJECT_ROOT / "web" / "static" / "js" / "app.js"
EQUITY_ROUTER_JS = PROJECT_ROOT / "web" / "static" / "js" / "equity_router.js"


def test_equity_market_switch_and_router_are_loaded():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "market_context.js" in html
    assert "equity_router.js" in html
    assert 'id="equity-market-switch"' in html
    assert 'data-market="a_share"' in html
    assert 'data-market="hong_kong"' in html


def test_primary_workspaces_and_equity_subnavigation_are_separate():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="workspace-nav"' in html
    assert 'id="equity-context-bar"' in html
    assert 'data-workspace="equities"' in html
    for workspace in ("futures", "macro", "industry", "system"):
        assert f'data-workspace="{workspace}"' in html
    for page in ("dashboard", "heatmap", "stocks", "selection", "strategies", "watchlist", "wyckoff"):
        assert f'data-equity-page="{page}"' in html
    assert html.index('id="equity-market-switch"') > html.index('id="equity-context-bar"')


def test_primary_workspace_function_keys_are_f1_through_f5():
    html = INDEX_HTML.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")
    workspace = html[html.index('id="workspace-nav"'):html.index('id="equity-context-bar"')]

    for key, label in (("F1", "EQUITIES"), ("F2", "FUTURES"), ("F3", "MACRO"), ("F4", "INDUSTRY"), ("F5", "SYSTEM")):
        assert key in workspace
        assert label in workspace
    for legacy_key in ("F8", "F9", "F10", "F11"):
        assert legacy_key not in workspace
    assert "command === 'F2' || command === 'FUTURES'" in app_js
    assert "command === 'F5' || command === 'SYSTEM'" in app_js


def test_terminal_uses_approved_semantic_tokens():
    css = STYLE_CSS.read_text(encoding="utf-8").lower()

    assert "--bg-void: #000000" in css
    assert "--accent: #ff6900" in css
    assert "--price-up: #ff3131" in css
    assert "--price-down: #00ff41" in css
    assert "--grid-line: #303030" in css
    assert ".market-pulse-grid > .state-loading" in css
    assert "background: var(--bg-void)" in css
    assert ".content {" in css and ".page {" in css


def test_equity_surfaces_use_active_market_and_never_fall_back_silently():
    js = APP_JS.read_text(encoding="utf-8")

    assert "currentEquityMarket()" in js
    assert "/api/equities/hong_kong/overview" in js
    assert "/api/equities/hong_kong/instruments" in js
    assert "/api/equities/hong_kong/instrument/" in js
    assert "quant:market-change" in js
    assert "currentEquityMarket() === 'hong_kong'" in js
    assert "'/api/equities/hong_kong/selection/start'" in js
    assert "/api/equities/hong_kong/watchlist" in js
    assert "/api/equities/hong_kong/wyckoff/start" in js
    assert "港股分析将只读取独立港股仓库" in js


def test_instrument_hash_routes_open_canonical_market_detail_and_restore_source():
    app_js = APP_JS.read_text(encoding="utf-8")
    router_js = EQUITY_ROUTER_JS.read_text(encoding="utf-8")

    assert "parseInstrumentRoute" in router_js
    assert "cleanSymbol.split('.', 1)[0]" in router_js
    assert "sourceState" in router_js
    instrument_activation = app_js.split("async function activateInstrumentRoute", 1)[1].split(
        "function returnFromInstrumentRoute", 1
    )[0]
    instrument_open = router_js.split("function openInstrument", 1)[1].split(
        "function restoreSourceState", 1
    )[0]
    assert "setMarket(route.market, { silent: true })" in instrument_activation
    assert "resetEquityMarketCaches()" in instrument_activation
    assert "await loadStats()" in instrument_activation
    assert "setMarket(" not in instrument_open


def test_equity_market_hash_restores_market_context_and_workspace():
    router_js = EQUITY_ROUTER_JS.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")

    assert "parseMarketRoute" in router_js
    assert "marketRouteFor" in router_js
    assert "equityMarketFromLocation" in app_js
    assert "setMarket(equityMarket, { silent: true })" in app_js


def test_hong_kong_dashboard_does_not_reuse_a_share_market_copy():
    html = INDEX_HTML.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "web" / "static" / "css" / "style.css").read_text(encoding="utf-8")

    for element_id in (
        "hero-market-label",
        "hero-market-symbol",
        "board-label-main",
        "board-label-secondary",
        "board-label-tertiary",
        "operator-note-1",
    ):
        assert f'id="{element_id}"' in html
    assert "renderHongKongDashboard" in app_js
    assert "HONG KONG UNIVERSE" in app_js
    assert "港股规则与 HKD 口径" in app_js
    assert ".quote-chart-toolbar [hidden]" in css
    assert "quant:open-instrument" in app_js
    assert "quant:return-to-source" in app_js
    assert "restoreSourceState" in app_js


def test_hong_kong_heatmap_never_loads_a_share_meta_or_copy():
    html = INDEX_HTML.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")

    assert 'id="heatmap-panel-title"' in html
    assert 'id="heatmap-scope-section"' in html
    assert 'id="heatmap-metric-section"' in html
    assert "prepareHeatmapForMarket" in app_js
    assert "HONG KONG HEATMAP" in app_js
    assert "全部港股" in app_js
    assert "最新交易日" in app_js
    meta_function = app_js.split("async function loadHeatmapMeta", 1)[1].split(
        "async function loadHeatmapHealth", 1
    )[0]
    assert meta_function.index("currentEquityMarket() === 'hong_kong'") < meta_function.index(
        "apiFetch('/api/heatmap/meta')"
    )
    assert "prepareHeatmapForMarket();\n        loadHeatmapMeta();" in app_js


def test_hong_kong_strategies_use_market_options_and_a_share_restores_config():
    html = INDEX_HTML.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")

    assert 'id="strategies-panel-title"' in html
    assert 'id="strategies-panel-subtitle"' in html
    strategy_function = app_js.split("async function loadStrategies", 1)[1].split(
        "function parseInputValue", 1
    )[0]
    hk_branch = strategy_function.split("currentEquityMarket() === 'hong_kong'", 1)[1]
    assert "/api/equities/hong_kong/selection/options" in hk_branch
    assert hk_branch.index("/api/equities/hong_kong/selection/options") < hk_branch.index(
        "apiFetch('/api/config')"
    )
    assert "renderHongKongStrategies" in strategy_function
    assert "暂无已验证的港股策略" in app_js
    assert "saveButton.hidden = isHongKong" in app_js
