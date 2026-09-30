from app.anomaly_review import anomaly_evidence_signature, anomaly_prompt_context, detect_anomaly_types


def test_text_anomaly_detector_catches_disagreement_truncation_and_large_expansion():
    entry = {
        "entry_id": "g:text:R1",
        "entry_type": "text_correction",
        "verification_verdict": "LIKELY_CORRUPT",
        "reason_code": "VERIFIER_TRANSCRIPTION_TRUNCATED_KEEP_ORIGINAL",
        "original_text": "PUMP PRESURE",
        "proposed_text": "PUMP PRESSURE " + ("technical recovered text " * 8),
        "ai_review_assistant": {
            "recommendation": "KEEP_ORIGINAL",
            "confidence": 0.62,
        },
    }
    anomalies = detect_anomaly_types(entry, "text")
    assert "VERIFIER_REVIEWER_DISAGREEMENT" in anomalies
    assert "VERIFIER_TRANSCRIPTION_TRUNCATED" in anomalies
    assert "LOW_AI_REVIEW_CONFIDENCE" in anomalies
    assert "LARGE_TEXT_EXPANSION" in anomalies


def test_text_anomaly_detector_does_not_treat_uncertain_resolution_as_disagreement():
    entry = {
        "entry_id": "g:text:R2",
        "entry_type": "text_correction",
        "verification_verdict": "UNCERTAIN",
        "original_text": "PUMP PRESURE",
        "proposed_text": "PUMP PRESSURE",
        "ai_review_assistant": {
            "recommendation": "APPLY_PROPOSED",
            "confidence": 0.98,
        },
    }
    anomalies = detect_anomaly_types(entry, "text")
    assert "VERIFIER_REVIEWER_DISAGREEMENT" not in anomalies


def test_vision_detector_catches_opposite_review_and_missing_evidence():
    entry = {
        "entry_id": "g:vision:V1",
        "entry_type": "vision_enrichment",
        "verification_verdict": "DECORATIVE_OR_LOW_VALUE",
        "generated_summary": "",
        "visible_text": [],
        "visible_objects": [],
        "ai_review_assistant": {
            "recommendation": "TECHNICAL",
            "confidence": 0.95,
        },
    }
    anomalies = detect_anomaly_types(entry, "vision")
    assert "VERIFIER_REVIEWER_DISAGREEMENT" in anomalies
    assert "MISSING_TECHNICAL_EVIDENCE" in anomalies


def test_anomaly_signature_ignores_anomaly_result_but_changes_with_human_decision():
    entry = {
        "entry_id": "g:text:R3",
        "entry_type": "text_correction",
        "verification_verdict": "UNCERTAIN",
        "original_text": "A",
        "proposed_text": "B",
        "human_verified": False,
        "ai_review_assistant": {"recommendation": "NEEDS_HUMAN", "confidence": 0.5},
    }
    before = anomaly_evidence_signature(entry, "text")
    entry["anomaly_review"] = {"verdict": "KEEP_ORIGINAL"}
    entry["anomaly_review_history"] = [{"verdict": "NEEDS_HUMAN"}]
    assert anomaly_evidence_signature(entry, "text") == before
    entry["human_verified"] = True
    assert anomaly_evidence_signature(entry, "text") != before


def test_prompt_context_includes_human_state_without_granting_model_authority():
    entry = {
        "entry_id": "g:vision:V2",
        "entry_type": "vision_enrichment",
        "verification_verdict": "UNCERTAIN",
        "human_verified": True,
        "human_visual_decision": "technical",
        "ai_review_assistant": {"recommendation": "NEEDS_HUMAN", "confidence": 0.8},
    }
    context = anomaly_prompt_context(entry, "vision", ["POST_HUMAN_REVIEW_RECHECK"])
    assert context["human_verified"] is True
    assert context["human_visual_decision"] == "technical"
    assert context["anomaly_types"] == ["POST_HUMAN_REVIEW_RECHECK"]
