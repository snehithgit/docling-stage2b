import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

from app.stage2b import _apply_vision_structural_gate, _validate_vision
from app.stage2c import (
    analyze_text_structure,
    correction_fidelity,
    garble_profile,
    image_structure_evidence,
    human_review_summary,
    normalize_human_verified_ledger,
    rebuild_chunk_overlays,
    technical_format_profile,
    upsert_ledger_entry,
    source_transcription_safety_profile,
    vision_enrichment_status,
)


def test_technical_format_is_pattern_class_not_specific_value():
    profile = technical_format_profile("PT100 24 V ± 5% QF1")
    assert profile["is_technical_format"] is True
    assert profile["dominant"] is True
    assert {"unit_value", "identifier"} <= set(profile["classes"])


def test_acronyms_and_identifiers_are_not_no_vowel_garble():
    profile = garble_profile("PLC RPM MCC PT100 K1 X12 QF1")
    assert profile["structurally_normal"] is True
    assert profile["score"] < 0.12


def test_broken_fragment_sequence_can_confirm_garble():
    profile = garble_profile(": oe s  e  e  te  e e ue")
    assert profile["confirmed"] is True
    assert profile["score"] >= 0.12

def test_realistic_midscore_garble_is_now_correction_eligible():
    # Real manuals contain corruption much milder than the synthetic worst-case
    # fixture. This example must clear the empirically calibrated gate.
    profile = garble_profile("Aait art t ais Ama-it oe s e te e")
    assert profile["score"] >= 0.12
    assert profile["confirmed"] is True


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_structure_accepts_thin_distributed_schematic_lines():
    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    for y in (100, 200, 300, 400, 500):
        draw.line((50, y, 750, y), fill="black", width=3)
    for x in (100, 250, 400, 550, 700):
        draw.line((x, 60, x, 540), fill="black", width=3)
    evidence = image_structure_evidence(_png_bytes(image))
    assert evidence["diagram_like"] is True
    assert evidence["thin_stroke_fraction"] >= 0.45
    assert evidence["method"] == "binary_erosion_thin_stroke_v2"


def test_image_structure_rejects_bold_filled_logo_shape():
    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    # Deliberately large, dark, logo-like wordmark proxy. The old occupancy
    # metric called shapes like this diagram-like; the erosion metric must not.
    draw.rectangle((160, 230, 640, 370), fill="black")
    draw.rectangle((210, 260, 590, 340), fill="white")
    for x in (200, 310, 420, 530):
        draw.rectangle((x, 250, x + 60, 350), fill="black")
    evidence = image_structure_evidence(_png_bytes(image))
    assert evidence["diagram_like"] is False
    assert evidence["filled_core_fraction"] > 0.35


def test_fidelity_rejects_new_engineering_value():
    gate = correction_fidelity("Pressure 1? bar", "Pressure 16 bar")
    assert gate["accepted"] is False
    assert "UNSUPPORTED_NEW_TECHNICAL_TOKEN" in gate["reasons"]


def test_fidelity_allows_character_cleanup_without_new_value():
    gate = correction_fidelity("Pump mo tor running", "Pump motor running")
    assert gate["accepted"] is True


def test_vision_schema_separates_text_from_objects():
    parsed = _validate_vision({
        "verdict": "TECHNICAL_USEFUL",
        "confidence": 0.9,
        "visible_text": ["K1", "M1"],
        "visible_objects": ["relay-like symbol"],
        "diagram_category": "electrical_schematic",
        "summary": "Visible interconnections.",
    })
    assert parsed["visible_text"] == ["K1", "M1"]
    assert parsed["visible_labels"] == ["K1", "M1"]  # compatibility only
    assert parsed["visible_objects"] == ["relay-like symbol"]
    assert parsed["diagram_category"] == "electrical_schematic"


def test_legacy_vision_summary_is_preserved_without_promoting_labels():
    parsed = _validate_vision({
        "verdict": "TECHNICAL_USEFUL",
        "confidence": 0.8,
        "visible_labels": ["three rectangular blocks", "K1"],
        "full_image": {"summary": "Simple legacy schematic."},
        "unresolved": False,
    })
    assert parsed["legacy_schema"] is True
    assert parsed["legacy_visible_labels"] == ["three rectangular blocks", "K1"]
    assert parsed["visible_text"] == []
    assert parsed["summary"] == "Simple legacy schematic."


def test_diagram_override_requires_structural_corroboration():
    merged = {
        "verdict": "DECORATIVE_OR_LOW_VALUE",
        "confidence": 0.8,
        "diagram_category": "wiring_diagram",
    }
    no = _apply_vision_structural_gate(merged, {"diagram_like": False, "diagram_score": 0.2})
    yes = _apply_vision_structural_gate(merged, {"diagram_like": True, "diagram_score": 0.8})
    assert no["verdict"] == "DECORATIVE_OR_LOW_VALUE"
    assert yes["verdict"] == "TECHNICAL_USEFUL"
    assert yes["deterministic_override"] == "STRUCTURALLY_CORROBORATED_TECHNICAL_DIAGRAM"


def test_diagram_like_decorative_conflict_is_pending_not_excluded():
    merged = {
        "verdict": "DECORATIVE_OR_LOW_VALUE",
        "confidence": 0.8,
        "diagram_category": "decorative_photo",
        "unresolved": False,
    }
    checked = _apply_vision_structural_gate(merged, {"diagram_like": True, "diagram_score": 0.9})
    assert checked["verdict"] == "UNCERTAIN"
    assert checked["unresolved"] is True
    assert checked["deterministic_override"] == "STRUCTURAL_DIAGRAM_CONFLICT_REQUIRES_REVIEW"

    # Stage 2C independently protects persisted/legacy results too.
    status, reason = vision_enrichment_status({
        "verdict": "DECORATIVE_OR_LOW_VALUE",
        "structural_image_evidence": {"diagram_like": True, "diagram_score": 0.9},
    })
    assert status == "pending"
    assert reason == "VISION_DIAGRAM_CONFLICT_REVIEW"


def test_source_transcription_safety_profile_rejects_real_wrong_region_signature():
    profile = source_transcription_safety_profile("Transmilter Terminal", "+1 SW RX NIX TX")
    assert profile["accepted"] is False
    assert "WRONG_REGION_LOW_OVERLAP" in profile["reasons"]


def test_ledger_only_applied_entries_feed_chunk_overlays(tmp_path: Path):
    result_dir = tmp_path / "book"
    common = {"route_id": "R1", "page": 1, "source_index": 2}
    upsert_ledger_entry(result_dir, "abc", {
        "entry_id": "g:text:R1", "entry_type": "text_correction", **common,
        "status": "rejected", "proposed_text": "wrong",
    })
    upsert_ledger_entry(result_dir, "abc", {
        "entry_id": "g:vision:R2", "entry_type": "vision_enrichment",
        "route_id": "R2", "page": 2, "source_index": 4,
        "status": "applied", "diagram_category": "electrical_schematic",
        "visible_text": ["K1"], "visible_objects": ["symbol"], "generated_summary": "diagram",
    })
    lines = [json.loads(line) for line in (result_dir / "chunk_overlays.jsonl").read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["entry_type"] == "vision_enrichment"
    assert lines[0]["route_id"] == "R2"

import pytest
from types import SimpleNamespace
from app.stage2b import _attempt_pi5_correction


class _FakePi5:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat_text(self, system, user, model=None, max_tokens=160):
        self.calls += 1
        return self.responses.pop(0)


def _chat(content, finish="stop"):
    return {"choices": [{"message": {"content": json.dumps(content)}, "finish_reason": finish}]}


def _stage2c_cfg():
    return SimpleNamespace(
        stage2c_enabled=True,
        stage2c_text_correction_enabled=True,
        stage2c_correction_min_confidence=0.85,
        stage2c_correction_min_garble_score=0.12,
        stage2c_pi5_correction_max_tokens=220,
        stage2c_correction_min_similarity=0.45,
        stage2c_correction_min_length_ratio=0.50,
        stage2c_correction_max_length_ratio=1.80,
        stage2b_pi5_max_tokens=160,
    )


@pytest.mark.asyncio
async def test_pi5_corrector_applies_only_after_clean_reverification():
    client = _FakePi5([
        _chat({"corrected_text": "Pump motor running", "confidence": 0.96, "note": "joined OCR split"}),
        _chat({"verdict": "LIKELY_OK", "confidence": 0.96, "reason_code": "CLEAN_PROSE", "evidence": "Pump motor running"}),
    ])
    verification = {
        "verdict": "LIKELY_CORRUPT", "confidence": 0.95, "reason_code": "OCR_GARBLE",
        "evidence_valid": True, "structural_analysis": {"garble": {"score": 0.5}},
    }
    result = await _attempt_pi5_correction(client, "Pump mo tor running", "", verification, "model", _stage2c_cfg())
    assert result["status"] == "applied"
    assert result["reverification"]["verdict"] == "LIKELY_OK"
    assert client.calls == 2


@pytest.mark.asyncio
async def test_pi5_corrector_rejects_invented_value_before_reverification():
    client = _FakePi5([
        _chat({"corrected_text": "Pressure 16 bar", "confidence": 0.99, "note": "guess"}),
        _chat({"corrected_text": "Pressure 16 bar", "confidence": 0.99, "note": "guess again"}),
    ])
    verification = {
        "verdict": "LIKELY_CORRUPT", "confidence": 0.95, "reason_code": "OCR_GARBLE",
        "evidence_valid": True, "structural_analysis": {"garble": {"score": 0.5}},
    }
    result = await _attempt_pi5_correction(client, "Pressure 1? bar", "", verification, "model", _stage2c_cfg())
    assert result["status"] == "rejected"
    assert "UNSUPPORTED_NEW_TECHNICAL_TOKEN" in result["fidelity"]["reasons"]
    assert result["correction_attempt_count"] == 2
    assert client.calls == 2


@pytest.mark.asyncio
async def test_pi5_corrector_retries_without_context_after_context_leakage():
    client = _FakePi5([
        _chat({"corrected_text": "Pump mo tor running NEARBY CONTEXT: Pressure 16 bar", "confidence": 0.7}),
        _chat({"corrected_text": "Pump motor running", "confidence": 0.96}),
        _chat({"verdict": "LIKELY_OK", "confidence": 0.96, "reason_code": "CLEAN_PROSE", "evidence": "Pump motor running"}),
    ])
    verification = {
        "verdict": "LIKELY_CORRUPT", "confidence": 0.95, "reason_code": "OCR_GARBLE",
        "evidence_valid": True, "structural_analysis": {"garble": {"score": 0.5}},
    }
    result = await _attempt_pi5_correction(
        client, "Pump mo tor running", "Pressure 16 bar", verification, "model", _stage2c_cfg()
    )
    assert result["status"] == "applied"
    assert result["proposed_text"] == "Pump motor running"
    assert result["correction_attempt_count"] == 2
    assert client.calls == 3


def test_human_verified_text_overlay_has_human_provenance(tmp_path: Path):
    result_dir = tmp_path / "book-human"
    upsert_ledger_entry(result_dir, "abc", {
        "entry_id": "g:text:R9",
        "entry_type": "text_correction",
        "route_id": "R9",
        "page": 4,
        "source_index": 8,
        "status": "applied",
        "original_text": "Wind Spee d",
        "proposed_text": "Wind Speed",
        "human_verified": True,
        "human_review": {"before_text": "Wind Spee d", "after_text": "Wind Speed"},
    })
    lines = [json.loads(line) for line in (result_dir / "chunk_overlays.jsonl").read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["text"] == "Wind Speed"
    assert lines[0]["provenance"] == "human_verified_manual_correction"
    assert lines[0]["human_verified"] is True


def test_automatic_upsert_cannot_overwrite_human_verified_correction(tmp_path: Path):
    result_dir = tmp_path / "book-human-precedence"
    human = {
        "entry_id": "g:text:R9",
        "entry_type": "text_correction",
        "route_id": "R9",
        "page": 4,
        "source_index": 8,
        "rule_version": "stage2c-structural-v2",
        "status": "applied",
        "reason": "HUMAN_VERIFIED",
        "original_text": "Wind Spee d",
        "proposed_text": "Wind Speed",
        "human_verified": True,
        "human_review": {"before_text": "Wind Spee d", "after_text": "Wind Speed"},
    }
    upsert_ledger_entry(result_dir, "abc", human)

    automatic = {
        "entry_id": "g:text:R9",
        "entry_type": "text_correction",
        "route_id": "R9",
        "page": 4,
        "source_index": 8,
        "rule_version": "stage2c-structural-v2",
        "status": "applied",
        "status_reason": "PI5_CORRECTION_REVERIFIED",
        "original_text": "Wind Spee d",
        "proposed_text": "Wind speed indication",
        "human_verified": False,
    }
    upsert_ledger_entry(result_dir, "abc", automatic)

    ledger = json.loads((result_dir / "correction_ledger.json").read_text())
    assert len(ledger["entries"]) == 1
    saved = ledger["entries"][0]
    assert saved["human_verified"] is True
    assert saved["proposed_text"] == "Wind Speed"
    assert saved["reason"] == "HUMAN_VERIFIED"

    overlays = [json.loads(line) for line in (result_dir / "chunk_overlays.jsonl").read_text().splitlines()]
    assert len(overlays) == 1
    assert overlays[0]["text"] == "Wind Speed"
    assert overlays[0]["provenance"] == "human_verified_manual_correction"


def test_human_save_replaces_stale_automatic_status_reason_but_keeps_it_in_audit():
    from app.stage2c import apply_human_correction_to_entry

    entry = {
        "entry_id": "g:text:R1",
        "entry_type": "text_correction",
        "original_text": "Wind Spee d",
        "proposed_text": "Wind Spee d",
        "status": "rejected",
        "reason": "REVERIFICATION_STILL_CORRUPT",
        "status_reason": "REVERIFICATION_STILL_CORRUPT",
    }
    saved = apply_human_correction_to_entry(entry, text="Wind Speed", action="apply")
    assert saved["status"] == "applied"
    assert saved["reason"] == "HUMAN_VERIFIED"
    assert saved["status_reason"] == "HUMAN_VERIFIED"
    assert saved["human_verified"] is True
    assert saved["human_review"]["previous_status"] == "rejected"
    assert saved["human_review"]["previous_reason"] == "REVERIFICATION_STILL_CORRUPT"
    assert saved["human_review"]["previous_status_reason"] == "REVERIFICATION_STILL_CORRUPT"


def test_normalize_human_verified_ledger_migrates_stale_live_reason_idempotently(tmp_path: Path):
    from app.stage2c import normalize_human_verified_ledger

    result_dir = tmp_path / "legacy-human"
    result_dir.mkdir()
    ledger = {
        "schema": "docling-correction-ledger/v2",
        "source_zip_sha256": "abc",
        "rule_version": "stage2c-structural-v2",
        "entries": [{
            "entry_id": "g:text:R1",
            "entry_type": "text_correction",
            "route_id": "R1",
            "page": 12,
            "source_index": 346,
            "status": "applied",
            "reason": "HUMAN_VERIFIED",
            "status_reason": "REVERIFICATION_STILL_CORRUPT",
            "original_text": "Wind Spee d",
            "proposed_text": "Wind Speed",
            "human_verified": True,
            "human_review": {"action": "apply", "before_text": "Wind Spee d", "after_text": "Wind Speed"},
        }],
    }
    (result_dir / "correction_ledger.json").write_text(json.dumps(ledger))

    assert normalize_human_verified_ledger(result_dir) == 1
    saved = json.loads((result_dir / "correction_ledger.json").read_text())["entries"][0]
    assert saved["reason"] == "HUMAN_VERIFIED"
    assert saved["status_reason"] == "HUMAN_VERIFIED"
    assert saved["human_review"]["previous_status_reason"] == "REVERIFICATION_STILL_CORRUPT"
    overlays = [json.loads(line) for line in (result_dir / "chunk_overlays.jsonl").read_text().splitlines()]
    assert overlays[0]["text"] == "Wind Speed"
    assert overlays[0]["provenance"] == "human_verified_manual_correction"
    assert normalize_human_verified_ledger(result_dir) == 0


def test_human_review_summary_requires_likely_corrupt_until_human_decision(tmp_path):
    from app.stage2c import human_review_summary
    result_dir = tmp_path / "book"
    result_dir.mkdir()
    ledger = {
        "entries": [
            {
                "entry_id": "g:text:R1", "entry_type": "text_correction", "route_id": "R1",
                "verification_verdict": "LIKELY_CORRUPT", "status": "rejected", "human_verified": False,
            },
            {
                "entry_id": "g:text:R2", "entry_type": "text_correction", "route_id": "R2",
                "verification_verdict": "LIKELY_CORRUPT", "status": "applied", "human_verified": True,
            },
            {
                "entry_id": "g:text:R3", "entry_type": "text_correction", "route_id": "R3",
                "verification_verdict": "LIKELY_OK", "status": "pending", "human_verified": False,
            },
            {
                "entry_id": "g:text:R4", "entry_type": "text_correction", "route_id": "R4",
                "verification_verdict": "UNCERTAIN", "status": "pending", "human_verified": False,
            },
        ]
    }
    (result_dir / "correction_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    summary = human_review_summary(result_dir)
    assert summary["review_candidates"] == 3
    assert summary["human_reviewed"] == 1
    assert summary["review_required"] == 2
    assert summary["corrupt_review_required"] == 1
    assert summary["uncertain_review_required"] == 1
    assert summary["review_complete"] is False
    assert summary["required_entry_ids"] == ["g:text:R1", "g:text:R4"]


@pytest.mark.asyncio
async def test_pi5_corrector_generates_human_suggestion_below_auto_garble_gate():
    client = _FakePi5([
        _chat({"corrected_text": "system's alarm", "confidence": 0.96, "note": "joined OCR spacing"}),
    ])
    verification = {
        "verdict": "LIKELY_CORRUPT", "confidence": 0.95, "reason_code": "OCR_GARBLE",
        "evidence_valid": True, "structural_analysis": {"garble": {"score": 0.10}},
    }
    result = await _attempt_pi5_correction(client, "system ' s alarm", "", verification, "model", _stage2c_cfg())
    assert result["status"] == "proposed"
    assert result["proposed_text"] == "system's alarm"
    assert result["suggestion_only"] is True
    assert result["eligibility"]["automatic_apply_eligible"] is False
    assert client.calls == 1

def test_policy_revalidation_updates_only_unreviewed_uncertain_entries(tmp_path):
    from app.stage2c import revalidate_unreviewed_text_entries
    result_dir = tmp_path / "book"
    result_dir.mkdir()
    ledger = {
        "entries": [
            {
                "entry_id": "g:text:R1", "entry_type": "text_correction", "route_id": "R1",
                "verification_verdict": "UNCERTAIN", "status": "pending", "human_verified": False,
                "original_text": "Softwara", "proposed_text": None,
            },
            {
                "entry_id": "g:text:R2", "entry_type": "text_correction", "route_id": "R2",
                "verification_verdict": "UNCERTAIN", "status": "applied", "human_verified": True,
                "original_text": "Wind Spee d", "proposed_text": "Wind Speed",
            },
        ]
    }
    (result_dir / "correction_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    changed = revalidate_unreviewed_text_entries(result_dir, {
        "g:text:R1": {"verdict": "LIKELY_OK", "verification": {"verdict": "LIKELY_OK"}},
        "g:text:R2": {"verdict": "LIKELY_OK", "verification": {"verdict": "LIKELY_OK"}},
    })
    assert changed == 1
    entries = {e["entry_id"]: e for e in json.loads((result_dir / "correction_ledger.json").read_text())["entries"]}
    assert entries["g:text:R1"]["verification_verdict"] == "LIKELY_OK"
    assert entries["g:text:R1"]["status"] == "excluded"
    assert entries["g:text:R2"]["verification_verdict"] == "UNCERTAIN"
    assert entries["g:text:R2"]["human_verified"] is True


def test_optional_human_review_does_not_require_save_for_auto_applied(tmp_path: Path):
    result_dir = tmp_path / "book"
    result_dir.mkdir()
    (result_dir / "correction_ledger.json").write_text(json.dumps({
        "entries": [{
            "entry_id": "g:text:R1",
            "entry_type": "text_correction",
            "status": "applied",
            "verification_verdict": "LIKELY_CORRUPT",
            "original_text": "bad",
            "proposed_text": "correct",
            "human_verified": False,
        }]
    }), encoding="utf-8")
    summary = human_review_summary(result_dir, require_human=False)
    assert summary["auto_applied"] == 1
    assert summary["review_required"] == 0
    assert summary["automation_unresolved"] == 0
    assert summary["review_complete"] is True


def test_old_partial_source_transcription_is_demoted_and_overlay_removed(tmp_path: Path):
    result_dir = tmp_path / "run"
    result_dir.mkdir()
    original = (
        "Hydraulic oil temperatures above 82°C / 180°F damage most seal compounds and accelerate the degradation "
        "of the oil. While the operation of any hydraulic system at temperatures above 82°C /180°F should be avoided, "
        "the oil temperature is too high when the viscosity falls below the optimum value for the hydraulic system's "
        "components. This can occur well below 82°C / 180°F, depending on the oil's viscosity grade."
    )
    proposed = original.split(" for the hydraulic system")[0]
    entry = {
        "entry_id": "old:text:R00010", "entry_type": "text_correction", "route_id": "R00010",
        "status": "applied", "status_reason": "SOURCE_IMAGE_TARGET_RECONSTRUCTION",
        "original_text": original, "proposed_text": proposed, "human_verified": False,
        "verification_verdict": "LIKELY_CORRUPT", "rule_version": "stage2c-structural-v2",
    }
    (result_dir / "correction_ledger.json").write_text(json.dumps({"entries": [entry]}), encoding="utf-8")
    rebuild_chunk_overlays(result_dir, [entry])
    assert (result_dir / "chunk_overlays.jsonl").read_text(encoding="utf-8") == ""
    assert normalize_human_verified_ledger(result_dir) == 1
    ledger = json.loads((result_dir / "correction_ledger.json").read_text(encoding="utf-8"))
    saved = ledger["entries"][0]
    assert saved["status"] == "pending"
    assert saved["proposed_text"] is None
    assert saved["status_reason"] == "INCOMPLETE_SOURCE_TRANSCRIPTION_KEEP_ORIGINAL"


def test_stage2c_independent_gate_demotes_wrong_region_applied_entry(tmp_path: Path):
    result_dir = tmp_path / "wrong-region"
    result_dir.mkdir()
    entry = {
        "entry_id": "g:text:R3", "entry_type": "text_correction", "route_id": "R3",
        "status": "applied", "status_reason": "SOURCE_IMAGE_TARGET_RECONSTRUCTION",
        "original_text": "Transmilter Terminal", "proposed_text": "+1 SW RX NIX TX",
        "human_verified": False, "verification_verdict": "LIKELY_CORRUPT",
        "scope_guard": {"accepted": True, "target_token_recall": 0.0, "sequence_similarity": 0.2857},
    }
    upsert_ledger_entry(result_dir, "sha", entry)
    ledger = json.loads((result_dir / "correction_ledger.json").read_text(encoding="utf-8"))
    saved = ledger["entries"][0]
    assert saved["status"] == "pending"
    assert saved["proposed_text"] is None
    assert saved["status_reason"] == "WRONG_REGION_LOW_OVERLAP_KEEP_ORIGINAL"
    assert (result_dir / "chunk_overlays.jsonl").read_text(encoding="utf-8") == ""


def test_upsert_never_persists_partial_automatic_source_transcription_as_applied(tmp_path: Path):
    result_dir = tmp_path / "run2"
    result_dir.mkdir()
    original = "A long immutable target sentence " * 8
    proposed = original[: int(len(original) * 0.7)].strip()
    entry = {
        "entry_id": "new:text:R1", "entry_type": "text_correction", "route_id": "R1",
        "status": "applied", "status_reason": "SOURCE_IMAGE_TARGET_RECONSTRUCTION",
        "original_text": original, "proposed_text": proposed, "human_verified": False,
        "verification_verdict": "LIKELY_CORRUPT",
        "source_reconstruction": {"finish_reason": "length", "truncated": True},
    }
    upsert_ledger_entry(result_dir, "sha", entry)
    ledger = json.loads((result_dir / "correction_ledger.json").read_text(encoding="utf-8"))
    saved = ledger["entries"][0]
    assert saved["status"] == "pending"
    assert saved["proposed_text"] is None
    assert (result_dir / "chunk_overlays.jsonl").read_text(encoding="utf-8") == ""
