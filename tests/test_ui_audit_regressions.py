from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def _text(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_docling_review_requires_a_real_book_job_before_loading() -> None:
    script = _text("docling-review.js")
    assert "Number.isInteger(jobId) || jobId <= 0" in script
    assert "window.location.replace('/review-center')" in script
    assert "loadPage(initialPage" in script


def test_docling_review_uses_shared_workspace_shell() -> None:
    page = _text("docling-review.html")
    assert 'class="mobile-topbar"' in page
    assert 'class="sidebar" id="sidebar"' in page
    assert 'id="nav-backdrop"' in page
    assert 'class="nav" aria-label="Main navigation"' in page
    assert "workspace-task-nav" not in page


def test_technical_evidence_uses_shared_shell_and_visible_status() -> None:
    page = _text("technical-evidence.html")
    assert 'class="mobile-topbar"' in page
    assert 'class="sidebar" id="sidebar"' in page
    assert 'id="nav-backdrop"' in page
    assert 'id="status" class="status-message page-feedback" role="status" aria-live="polite"></div>' in page
    assert "workspace-task-nav" not in page


def test_workspace_hubs_do_not_poll_full_document_pipeline() -> None:
    script = _text("workspace-hub.js")
    assert "/api/documents" not in script
    assert "/api/workers" in script
    assert "/api/review-workers/status" in script
    assert "document.visibilityState !== 'visible'" in script


def test_books_library_throttles_expensive_document_refresh() -> None:
    script = _text("workflow.js")
    assert "document.visibilityState !== 'visible'" in script
    assert "}, 30000);" in script
    assert "visibilitychange" in script


def test_quality_polling_stops_in_hidden_tabs() -> None:
    script = _text("quality.js")
    assert "document.visibilityState !== 'visible'" in script
    assert "visibilitychange" in script
    assert "pagehide" in script


def test_nav_loads_shared_audit_css_and_refreshes_attention_status() -> None:
    script = _text("nav.js")
    assert "ui-audit-fixes.css" in script
    assert "setInterval(refreshAttentionStrip, 30000)" in script
    assert "visibilitychange" in script


def test_shared_audit_css_hides_closed_drawer_and_preserves_table_actions() -> None:
    css = _text("ui-audit-fixes.css")
    assert '.settings-drawer[aria-hidden="true"]' in css
    assert "visibility: hidden" in css
    assert ".table-wrap:has(#quality-jobs)" in css
    assert ".table-wrap:has(#verification-books)" in css
    assert "position: sticky" in css


def test_shared_audit_css_targets_real_verifier_audit_cards() -> None:
    css = _text("ui-audit-fixes.css")
    assert ".vision-audit-card > .vision-audit-card-head" in css
    assert ".vision-audit-card > .vision-audit-main-grid" in css
    assert ".vision-audit-card .vision-audit-source-image" in css
    assert "overflow-wrap: anywhere" in css


def test_human_review_textareas_have_accessible_labels() -> None:
    page = _text("review.html")
    assert '<label class="visually-hidden" for="original">Raw Docling target text</label>' in page
    assert '<label class="visually-hidden" for="correction">Human correction text</label>' in page
