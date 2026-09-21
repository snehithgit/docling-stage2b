import json
from pathlib import Path

from app.stage2c import apply_human_visual_decision, set_audit_gate_bypass, verifier_audit_summary


def _ledger(path: Path, entries):
    path.mkdir(parents=True, exist_ok=True)
    (path / "correction_ledger.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")


def test_uncertain_visual_blocks_until_human_decision(tmp_path: Path):
    d = tmp_path / "book"
    _ledger(d, [{"entry_id":"g:vision:AV000001", "entry_type":"vision_enrichment", "status":"pending", "verification_verdict":"UNCERTAIN", "unresolved":True}])
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
