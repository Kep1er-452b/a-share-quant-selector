from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_index_controls_and_stock_layout_markup_exist():
    html = (ROOT / "web/templates/index.html").read_text(encoding="utf-8")

    assert 'id="dashboard-index-months"' in html
    assert 'id="dashboard-index-detail-btn"' in html
    assert 'aria-label="查看指数详细信息"' in html
    assert 'class="stock-detail-layout"' in html
    assert 'id="stock-indicator-toolbar"' in html


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


def test_stock_info_panels_keep_empty_extension_sections_visible():
    js = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
    assert "displayValue" in js
    assert "? '--' : item.value" in js


def test_universe_count_has_purple_style_hook():
    css = (ROOT / "web/static/css/style.css").read_text(encoding="utf-8")
    assert ".quote-price.universe-count" in css
