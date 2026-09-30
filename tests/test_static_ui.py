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
    assert "Text · Vision · Artifact" in verification
    assert 'id="interlock-start"' in verification
    assert 'id="interlock-stop"' in verification
    assert 'id="interlock-auto"' in verification
    assert "/api/stage2b/results/pi5" in js
    assert "/api/stage2b/results/oneplus" in js
    assert "shared Text · Vision · Artifact interlock" in verification
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
    assert 'id="interlock-start"' in html
    assert 'id="interlock-stop"' in html
    assert 'id="interlock-auto"' in html
    assert 'id="artifact-mode"' in html
    assert 'id="artifact-stage"' in html
    assert "device-controls-disclosure" not in html
    assert "Alive" in js and "Offline" in js
    assert "/api/stage2b/interlock/${mode}" in js
    assert "window.setInterlockMode = setInterlockMode" in js
    assert "Normal Text/Vision work" in html
    assert js.count("pollVerification();") == 1


def test_stage2b_ui_uses_inline_feedback_not_alerts():
    js = read("verification.js")
    assert "feedback(" in js
    assert "alert(" not in js


def test_all_primary_pages_link_to_verification():
    for name in ["index.html", "convert.html", "quality.html", "errors.html"]:
        assert 'href="/verification"' in read(name), name


def test_verification_polling_never_overlaps_and_backs_off_when_hidden():
    js = read("verification.js")
    assert "refreshInFlight" in js
    assert "setInterval(" not in js
    assert 'document.visibilityState === "visible" ? 5000 : 15000' in js
    assert 'document.addEventListener("visibilitychange"' in js
    assert "window.clearTimeout(refreshTimer)" in js


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


def test_queue_page_is_single_source_and_add_book_is_discoverable():
    html = read("index.html")
    workflow = read("workflow.html")
    js = read("dashboard.js")
    nav = read("nav.js")
    assert "Advanced document table" not in html
    assert 'id="documents-body"' not in html
    assert "/api/documents" not in js
    assert 'href="/add-book"' in workflow
    assert 'href = "/add-book"' in nav or 'href="/add-book"' in nav or "'/add-book'" in nav
    assert "Build Stage 2C" not in js
    assert "Build chunks" not in js


def test_quality_page_is_diagnostic_and_links_back_to_book_workflow():
    js = read("quality.js")
    assert 'href="/book?job=${job.id}"' in js
    assert "/api/stage2c/books/${id}/build" not in js
    assert "/api/stage3/books/${id}/build" not in js


def test_book_workflow_uses_automatic_stage2c_and_contextual_audit_gate():
    html = read("book.html")
    js = read("book.js")
    assert "Hybrid" in html
    assert "Correction finalization · Stage 2C" in js
    assert "Audit is blocking Stage 3" in js
    assert "Keep original if unreadable" in js
    assert "Build Hybrid chunks" in js
    assert "/api/postprocess/jobs/${jobId}/human-review" in js
    assert "stage2c_auto_finalize" in js
    assert 'id="book-audit-bypass-panel"' not in html
    assert "showBypass = stage2bDone" in js


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


def test_review_page_uses_shared_shell_queue_and_docling_context():
    html = read("review.html")
    js = read("review.js")
    css = read("review.css")
    assert 'class="app-shell"' in html
    assert '/assets/nav.js?' in html
    assert "RAW DOCLING SOURCE" in html
    assert 'id="queue-progress"' in html
    assert 'id="queue-prev"' in html and 'id="queue-next"' in html
    assert "Table row and header context" in js
    assert "/docling-context" in js
    assert '$(' + '"correction"' + ').value = rawTarget' in js
    assert "diffTokens" in js
    assert "120000" in js
    assert 'save("apply")' in js
    assert "/corrections/${encodeURIComponent(entryId)}" in js
    assert ".docling-neighbor" in css
    assert ".diff-removed" in css and ".diff-added" in css
    assert 'id="back-workflow"' in html
    assert '/book?job=${encodeURIComponent(job)}' in js
    assert "Keep original" in html
    assert "Not Pi5" not in html


def test_book_stage3_completed_state_uses_pipeline_freshness_and_machine_rag_stage():
    html = read("book.html")
    js = read("book.js")
    assert "pipeline.stage3_ready === true" in js
    assert "pipeline.machine_embedding_ready === true" in js
    assert "Download chunks" in js
    assert "Rebuild chunks" in js
    assert "Retrieval-Augmented Generation (RAG)" in js
    assert "<strong>RAG</strong>" in html


def test_review_keeps_human_override_surface_and_auto_advance():
    html = read("review.html")
    js = read("review.js")
    assert "HUMAN DECISION" in html
    assert "Apply human correction" in html
    assert "Keep original" in html
    assert "/corrections/${encodeURIComponent(entryId)}" in js
    assert "Opening the next review item" in js
    assert "Ctrl+Enter" in html

def test_cloud_workflow_exposes_quota_pause_independently_from_audit_gate():
    html = read("book.html")
    js = read("book.js")
    verification_html = read("verification.html")
    verification_js = read("verification.js")
    assert 'id="book-quota-alert"' in html
    assert 'id="cloud-quota-alert"' in verification_html
    assert "Cloud quota paused" in js
    assert "Audit is blocking Stage 3" in js
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


def test_verification_exposes_explicit_provider_pool_and_moves_physical_worker_settings_off_page():
    html = read("verification.html")
    js = read("verification.js")
    assert 'id="text-provider-select"' in html
    assert 'id="vision-provider-select"' in html
    assert html.count('<option value="pi5">Pi5 Vision</option>') == 2
    assert html.count('<option value="oneplus">OnePlus Vision</option>') == 2
    assert html.count('<option value="groq">Groq Vision</option>') == 2
    assert html.count('<option value="colab">Colab worker pool</option>') == 2
    assert 'href="/workers"' in html
    assert 'href="/review-workers"' in html
    assert 'id="colab-url"' not in html
    assert 'id="colab-api-key"' not in html
    assert html.lower().count("no automatic fallback") >= 2
    assert '/api/stage2b/providers/${kind}' in js
    assert 'JSON.stringify({provider})' in js
    assert "automatic fallback" in js.lower()


def test_workers_page_supports_individual_stop_artifact_participation_and_multiple_colab_workers():
    html = read("workers.html")
    js = read("workers.js")
    assert "Stop/resume each physical worker independently" in html
    assert "Add Colab worker" in html
    assert "Participate in Artifact sweep" in js
    assert "Stop after current job" in js
    assert "/api/workers/local/${local}" in js
    assert "/api/workers/colab" in js
    assert "/api/workers/colab/${wid}/test" in js
    assert "Remove" in js


def test_review_worker_page_is_deferred_until_primary_machine_work_is_complete_and_human_remains_authoritative():
    html = read("review-workers.html")
    js = read("review-workers.js")
    text_review = read("review.html")
    vision_review = read("vision-audit.js")
    assert "All Text + Vision + Artifact machine jobs across the library must finish first" in html
    assert "They never set" in html and "human_verified" in html
    assert "Text review" in js and "Vision review" in js
    assert "/api/review-workers/settings" in js
    assert "/api/review-workers/status" in js
    assert 'id="ai-review-assistant-card"' in text_review
    assert "AI REVIEW ASSISTANT · COLAB" in text_review
    assert "aiReviewAssistantBlock" in vision_review
    assert "Human authority preserved" in vision_review


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
    js = read("review.js")
    assert "Already applied automatically to the Stage 2C overlay. No manual Save is required." in js
    assert "status === 'applied' && !humanVerified" in js


def test_vision_verifier_audit_page_is_read_only_and_evidence_first():
    html = read("vision-audit.html")
    js = read("vision-audit.js")
    assert "Verifier audit" in html
    assert "Verifier audit" in html
    assert "Human audit gate" in html
    assert "Technical / Decorative" in html
    assert "Bypass selected book audit for testing" not in html
    assert "Show extracted detail" in js
    assert "/api/stage2b/vision-audit?limit=5000" in js
    assert "/vision-image?region=full" not in js  # URLs come from the audit API, not guessed client-side
    assert "Exact full image sent" in js
    assert "Why it was checked" in js
    assert "Pipeline" in js
    assert "Result" in js
    assert "Exact full-image prompt" in js
    assert "Raw crop response" in js
    assert "Recover evidence" in js
    assert "Use existing evidence" in js
    assert "Undo human decision" in js
    assert "waive-recovery" in js
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
    assert "/api/stage2b/text-audit?${params}" in text_js
    assert "limit: String(PAGE_SIZE)" in text_js
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
    assert "Retrieval-Augmented Generation (RAG)" in html
    assert "Generate only from the evidence above" in html
    assert 'id="prepare-index"' in html
    assert 'id="retrieval-query"' in html
    assert "Refresh Stage 3 text indexes" in html
    assert 'id="answer-provider"' in html
    assert '<option value="pi5">Pi5 · local</option>' in html
    assert '<option value="oneplus">OnePlus · local</option>' in html
    assert '<option value="groq">Groq · cloud</option>' in html
    assert 'id="generate-answer"' in html
    assert 'id="cancel-answer"' in html
    assert 'id="generation-progress"' in html
    assert 'id="copy-external-prompt"' in html
    assert "No automatic fallback" in html
    assert "/api/retrieval/search" in js
    assert "/api/retrieval/generate/start" in js
    assert "/api/retrieval/generate/cancel/" in js
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
    assert "Retrieval-Augmented Generation (RAG)" in js
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


def test_book_workflow_places_testing_bypass_contextually_in_stage2c():
    html = read("book.html")
    js = read("book.js")
    assert "Bypass audit for testing" in js
    assert "Remove testing bypass" in js
    assert 'id="book-audit-bypass-panel"' not in html
    assert "showBypass = stage2bDone" in js
    assert "Audit is blocking Stage 3" in js


def test_book_workflow_exposes_safe_delete_book_control():
    html = read("book.html")
    js = read("book.js")
    assert 'id="delete-book-button"' in html
    assert "Delete book" in html
    assert "/api/postprocess/jobs/${jobId}/delete" in js
    assert "_deleted_books" in js
    assert "active database history will be deleted" in js
    html = read("book.html")
    assert 'id="book-audit-bypass-panel"' not in html
    assert 'id="book-audit-bypass-button"' not in html
    assert "Bypass audit for testing" in js


def test_managed_add_book_ui_registers_normal_pipeline_job():
    html = read("add-book.html")
    js = read("add-book.js")
    workflow = read("workflow.html")
    assert "Add a book" in html
    assert "/api/books/add/file" in js
    assert "/api/books/add/url" in js
    assert "normal conversion pipeline" in html
    assert 'href="/add-book"' in workflow
    assert "one-off ZIP only" in read("convert.js")


def test_oneplus_page_shows_verifier_circuit_health_and_confirms_server_stop_restart():
    html = read("oneplus.html")
    js = read("oneplus.js")
    assert "Vision verifier · OnePlus" in html
    assert "/api/stage2b/status" in js
    assert "endpoint_circuit" in js
    assert "window.confirm" in js


def test_destructive_or_expensive_ui_actions_are_confirmed():
    assert "window.confirm" in read("quality.js")
    assert "window.confirm" in read("dashboard.js")
    assert "window.confirm" in read("artifact-audit.js")
    assert "Delete machine" in read("retrieval.js")



def test_4011b_review_queue_is_global_filterable_and_audits_are_one_by_one():
    review_html = read("review.html")
    review_js = read("review.js")
    text_js = read("text-audit.js")
    vision_js = read("vision-audit.js")
    artifact_js = read("artifact-audit.js")

    for control in ["review-filter-book", "review-filter-type", "review-filter-reason", "review-filter-state"]:
        assert f'id="{control}"' in review_html
    assert "/api/postprocess/human-review?${params}" in review_js
    assert "Technical reason code" in review_html
    assert 'const PAGE_SIZE = 1;' in text_js
    assert 'const PAGE_SIZE = 1;' in vision_js
    assert 'const PAGE_SIZE = 1;' in artifact_js
    assert "vision-audit-evidence-counts" in vision_js
    assert "Show extracted detail" in artifact_js
    assert "decisionInFlight" in vision_js  # decision request locks navigation and sibling buttons
    assert "previousPosition" not in vision_js  # late responses must not snap the reviewer backward
    assert 'value="human_review">Human review</option>' in review_html or "needs_review" in review_html
    assert 'value="human_review">Human review</option>' in read("text-audit.html")
    assert 'value="HUMAN_REVIEW">Human review</option>' in read("vision-audit.html")
    assert 'id="aa-decision"' in read("artifact-audit.html")
    assert 'value="human_review">Human review</option>' in read("artifact-audit.html")
    assert "needsHumanReview(job)" in vision_js
    assert "downstream.current_authoritative" in vision_js
    assert "visualSubjectKey(job)" in vision_js
    assert "artifactNeedsHumanReview(job)" in artifact_js
    assert "downstream.current_authoritative" in artifact_js
    assert ".artifact-decision" in artifact_js
    assert 'event.altKey && event.key === "ArrowRight"' in text_js
    assert 'event.altKey && event.key === "ArrowRight"' in vision_js
    assert 'event.altKey && event.key === "ArrowRight"' in artifact_js
    assert "text-audit-decision" in text_js
    assert "Accept correction" in text_js
    assert "Keep original" in text_js
    assert "/corrections/${encodeURIComponent(entryId)}" in text_js
    assert "await loadAudit()" in text_js  # inline text decisions advance the filtered queue


def test_ah3_human_review_exposes_ai_reviewer_filters_without_granting_ai_authority():
    review_html = read("review.html")
    review_js = read("review.js")
    vision_html = read("vision-audit.html")
    vision_js = read("vision-audit.js")

    for control in [
        "review-filter-ai",
        "review-filter-recommendation",
        "review-filter-worker",
        "review-filter-attention",
    ]:
        assert f'id="{control}"' in review_html
    assert "Needs my attention" in review_html
    assert "Verifier ↔ reviewer disagreement" in review_html
    assert 'params.set("ai_review", aiReview)' in review_js
    assert 'params.set("recommendation", recommendation)' in review_js
    assert 'params.set("review_worker", worker)' in review_js
    assert 'params.set("attention", attention)' in review_js
    assert "filter_attention" in review_js
    assert "AI reviewed" in review_js
    assert "Disagreements" in review_js
    assert "human_verified" not in review_js[review_js.index("function fillReviewFilters"):review_js.index("async function loadQueue")]

    for control in ["va-ai-review", "va-ai-recommendation", "va-review-worker", "va-attention"]:
        assert f'id="{control}"' in vision_html
    assert "visionReviewerDisagreement" in vision_js
    assert "visionAiNeedsAttention" in vision_js
    assert 'verdict === "UNCERTAIN"' not in vision_js[vision_js.index("function visionReviewerDisagreement"):vision_js.index("function visionAiNeedsAttention")]
    assert "/api/review-workers/settings" in vision_js
    assert "human_visual_decision" in vision_js


def test_ah4_anomaly_review_is_separate_advisory_layer_with_one_click_rereview():
    review_html = read("review.html")
    review_js = read("review.js")
    vision_js = read("vision-audit.js")
    workers_html = read("review-workers.html")
    workers_js = read("review-workers.js")

    assert 'id="anomaly-review-card"' in review_html
    assert 'id="anomaly-rereview"' in review_html
    assert "Re-review with Colab" in review_html
    assert "/anomaly-review" in review_js
    assert "Use anomaly correction" in review_html
    assert "human decision remains authoritative" in review_js.lower()

    assert "anomalyReviewBlock" in vision_js
    assert "audit-anomaly-review" in vision_js
    assert "/anomaly-review" in vision_js
    assert "never replaces an existing human visual decision automatically" in vision_js

    assert "Anomaly review" in workers_js
    assert 'data-role="anomaly"' in workers_js
    assert 'id="review-anomaly-pending"' in workers_html
    assert "textRemaining" in workers_js
    assert "visionRemaining" in workers_js
    assert "attempt_count" in workers_js
    assert "error_message" in workers_js


def test_4011c_shared_attention_terminology_and_polling_feedback_are_consistent():
    nav = read("nav.js")
    verification = read("verification.js")
    dashboard = read("dashboard.js")
    review = read("review.js")
    assert "global-attention-strip" in nav
    assert "/api/errors" in nav
    assert "Needs attention" in nav
    assert "Retrieval-Augmented Generation (RAG)" in nav
    assert "Text verifier ·" in verification
    assert "Vision verifier ·" in verification
    assert "Queue refresh failed" in dashboard
    assert "filtersLoaded ?" in review


def test_queue_exposes_safe_delete_for_terminal_documents():
    js = read("dashboard.js")
    assert 'class="mini-action danger-action queue-delete"' in js
    assert '/api/jobs/${conversionId}/delete' in js
    assert '/api/postprocess/jobs/${stage2Id}/delete' in js
    assert 'FileMissing after a manual rename' in js
    assert 'window.confirm(message)' in js


def test_review_repairs_missing_ledger_entry_and_never_exposes_blank_editor():
    review = read("review.js")
    verification = read("verification.js")
    text_audit = read("text-audit.js")

    assert "/corrections/${encodeURIComponent(entryId)}/repair" in review
    assert "repairMissingEntry" in review
    assert "setReviewUnavailable" in review
    assert '$("correction").disabled = true' in review
    assert '$("save").disabled = true' in review
    assert "Preparing the review record from the completed verifier result" in review

    assert "review_entry_ready" in verification
    assert "Preparing review…" in verification
    assert "Repair / review" in verification

    assert "Review record pending publication; verifier result preserved" in text_audit
    assert "Repair / inspect context" in text_audit


def test_structural_review_pages_are_evidence_first_and_use_full_width_workspace():
    book_js = read("book.js")
    reading_html = read("reading-order-review.html")
    reading_js = read("reading-order-review.js")
    table_html = read("table-repair.html")
    table_js = read("table-repair.js")
    structural_html = read("structural-review.html")
    structural_js = read("structural-review.js")
    workflow_css = read("workflow.css")

    assert "READING_ORDER_ANOMALY" in book_js
    assert "/reading-order-review?job=${jobId}&route=" in book_js
    assert "/structural-review?job=${jobId}&route=" in book_js
    assert "Review finding" in book_js
    assert 'data-structural-decision="accepted"' not in book_js
    assert 'data-structural-decision="dismissed"' not in book_js

    for html in (reading_html, table_html, structural_html):
        assert 'app-shell review-workspace-shell' in html
        assert 'main-content review-workspace-main' in html
        assert 'class="stage-card"' not in html
    assert ".ui29 .app-shell.review-workspace-shell" in workflow_css
    assert ".review-workspace-card" in workflow_css

    assert "Mark this page reviewed" in reading_html
    assert "Accept current Docling order" in reading_html
    assert "Dismiss as false positive" in reading_html
    assert "leave this route unresolved" in reading_html
    assert "/reading-order-review/${encodeURIComponent(routeId)}" in reading_js
    assert "reviewed_pages" in reading_js
    assert "source_page_url" in reading_js

    assert "Mark source page reviewed" in table_html
    assert "source_reviewed: true" in table_js
    assert "reviewed_items:['table-source']" in table_js

    assert "Mark this evidence reviewed" in structural_html
    assert "Leave unresolved" in structural_html
    assert "reviewed_items" in structural_js
    assert "/structural-review/${encodeURIComponent(routeId)}" in structural_js


def test_docling_page_review_ui_exposes_bbox_repair_and_table_header_controls():
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (root / "docling-review.html").read_text(encoding="utf-8")
    js = (root / "docling-review.js").read_text(encoding="utf-8")
    assert "Draw missing region" in html
    assert "Coverage view" in html
    assert 'id="header-rows"' in html
    assert "Re-extract selected bbox" in html
    assert "/docling-review/reextract" in js
    assert "/docling-review/repairs" in js
    assert "header_rows" in js
    assert "raw Docling ZIP will remain unchanged" in js


def test_review_pages_link_directly_to_matching_docling_bbox():
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    text_js = (root / "text-audit.js").read_text(encoding="utf-8")
    review_html = (root / "review.html").read_text(encoding="utf-8")
    review_js = (root / "review.js").read_text(encoding="utf-8")
    artifact_js = (root / "artifact-audit.js").read_text(encoding="utf-8")
    vision_js = (root / "vision-audit.js").read_text(encoding="utf-8")
    docling_js = (root / "docling-review.js").read_text(encoding="utf-8")

    assert "Open Docling PDF bbox" in text_js
    assert "#/texts/${textIndex}" in text_js
    assert "#/tables/${tableIndex}" in text_js
    assert 'id="open-docling-bbox"' in review_html
    assert "setDoclingReviewLink" in review_js
    assert "#/texts/${sourceIndex}" in review_js
    assert "#/tables/${tableIndex}" in review_js
    assert "Open Docling PDF bbox" in artifact_js
    assert "#/pictures/${job.picture_index}" in artifact_js
    assert "Open Docling PDF bbox" in vision_js
    assert "#/pictures/${index}" in vision_js
    assert "q.get('ref')" in docling_js
    assert "Opened ${wantedRef} from the review page." in docling_js
    assert "focusSelectedBox" in docling_js
    assert "Back to review" in docling_js


def test_verification_results_show_execution_provider_and_truthful_legacy_timing():
    html = read("verification.html")
    js = read("verification.js")
    assert "<th>Provider</th>" in html
    assert "job.execution_provider" in js
    assert "Legacy / unknown" in js
    assert "Not recorded" in js
    assert "Existing completed rows keep their original execution provider" in js


def test_sidebar_navigation_is_normalized_with_worker_review_and_chunk_icons():
    js = read("nav.js")
    for href, label in [
        ("/workers", "Workers"),
        ("/review-workers", "Review workers"),
        ("/chunks", "Chunk Viewer"),
    ]:
        assert href in js
        assert label in js
    assert "icons.workers" in js
    assert "icons.reviewWorkers" in js
    assert "icons.chunks" in js
    assert "nav.replaceChildren(fragment)" in js


def test_sidebar_keeps_version_and_diagnostics_visible_with_scrollable_nav():
    css = read("styles.css")
    assert "flex: 1 1 auto" in css
    assert "overflow-y: auto" in css
    assert "Keep version + diagnostics visible while only the navigation list scrolls" in css
    assert ".ui29 .sidebar-footer { margin:8px 10px 0; flex:none; }" in css


def test_worker_forms_preserve_unsaved_edits_during_status_polling():
    workers = read("workers.js")
    review = read("review-workers.js")
    assert "dirtyWorkers = new Set()" in workers
    assert "!dirtyWorkers.size" in workers
    assert "settingsDirty" in review
    assert "if(settingsInFlight||(!force&&settingsDirty))return" in review
    assert "loadStatus()" in review


def test_review_workers_page_uses_structured_responsive_assignment_layout():
    html = read("review-workers.html")
    css = read("styles.css")
    assert 'class="review-overview-grid"' in html
    assert 'id="review-worker-assignment"' in html
    assert 'id="review-unsaved"' in html
    assert ".review-assignment-card" in css
    assert ".review-assignment-options" in css
    assert "grid-template-columns:repeat(auto-fit,minmax(300px,1fr))" in css


def test_verification_separates_fast_status_from_heavy_result_refresh():
    js = read("verification.js")
    assert "DETAIL_REFRESH_MS = 15000" in js
    assert "async function load(forceDetails = false)" in js
    assert "if (forceDetails || now >= detailRefreshAt)" in js
    assert "setStableHtml" in js
    assert "shouldDeferRefresh" in js


def test_queue_event_refresh_is_debounced_non_overlapping_and_interaction_safe():
    js = read("dashboard.js")
    assert "let refreshInFlight = false" in js
    assert "scheduleEventRefresh" in js
    assert "shouldDeferRefresh" in js
    assert "setDashboardHtml" in js



def test_ah5_text_audit_has_same_ai_review_filters_as_vision():
    html = read("text-audit.html")
    js = read("text-audit.js")
    for control in ("ta-ai-review", "ta-ai-recommendation", "ta-review-worker", "ta-attention"):
        assert f'id="{control}"' in html
    assert "Needs my attention" in html
    assert "Verifier ↔ reviewer disagreement" in html
    assert 'params.set("ai_review", aiReview)' in js
    assert 'params.set("review_worker", reviewWorker)' in js
    assert "AI review assistant · advisory only" in js
    assert "Anomaly review · Colab" in js


def test_ah5_dedicated_anomaly_review_page_has_colab_rereview_and_explicit_yes_no():
    html = read("anomaly-review.html")
    js = read("anomaly-review.js")
    nav = read("nav.js")
    main_py = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")

    assert "<h1>Anomaly Review</h1>" in html
    assert "Include all human-reviewed" in html
    assert "Re-verify with Colab" in html
    assert "Yes</strong> explicitly accepts" in html
    assert "No</strong> keeps the current state" in html
    assert "Yes · accept Colab" in js
    assert "No · keep current" in js
    assert "/api/anomaly-review" in js
    assert "/anomaly-review" in nav
    assert "Anomaly review" in nav
    assert '@app.get("/anomaly-review")' in main_py
    assert '@app.get("/api/anomaly-review")' in main_py
    assert '@app.post("/api/anomaly-review/{job_id}/{entry_id}/decision")' in main_py
    assert "human_authority_preserved" in main_py


def test_ah5_anomaly_page_defers_background_polling_but_forces_user_actions():
    js = read("anomaly-review.js")
    assert "async function load(force=false)" in js
    assert "!force && window.DoclingUI?.shouldDeferRefresh?.()" in js
    assert "load(true)" in js
    assert "document.visibilityState === \"visible\"" in js
