import json
from pathlib import Path

from app.stage2c import apply_human_visual_decision, merge_human_visual_evidence, set_audit_gate_bypass, verifier_audit_summary


def _ledger(path: Path, entries):
    path.mkdir(parents=True, exist_ok=True)
    (path / "correction_ledger.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")


def test_uncertain_visual_blocks_until_human_decision(tmp_path: Path):
    d = tmp_path / "book"
    _ledger(d, [{"entry_id":"g:vision:AV000001", "entry_type":"vision_enrichment", "status":"pending", "verification_verdict":"UNCERTAIN", "unresolved":True, "generated_summary":"Visible technical diagram."}])
    assert verifier_audit_summary(d)["blocking_review_required"] == 1
    entry = apply_human_visual_decision(d, "g:vision:AV000001", "technical")
    assert entry["status"] == "applied"
    assert entry["human_visual_decision"] == "technical"
    assert verifier_audit_summary(d)["blocking_review_required"] == 0


def test_visual_not_useful_excludes_from_overlay(tmp_path: Path):
    d = tmp_path / "book"
    _ledger(d, [{"entry_id":"g:vision:R1", "entry_type":"vision_enrichment", "status":"pending", "verification_verdict":"UNCERTAIN"}])
    entry = apply_human_visual_decision(d, "g:vision:R1", "not_useful")
    assert entry["status"] == "excluded"
    assert entry["status_reason"] == "HUMAN_VISUAL_EXCLUDED"


def test_testing_bypass_does_not_resolve_evidence(tmp_path: Path):
    d = tmp_path / "book"
    _ledger(d, [{"entry_id":"g:vision:R1", "entry_type":"vision_enrichment", "status":"pending", "verification_verdict":"UNCERTAIN"}])
    set_audit_gate_bypass(d, True)
    summary = verifier_audit_summary(d)
    assert summary["review_required"] == 1
    assert summary["blocking_review_required"] == 0
    assert summary["gate_status"] == "bypassed_for_testing"
    saved = json.loads((d / "correction_ledger.json").read_text())
    assert not saved["entries"][0].get("human_visual_decision")


def test_human_useful_empty_evidence_marks_recovery_required(tmp_path: Path):
    d = tmp_path / "book"
    _ledger(d, [{
        "entry_id":"g:vision:R2", "entry_type":"vision_enrichment", "status":"pending",
        "verification_verdict":"UNCERTAIN", "unresolved":True, "verification_job_id":77,
        "visible_text":[], "visible_objects":[], "generated_summary":"",
        "verification_parse_failed": True,
    }])
    entry = apply_human_visual_decision(d, "g:vision:R2", "useful")
    assert entry["status"] == "applied"
    assert entry["human_verified"] is True
    assert entry["human_evidence_recovery_required"] is True
    summary = verifier_audit_summary(d)
    assert summary["vision_evidence_recovery_required"] == 1
    assert summary["blocking_review_required"] == 1
    assert summary["gate_status"] == "waiting_for_evidence_recovery"


def test_human_visual_recovery_fills_evidence_without_overwriting_decision(tmp_path: Path):
    d = tmp_path / "book"
    _ledger(d, [{
        "entry_id":"g:vision:R3", "entry_type":"vision_enrichment", "status":"applied",
        "status_reason":"HUMAN_VISUAL_ACCEPTED", "verification_verdict":"UNCERTAIN",
        "human_verified":True, "human_visual_decision":"useful", "unresolved":False,
        "visible_text":[], "visible_objects":[], "generated_summary":"",
        "human_evidence_recovery_required": True,
    }])
    updated = merge_human_visual_evidence(d, "g:vision:R3", {
        "verdict":"TECHNICAL_USEFUL", "diagram_category":"wiring_diagram",
        "visible_text":["PSU 1", "24V DC 5 A"],
        "visible_objects":["power supply module"],
        "summary":"Main and emergency power supply wiring.",
        "unresolved":False, "crop_coverage":"human_recovery_all_configured_regions",
        "incomplete_crop_count":0,
    }, "human_visual_recovery_77.json")
    assert updated["human_visual_decision"] == "useful"
    assert updated["human_verified"] is True
    assert updated["status"] == "applied"
    assert updated["status_reason"] == "HUMAN_VISUAL_ACCEPTED"
    assert updated["human_evidence_recovery_required"] is False
    assert "24V DC 5 A" in updated["visible_text"]
    assert updated["diagram_category"] == "wiring_diagram"
    summary = verifier_audit_summary(d)
    assert summary["vision_evidence_recovery_required"] == 0
    assert summary["blocking_review_required"] == 0

def test_existing_human_useful_empty_evidence_without_new_flag_still_blocks(tmp_path: Path):
    d = tmp_path / "book"
    _ledger(d, [{
        "entry_id":"g:vision:legacy", "entry_type":"vision_enrichment", "status":"applied",
        "status_reason":"HUMAN_VISUAL_ACCEPTED", "verification_verdict":"UNCERTAIN",
        "human_verified":True, "human_visual_decision":"useful", "unresolved":False,
        "visible_text":[], "visible_objects":[], "generated_summary":"",
    }])
    summary = verifier_audit_summary(d)
    assert summary["vision_evidence_recovery_required"] == 1
    assert summary["blocking_review_required"] == 1
