from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web/templates/index.html"
JS = ROOT / "web/static/js/system_workspace.js"


def test_system_workspace_has_required_views_and_script():
    html = HTML.read_text(encoding="utf-8")
    for view in ("ops-tasks", "ops-events", "ops-health", "ops-performance", "ops-config"):
        assert f'id="{view}"' in html
    assert "system_workspace.js" in html
    assert html.index("system_workspace.js") < html.index("app.js")


def test_system_controller_uses_bounded_ops_apis_and_lifecycle():
    js = JS.read_text(encoding="utf-8")
    for endpoint in ("/api/ops/tasks", "/api/ops/events", "/api/ops/health", "/api/ops/performance"):
        assert endpoint in js
    for method in ("mount()", "activate()", "deactivate()", "refresh()"):
        assert method in js
    assert "AbortController" in js
    assert "clearInterval" in js
    assert "quantEquityRouter.openInstrument" in js
    assert "/api/ops/diagnostics/export" in js
    assert 'id="ops-diagnostics-export"' in HTML.read_text(encoding="utf-8")


def test_system_controller_builds_structured_event_filters_without_template_changes():
    js = JS.read_text(encoding="utf-8")

    for field in (
        "since", "until", "severity", "domain", "market", "module",
        "dataset", "symbol", "job_id", "error_code",
    ):
        assert f"'{field}'" in js
    assert "data-ops-filter" in js
    assert "URLSearchParams" in js


def test_system_controller_exposes_validated_task_cancellation_and_job_diagnostics():
    js = JS.read_text(encoding="utf-8")

    assert "/api/ops/tasks/" in js
    assert "data-ops-cancel" in js
    assert "openJob(jobId)" in js
    assert "SUPPORTED_CANCEL_TYPES" in js
    for task_type in ("selection", "update", "diagnostic", "wyckoff", "domain_sync"):
        assert f"'{task_type}'" in js
