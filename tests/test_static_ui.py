from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_styles_have_one_root_token_block():
    css = read("styles.css")
    assert css.count(":root {") == 1


def test_shared_component_selectors_are_not_redeclared():
    import re
    css = read("styles.css")
    for selector in [".field-help", ".document-actions", ".mini-action", ".watcher-format-group"]:
        matches = re.findall(rf"(?m)^{re.escape(selector)}\s*\{{", css)
        assert len(matches) == 1, selector


def test_no_native_alert_calls_in_frontend():
    for path in STATIC.glob("*.js"):
        assert "alert(" not in path.read_text(encoding="utf-8"), path.name


def test_error_retry_keeps_stable_label_and_inline_feedback():
    js = read("errors.js")
    assert 'const originalLabel = "Retry conversion"' in js
    assert 'button.textContent = originalLabel' in js
    assert 'data-retry-feedback' in js


def test_quality_page_uses_user_facing_copy():
    html = read("quality.html")
    js = read("quality.js")
    assert "Stage 2A" not in html
    assert "Non-destructive mode" not in html
    assert "prepare Pi5 / OnePlus routes" not in html
    assert "entered Stage 2" not in js


def test_convert_acronyms_and_sentence_case():
    html = read("convert.html")
    assert ">VLM<" in html
    assert ">ASR<" in html
    assert ">Abort on error<" in html


def test_verification_has_own_page_and_quality_is_not_stage2b_dashboard():
    quality = read("quality.html")
    verification = read("verification.html")
    js = read("verification.js")
    assert "Run Pi5 and OnePlus routes" not in quality
    assert "<h1>Verification</h1>" in verification
    assert "Verify book" in js
    assert "Auto verify all" in verification
    assert "/api/stage2b/results/pi5" in js
    assert "/api/stage2b/results/oneplus" in js
    assert "Stop verifier" in verification
    assert "Remaining text work" not in verification
    assert "Remaining vision work" not in verification
    assert "slice(0, 40)" not in js


def test_verification_device_status_controls_are_always_visible():
    html = read("verification.html")
    js = read("verification.js")
    assert "Live verifier status" in html
    assert 'id="pi5-health"' in html
    assert 'id="oneplus-health"' in html
    assert 'id="pi5-start"' in html
    assert 'id="oneplus-start"' in html
    assert 'id="pi5-stop"' in html
    assert 'id="oneplus-stop"' in html
    assert 'id="pi5-auto"' in html
    assert 'id="oneplus-auto"' in html
    assert "device-controls-disclosure" not in html
    assert "Alive" in js and "Offline" in js
    assert "/api/stage2b/${target}/start" in js
    assert "window.startVerifier = startVerifier" in js
    assert js.count("pollVerification();") == 1


def test_stage2b_ui_uses_inline_feedback_not_alerts():
    js = read("verification.js")
    assert "feedback(" in js
    assert "alert(" not in js


def test_all_primary_pages_link_to_verification():
    for name in ["index.html", "convert.html", "quality.html", "errors.html"]:
        assert 'href="/verification"' in read(name), name


def test_verification_polling_never_overlaps():
    js = read("verification.js")
    assert "refreshInFlight" in js
    assert "setInterval(" not in js
    assert "setTimeout(pollVerification, 3000)" in js


def test_oneplus_page_is_script_only_and_keeps_nonbusy_disabled_cursor():
    html = read("oneplus.html")
    js = read("oneplus.js")
    css = read("styles.css")
    assert "Install / update script" in html
    assert "Start llama server" in html
    assert "Restart llama server" in html
    assert "Stop llama server" in html
    assert "/api/oneplus-control/install-script" in js
    assert "/api/oneplus-control/models" not in js
    assert "/api/oneplus-control/logs" not in js
    assert "/api/oneplus-control/capture" not in js
    assert "model-path" not in html
    assert "mmproj-path" not in html
    assert "cursor: wait" not in css
    assert "cursor: not-allowed" in css


def test_oneplus_page_has_separate_ssh_reconnect_and_stop_controls():
    html = read("oneplus.html")
    js = read("oneplus.js")
    assert 'id="reconnect-ssh"' in html
    assert 'id="stop-ssh"' in html
    assert '/api/oneplus-control/ssh/${action}' in js
    assert 'sshAction("reconnect")' in js
    assert 'sshAction("stop")' in js



def test_oneplus_script_control_uses_canonical_layout_classes():
    html = Path("app/static/oneplus.html").read_text(encoding="utf-8")
    assert 'class="page-header"' in html
    assert 'class="panel oneplus-status-card"' in html
    assert 'class="oneplus-server-actions"' in html
    assert 'class="oneplus-ssh-actions"' in html
    assert 'danger-button' not in html
    assert 'class="status-card"' not in html
    assert 'class="page-heading"' not in html
    assert 'class="sidebar-foot"' not in html
    assert 'command-preview' not in html


def test_oneplus_server_action_buttons_have_responsive_grid_css():
    css = Path("app/static/styles.css").read_text(encoding="utf-8")
    assert ".oneplus-server-actions" in css
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in css
    assert ".oneplus-ssh-actions" in css
    assert ".danger-outline" in css


def test_overview_centralizes_progression_in_book_workflow():
    html = read("index.html")
    js = read("dashboard.js")
    assert "Processed library" in html
    assert 'id="documents-body"' in html
    assert "/api/documents" in js
    assert 'href="/book?job=${doc.id}"' in js
    assert "Build Stage 2C" not in js
    assert "Build chunks" not in js


def test_quality_page_is_diagnostic_and_links_back_to_book_workflow():
    js = read("quality.js")
    assert 'href="/book?job=${job.id}"' in js
    assert "/api/stage2c/books/${id}/build" not in js
    assert "/api/stage3/books/${id}/build" not in js


def test_book_workflow_uses_automatic_stage2c_and_optional_audit():
    html = read("book.html")
    js = read("book.js")
    assert "Hybrid" in html
    assert "Automatic corrections & enrichment" in js
    assert "Review is optional" in js
    assert "Keep original if unreadable" in js
    assert "Build Hybrid chunks" in js
    assert "/api/postprocess/jobs/${jobId}/human-review" in js
    assert "stage2c_auto_finalize" in js


def test_verification_exposes_manual_crossover_and_optional_audit():
    html = read("verification.html")
    js = read("verification.js")
    assert "Source-image reconstruction" in html
    assert "Re-read target → ${visionVerifierName()}" in js
    assert "Text consistency → ${textVerifierName()}" in js
    assert "/api/stage2b/jobs/${jobId}/crosscheck" in js
    assert "/review?job=${job.postprocess_job_id}" in js
    assert "/vision-audit?job=${encodeURIComponent(job.id)}" in js


def test_frontend_assets_and_badge_match_release_version():
    from app.version import APP_VERSION
    import re
    for page in STATIC.glob("*.html"):
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', page.read_text(encoding="utf-8"))
        assert assets
        assert all(asset.endswith("?v=" + APP_VERSION) for asset in assets)
    assert f'const version = "{APP_VERSION}"' in read("nav.js")


def test_review_page_uses_raw_docling_neighbor_context_for_manual_correction():
    html = read("review.html")
    js = read("review.js")
    css = read("review.css")
    assert "RAW DOCLING READING ORDER" in html
    assert "TEXT ABOVE" in html
    assert "OCR TARGET BLOCK" in html
    assert "TEXT BELOW" in html
    assert "Not Pi5" in html
    assert "Correction text" in html
    assert 'id="use-suggestion"' not in html
    assert 'id="reset"' in html
    assert "/docling-context" in js
    assert '$("correction").value = rawTarget' in js
    assert "diffTokens" in js
    assert "120000" in js  # bounded diff work for unexpectedly large OCR spans
    assert 'save("apply")' in js
    assert "/corrections/${encodeURIComponent(entryId)}" in js
    assert ".docling-neighbor" in css
    assert ".diff-removed" in css
    assert ".diff-added" in css
    assert 'id="back-workflow"' in html
    assert '/book?job=${encodeURIComponent(job)}' in js
    assert "Original text is correct" in html


def test_book_stage3_completed_state_shows_download_and_rebuild():
    js = read("book.js")
    assert "['ready','completed'].includes" in js
    assert "Download chunks" in js
    assert "Rebuild chunks" in js


def test_review_keeps_optional_manual_override_surface():
    html = read("review.html")
    js = read("review.js")
    assert "RAW DOCLING READING ORDER" in html
    assert "Save manual override" in html
    assert "Original text is correct" in html
    assert "/corrections/${encodeURIComponent(entryId)}" in js

def test_cloud_workflow_exposes_quota_pause_without_requiring_human_gate():
    html = read("book.html")
    js = read("book.js")
    verification_html = read("verification.html")
    verification_js = read("verification.js")
    assert 'id="book-quota-alert"' in html
    assert 'id="cloud-quota-alert"' in verification_html
    assert "Cloud quota paused" in js
    assert "Review is optional" in js
    assert "Groq requests are paused before the configured safety reserve" in verification_js
    assert "queued Groq routes remain pending" in verification_js


def test_dashboard_surfaces_cloud_quota_warning_and_dynamic_text_provider():
    html = read("index.html")
    js = read("dashboard.js")
    assert 'id="dashboard-cloud-quota"' in html
    assert '/api/stage2b/status' in js
    assert 'Groq API use paused before the configured free-tier reserve.' in js
    assert 'queued Groq routes are preserved' in js
    assert 'doc.text_verifier_label || "Text"' in js


def test_verification_exposes_explicit_text_and_vision_provider_selectors_without_fallback():
    html = read("verification.html")
    js = read("verification.js")
    assert 'id="text-provider-select"' in html
    assert 'id="vision-provider-select"' in html
    assert html.count('<option value="pi5">Pi5 Vision</option>') == 2
    assert html.count('<option value="oneplus">OnePlus Vision</option>') == 2
    assert html.count('<option value="groq">Groq Vision</option>') == 2
    assert html.lower().count("no automatic fallback") >= 2
    assert '/api/stage2b/providers/${kind}' in js
    assert 'JSON.stringify({provider})' in js
    assert "automatic fallback" in js.lower()


def test_verification_exposes_persistent_groq_usage_audit_table():
    html = read("verification.html")
    js = read("verification.js")
    assert 'id="groq-usage-panel"' in html
    assert 'id="groq-calls"' in html
    assert 'id="groq-input"' in html
    assert 'id="groq-output"' in html
    assert 'id="groq-kinds"' in html
    assert 'id="groq-models"' in html
    assert 'id="groq-usage-rows"' in html
    assert "Request ID / error" in html
    assert "/api/groq/usage?limit=50" in js
    assert "estimated_paid_equivalent_cost_usd" in js
    assert "item.request_id" in js
    assert "item.error_code" in js


def test_review_auto_applied_correction_needs_no_save_click():
    html = read("review.html")
    js = read("review.js")
    assert "Automatic source-image corrections are applied without a Save click" in html
    assert "Already applied automatically to the Stage 2C overlay. No manual Save is required." in js
    assert "status === 'applied' && !humanVerified" in js


def test_vision_verifier_audit_page_is_read_only_and_evidence_first():
    html = read("vision-audit.html")
    js = read("vision-audit.js")
    assert "Vision Verifier Audit" in html
    assert "Read-only" in html
    assert "No classification is performed on this page" in html
    assert "/api/stage2b/vision-audit?limit=5000" in js
    assert "/vision-image?region=full" not in js  # URLs come from the audit API, not guessed client-side
    assert "Exact full image sent" in js
    assert "Why Stage 2A sent it" in js
    assert "Vision decision" in js
    assert "What the pipeline did" in js
    assert "Exact full-image prompt" in js
    assert "Raw crop response" in js
    assert "/rerun" not in js
    assert "retryVerificationJob" not in js


def test_primary_pages_link_to_vision_verifier_audit():
    for name in ["index.html", "convert.html", "quality.html", "verification.html", "errors.html", "book.html", "oneplus.html", "workflow.html"]:
        assert 'href="/vision-audit"' in read(name), name
