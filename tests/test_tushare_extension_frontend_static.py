from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_index_controls_and_stock_layout_markup_exist():
    html = (ROOT / "web/templates/index.html").read_text(encoding="utf-8")

    assert 'id="dashboard-index-months"' in html
    assert 'id="dashboard-index-detail-btn"' in html
    assert 'aria-label="查看指数详细信息"' in html
    assert 'class="stock-detail-layout"' in html
    assert 'id="stock-indicator-toolbar"' in html
    assert 'id="stock-range-toolbar"' in html
    assert 'data-limit="all"' in html
    assert 'id="stock-sequence-toggle"' in html
    assert 'class="indicator-toggle sequence-toggle active"' in html
    assert 'class="sequence-toggle-icon"' in html


def test_frontend_uses_adjusted_data_indicator_settings_and_chinese_tooltips():
    js = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")

    assert "quantMaSettings" in js
    assert "quantMacdSettings" in js
    assert "adjusted_data" in js
    assert "months=${encodeURIComponent(state.indexMonths)}" in js
    assert "开盘" in js
    assert "最高" in js
    assert "最低" in js
    assert "收盘" in js
    assert "成交量" in js
    assert "MACD" in js
    assert "涨跌幅" in js
    assert "formatTooltipChangePct" in js
    assert "quantStockChartLimit" in js
    assert "limit=${encodeURIComponent(state.currentStockLimit)}" in js
    assert "right: 48" in js
    assert "quantShowSequenceMarkers" in js
    assert "showSequenceMarkers ? buildSequenceMarks" in js


def test_stock_info_panels_keep_empty_extension_sections_visible():
    js = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
    assert "displayValue" in js
    assert "? '--' : item.value" in js
    assert "formatWanYuanMarketValue" in js
    assert "formatTradingValue" in js


def test_frontend_review_hardening_for_watchlist_and_wyckoff_polling():
    js = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")

    assert 'colspan="10" class="state-loading"' in js
    assert "escapeHtml(BOARD_LABELS[board] || board)" in js

    start = js.index("state.wyckoffJobId = result.job_id;")
    immediate_poll = js.index("await pollWyckoffJob();", start)
    interval_start = js.index("state.wyckoffPollTimer = window.setInterval", start)
    assert immediate_poll < interval_start
    assert "job.status === 'cancelled'" in js


def test_universe_count_has_purple_style_hook():
    css = (ROOT / "web/static/css/style.css").read_text(encoding="utf-8")
    assert ".quote-price.universe-count" in css
    assert ".sequence-toggle-icon" in css
