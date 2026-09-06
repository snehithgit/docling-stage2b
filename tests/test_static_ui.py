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
    assert "Pi5 + OnePlus verification" in verification
    assert "Verify book" in js
    assert "Auto verify all" in verification
    assert "/api/stage2b/results/pi5" in js
    assert "/api/stage2b/results/oneplus" in js
    assert "Stop verifier" in verification
    assert "Remaining text work" not in verification
    assert "Remaining vision work" not in verification
    assert "slice(0, 40)" not in js


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
