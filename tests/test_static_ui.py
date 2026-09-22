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
    assert "<h1>Verify uncertain content</h1>" in verification
    assert "Verify book" in js
    assert "Auto verify all" in verification
    assert "/api/stage2b/results/pi5" in js
    assert "/api/stage2b/results/oneplus" in js
    assert "Stop verifier" in verification
    assert "Remaining text work" not in verification
    assert "Remaining vision work" not in verification
    assert "slice(0, 40)" not in js

    assert "Artifact sweep" in verification
    assert "artifact_pending" in js
    assert "Text ${text.pending}" in js
    assert "Vision ${vision.pending}" in js
    assert "Artifact ${artifact.pending}" in js


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


def test_oneplus_page_is_llama_only_and_keeps_nonbusy_disabled_cursor():
    html = read("oneplus.html")
    js = read("oneplus.js")
    css = read("styles.css")
    assert "Install / update llama control script" in html
    assert 'id="workload-state"' in html
    assert 'id="workload-detail"' in html
    assert 'cooldown_remaining_seconds' in js
    assert '>Start<' in html
    assert '>Restart<' in html
    assert '>Stop<' in html
    assert "/api/oneplus-control/install-script" in js
    assert "/api/oneplus-control/models" not in js
    assert "/api/oneplus-control/logs" not in js
    assert "/api/oneplus-control/capture" not in js
    assert "/api/oneplus-control/charge/" not in js
    assert "charging control" not in html.lower()
    assert 'id="reconnect-ssh"' not in html
    assert 'id="stop-ssh"' not in html
    assert "model-path" not in html
    assert "mmproj-path" not in html
    assert "cursor: wait" not in css
    assert "cursor: not-allowed" in css


def test_oneplus_script_control_uses_canonical_layout_classes():
    html = Path("app/static/oneplus.html").read_text(encoding="utf-8")
    assert 'class="page-header"' in html
    assert 'class="panel oneplus-status-card"' in html
    assert 'class="oneplus-server-actions"' in html
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


def test_book_workflow_uses_automatic_stage2c_and_explicit_audit_gate():
    html = read("book.html")
    js = read("book.js")
    assert "Hybrid" in html
    assert "Automatic corrections & enrichment" in js
    assert "Verifier Audit remains a human gate" in js
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
    assert "/text-audit?job=${encodeURIComponent(job.id)}" in js
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


def test_book_stage3_completed_state_uses_pipeline_freshness_and_machine_rag_stage():
    html = read("book.html")
    js = read("book.js")
    assert "pipeline.stage3_ready === true" in js
    assert "pipeline.machine_embedding_ready === true" in js
    assert "Download chunks" in js
    assert "Rebuild chunks" in js
    assert "Machine embeddings & RAG" in js
    assert "Machine RAG" in html


def test_review_keeps_optional_manual_override_surface():
    html = read("review.html")
    js = read("review.js")
    assert "RAW DOCLING READING ORDER" in html
    assert "Save manual override" in html
    assert "Original text is correct" in html
    assert "/corrections/${encodeURIComponent(entryId)}" in js

def test_cloud_workflow_exposes_quota_pause_independently_from_audit_gate():
    html = read("book.html")
    js = read("book.js")
    verification_html = read("verification.html")
    verification_js = read("verification.js")
    assert 'id="book-quota-alert"' in html
    assert 'id="cloud-quota-alert"' in verification_html
    assert "Cloud quota paused" in js
    assert "Verifier Audit remains a human gate" in js
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
    assert "Verifier audit" in html
    assert "Verifier audit" in html
    assert "Human audit gate" in html
    assert "Technical / Decorative" in html
    assert "Bypass selected book audit for testing" in html
    assert "/api/stage2b/vision-audit?limit=5000" in js
    assert "/vision-image?region=full" not in js  # URLs come from the audit API, not guessed client-side
    assert "Exact full image sent" in js
    assert "Why Stage 2A sent it" in js
    assert "Vision decision" in js
    assert "What the pipeline did" in js
    assert "Exact full-image prompt" in js
    assert "Raw crop response" in js
    assert "Recover evidence" in js
    assert "Evidence recovery" in html
    assert "human_evidence_recovery_required" in js
    assert "/rerun" not in js
    assert "retryVerificationJob" not in js


def test_primary_pages_link_to_unified_verifier_audit():
    for name in ["index.html", "convert.html", "quality.html", "verification.html", "errors.html", "book.html", "oneplus.html", "workflow.html"]:
        assert 'href="/text-audit"' in read(name), name


def test_verifier_audit_is_unified_and_text_audit_is_read_only():
    text_html = read("text-audit.html")
    text_js = read("text-audit.js")
    vision_html = read("vision-audit.html")
    assert "Verifier audit" in text_html
    assert 'href="/text-audit"' in text_html and 'href="/vision-audit"' in text_html
    assert "Exact target crop sent" in text_js
    assert "/api/stage2b/text-audit?limit=5000" in text_js
    assert "/api/stage2b/jobs/${job.id}/result" in text_js
    assert "Verifier audit" in vision_html
    assert 'href="/text-audit"' in vision_html and 'href="/vision-audit"' in vision_html


def test_verification_has_one_click_retry_all_failed():
    html = read("verification.html")
    js = read("verification.js")
    assert 'id="retry-all-failed"' in html
    assert "Retry all failed" in html
    assert "/api/stage2b/retry-all-failed" in js
    assert "successful and pending" not in js.lower()  # copy stays concise; backend owns safety contract


def test_sidebar_version_is_prominent_and_server_checked():
    js = read("nav.js")
    css = read("styles.css")
    assert "Version ${version}" in js
    assert "/api/version" in js
    assert "UI and server match" in js
    assert ".ui29 .app-version" in css


def test_primary_workspace_pages_link_to_rag_quality():
    for name in ["workflow.html", "index.html", "convert.html", "verification.html", "text-audit.html", "vision-audit.html", "oneplus.html", "book.html"]:
        assert 'href="/retrieval"' in read(name), name


def test_rag_quality_page_keeps_search_first_and_adds_explicit_grounded_generation():
    html = read("retrieval.html")
    js = read("retrieval.js")
    assert "Machine RAG" in html
    assert "Generate only from the evidence above" in html
    assert 'id="prepare-index"' in html
    assert 'id="retrieval-query"' in html
    assert "Refresh Stage 3 text indexes" in html
    assert 'id="answer-provider"' in html
    assert '<option value="pi5">Pi5 · local</option>' in html
    assert '<option value="oneplus">OnePlus · local</option>' in html
    assert '<option value="groq">Groq · cloud</option>' in html
    assert 'id="generate-answer"' in html
    assert 'id="copy-external-prompt"' in html
    assert "No automatic fallback" in html
    assert "/api/retrieval/search" in js
    assert "/api/retrieval/generate" in js
    assert "/api/retrieval/prompt-bundle" in js
    assert "/api/retrieval/reindex-all" in js
    assert "/api/retrieval/benchmark/run" in js
    assert "/api/retrieval/follow-reference" in js
    assert "/api/postprocess/jobs/${encodeURIComponent(row.postprocess_job_id)}/source-page/" in js
    assert "Follow reference" in js
    assert "+ Page" in js
    assert 'id="source-page-modal"' in html
    assert "Use as expected" in js


def test_artifact_audit_page_exposes_inventory_and_queue_actions():
    html = read("artifact-audit.html")
    js = read("artifact-audit.js")
    assert "<h1>Artifact audit</h1>" in html
    assert "Verify all technical artifacts" in html
    assert "Retry failed artifact queue" in html
    assert "/api/stage2b/artifact-audit?" in js
    assert "/api/stage2b/artifact-audit/start-all" in js
    assert "/api/stage2b/artifact-audit/retry-failed" in js
    assert "Technical candidates only" in html


def test_retrieval_ui_exposes_equipment_scoped_multi_manual_workflow():
    html = read("retrieval.html")
    js = read("retrieval.js")
    assert 'id="retrieval-scope"' in html
    assert 'id="equipment-form"' in html
    assert 'id="equipment-manual-list"' in html
    assert "Machines & manuals" in html
    assert "Hybrid embeddings are built" in html
    assert "equipment_id" in js
    assert "/api/retrieval/equipment" in js
    assert "All books · diagnostic" not in html
    assert 'value="" selected disabled>Choose a machine or manual…' in html


def test_retrieval_scope_requires_explicit_choice_except_job_deep_link():
    js = read("retrieval.js")
    assert "else if ((data.equipment || []).length)" not in js
    assert "select.value = `equipment:${data.equipment[0].equipment_id}`" not in js
    assert "else select.value = '';" in js
    assert "Choose a machine or manual" in js
    assert "No unrelated machine is included" in js



def test_ready_workflow_keeps_open_link_and_adds_book_scoped_rag_link():
    js = read("workflow.js")
    assert "ragReady:true" in js
    assert 'href="/book?job=${encodeURIComponent(book.id)}">Open</a>' in js
    assert 'href="/retrieval?job=${encodeURIComponent(book.id)}">Test RAG</a>' in js
    assert "href:'/retrieval'" not in js


def test_retrieval_reads_job_query_param_once_and_preselects_book_scope():
    js = read("retrieval.js")
    assert "new URLSearchParams(window.location.search).get('job')" in js
    assert "if (requestedJobId)" in js
    assert "requestedValue = owner ? `equipment:${owner.equipment_id}` : `book:${requestedJobId}`" in js
    assert "select.value = requestedValue" in js
    assert "requestedJobId = null;" in js


def test_source_page_dialog_moves_restores_and_traps_keyboard_focus():
    js = read("retrieval.js")
    assert "sourcePageReturnFocus = trigger || document.activeElement" in js
    assert "requestAnimationFrame(() => $('source-page-close').focus())" in js
    assert "document.contains(returnFocus)" in js
    assert "returnFocus.focus()" in js
    assert "function trapSourcePageFocus(event)" in js
    assert "event.key !== 'Tab'" in js


def test_mobile_nav_moves_focus_traps_tab_and_restores_trigger():
    js = read("nav.js")
    assert "function focusableItems()" in js
    assert "requestAnimationFrame(function ()" in js
    assert "if (target) target.focus()" in js
    assert 'if (event.key !== "Tab") return;' in js
    assert "last.focus()" in js
    assert "first.focus()" in js
    assert "target.focus();" in js


def test_missing_book_id_feedback_has_direct_return_link():
    js = read("book.js")
    assert "Missing book id." in js
    assert '<a href="/">Return to My books</a>' in js


def test_every_static_page_has_skip_link_and_main_target():
    pages = list(STATIC.glob("*.html"))
    assert pages
    for page in pages:
        html = page.read_text(encoding="utf-8")
        assert '<a class="skip-link" href="#main-content">Skip to main content</a>' in html, page.name
        assert 'id="main-content"' in html, page.name
    css = read("styles.css")
    assert ".skip-link" in css
    assert ".skip-link:focus" in css


def test_retrieval_hybrid_readiness_is_scope_specific_and_proactive():
    html = read("retrieval.html")
    js = read("retrieval.js")
    assert "Hybrid · loading model…" in html
    assert "function selectedScopeStatus()" in js
    assert "function updateScopeControls" in js
    assert "!state.hybridReady" in js
    assert "Switched to Lexical only" in js
    assert "Build machine embeddings" in js
    assert "hybridOption.disabled = !globallyHybrid || !state || !state.hybridAllowed || !state.hybridReady" in js


def test_retrieval_ui_has_no_all_books_scope_or_fallback():
    html = read("retrieval.html")
    js = read("retrieval.js")
    assert "All books · diagnostic" not in html
    assert "All books · diagnostic" not in js
    assert "Diagnostic all-books mode" not in js
    assert "value=\"all\"" not in html
    assert "select.value = 'all'" not in js


def test_chunk_viewer_exposes_stage3_text_and_source_page_workspace():
    html = read("chunks.html")
    js = read("chunks.js")
    assert "Chunk Viewer" in html
    assert 'id="chunk-scope"' in html
    assert 'id="chunk-query"' in html
    assert 'id="chunk-page-filter"' in html
    assert 'id="chunk-full-text"' in html
    assert 'id="chunk-page-image"' in html
    assert "/api/chunks?" in js
    assert "/api/chunks/${encodeURIComponent(jobId)}/${encodeURIComponent(chunkId)}" in js
    assert "/source-page/" in js
    assert "The viewer never searches unrelated machines together" in html


def test_navigation_injects_chunk_viewer_and_page_guidance():
    js = read("nav.js")
    css = read("styles.css")
    assert "Chunk Viewer" in js
    assert "workspace-guide" in js
    assert "Machine-scoped retrieval" in js
    assert ".workspace-guide" in css


def test_machine_manager_explains_revision_authority_and_keeps_hybrid_machine_scoped():
    html = read("retrieval.html")
    js = read("retrieval.js")
    assert "Historical and Draft revisions stay available for audit but are excluded from machine retrieval" in html
    assert "Manual revision" in js
    assert "Manual authority" in js
    assert ">Current</option>" in js
    assert ">Historical</option>" in js
    assert "Draft / not in RAG" in js
    assert "active_manual_count" in js
    assert "Build machine embeddings" in js


def test_retrieval_ui_exposes_fresh_machine_hybrid_benchmark_and_intent_audit():
    html = read("retrieval.html")
    js = read("retrieval.js")
    assert 'id="run-hybrid-benchmark"' in html
    assert "Run fresh machine hybrid" in html
    assert "/api/retrieval/benchmark/run-hybrid" in js
    assert "equipment_id: scopeRequest().equipment_id" in js
    assert "semantic_intent" in js
    assert "intent ${String(row.semantic_intent)" in js


def test_removed_sidebar_failure_badge_cannot_break_pages():
    """Optional/removed nav badges must never abort primary page rendering."""
    for name in ["dashboard.js", "convert.js", "errors.js"]:
        js = read(name)
        assert '$("#failed-nav").textContent' not in js, name
        assert 'document.getElementById("failed-nav")' in js, name


def test_dashboard_recent_documents_renderer_is_defensive():
    js = read("dashboard.js")
    assert 'const body = $("#jobs-body");' in js
    assert 'if (!body) return;' in js
    assert 'renderJobs(data.jobs);' in js


def test_book_workflow_always_exposes_audit_testing_bypass_control():
    js = read("book.js")
    assert "Bypass audit for testing" in js
    assert "Remove audit bypass" in js
    assert "/api/postprocess/jobs/${jobId}/verifier-audit/bypass" in js
    assert "window.confirm" in js
    assert "They are NOT accepted" in js
    assert "always visible at the top of this Book workflow page" in js


def test_book_workflow_exposes_safe_delete_book_control():
    html = read("book.html")
    js = read("book.js")
    assert 'id="delete-book-button"' in html
    assert "Delete book" in html
    assert "/api/postprocess/jobs/${jobId}/delete" in js
    assert "_deleted_books" in js
    assert "active database history will be deleted" in js
    html = read("book.html")
    assert 'id="book-audit-bypass-panel"' in html
    assert 'id="book-audit-bypass-button"' in html
    assert "Bypass audit for testing" in html
