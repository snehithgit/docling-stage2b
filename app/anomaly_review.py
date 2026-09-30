from __future__ import annotations

import hashlib
import json
from typing import Any


_LOW_CONFIDENCE = 0.75


def _confidence(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0.0 <= number <= 1.0 else None


def _reason_blob(entry: dict[str, Any]) -> str:
    values = [
        entry.get("reason"),
        entry.get("reason_code"),
        entry.get("status_reason"),
        entry.get("verification_verdict"),
        (entry.get("verification") or {}).get("reason_code") if isinstance(entry.get("verification"), dict) else None,
    ]
    return " ".join(str(value or "") for value in values).upper()


def _text_disagreement(entry: dict[str, Any], review: dict[str, Any]) -> bool:
    verdict = str(entry.get("verification_verdict") or "").upper()
    recommendation = str(review.get("recommendation") or "").upper()
    if verdict == "LIKELY_CORRUPT" and recommendation == "KEEP_ORIGINAL":
        return True
    if verdict == "LIKELY_OK" and recommendation == "APPLY_PROPOSED":
        return True
    return False


def _vision_disagreement(entry: dict[str, Any], review: dict[str, Any]) -> bool:
    verdict = str(entry.get("verification_verdict") or "").upper()
    recommendation = str(review.get("recommendation") or "").upper()
    if verdict == "TECHNICAL_USEFUL" and recommendation in {"DECORATIVE", "NOT_USEFUL"}:
        return True
    if verdict == "DECORATIVE_OR_LOW_VALUE" and recommendation in {"TECHNICAL", "USEFUL"}:
        return True
    return False


def detect_anomaly_types(entry: dict[str, Any], review_type: str) -> list[str]:
    """Return deterministic anomaly classes worth an independent Colab audit.

    This detector never changes ledger state. Human-reviewed entries are still
    classifiable, but the automatic review supervisor deliberately excludes
    them; they enter anomaly review only through the explicit re-review action.
    """
    kind = "text" if str(review_type).lower() in {"text", "anomaly_text"} else "vision"
    review = entry.get("ai_review_assistant")
    review = review if isinstance(review, dict) else {}
    anomalies: list[str] = []

    recommendation = str(review.get("recommendation") or "").upper()
    confidence = _confidence(review.get("confidence"))
    if recommendation == "NEEDS_HUMAN":
        anomalies.append("AI_REVIEW_NEEDS_HUMAN")
    if confidence is not None and confidence < _LOW_CONFIDENCE:
        anomalies.append("LOW_AI_REVIEW_CONFIDENCE")

    if kind == "text":
        if review and _text_disagreement(entry, review):
            anomalies.append("VERIFIER_REVIEWER_DISAGREEMENT")
        if recommendation == "EDIT_SUGGESTED":
            anomalies.append("REVIEW_EDIT_SUGGESTED")
        if "TRUNCAT" in _reason_blob(entry):
            anomalies.append("VERIFIER_TRANSCRIPTION_TRUNCATED")

        original = str(entry.get("original_text") or "").strip()
        proposed = str(entry.get("proposed_text") or "").strip()
        if len(original) >= 12 and len(proposed) >= max(48, len(original) * 3):
            anomalies.append("LARGE_TEXT_EXPANSION")
        if len(original) >= 36 and proposed and len(proposed) * 3 < len(original):
            anomalies.append("LARGE_TEXT_CONTRACTION")
    else:
        if review and _vision_disagreement(entry, review):
            anomalies.append("VERIFIER_REVIEWER_DISAGREEMENT")
        if recommendation in {"TECHNICAL", "USEFUL"} and not (
            entry.get("visible_text")
            or entry.get("visible_objects")
            or str(entry.get("generated_summary") or "").strip()
        ):
            anomalies.append("MISSING_TECHNICAL_EVIDENCE")

    return list(dict.fromkeys(anomalies))


def anomaly_evidence_signature(entry: dict[str, Any], review_type: str) -> str:
    """Stable signature of evidence relevant to anomaly review.

    Existing anomaly_review/history fields are intentionally excluded so storing
    an audit result cannot schedule itself again. Human decisions are included:
    an explicit human change makes an older anomaly result stale.
    """
    kind = "text" if str(review_type).lower() in {"text", "anomaly_text"} else "vision"
    ai_review = entry.get("ai_review_assistant")
    ai_review = ai_review if isinstance(ai_review, dict) else {}
    common = {
        "entry_id": entry.get("entry_id"),
        "entry_type": entry.get("entry_type"),
        "page": entry.get("page"),
        "verification_verdict": entry.get("verification_verdict"),
        "status": entry.get("status"),
        "human_verified": bool(entry.get("human_verified")),
        "human_review": entry.get("human_review"),
        "ai_review_assistant": ai_review,
    }
    if kind == "text":
        payload = {
            **common,
            "source_type": entry.get("source_type"),
            "source_index": entry.get("source_index"),
            "table_index": entry.get("table_index"),
            "cell_index": entry.get("cell_index"),
            "original_text": entry.get("original_text"),
            "proposed_text": entry.get("proposed_text"),
        }
    else:
        payload = {
            **common,
            "picture_index": entry.get("picture_index"),
            "source_index": entry.get("source_index"),
            "artifact": entry.get("artifact"),
            "diagram_category": entry.get("diagram_category"),
            "generated_summary": entry.get("generated_summary"),
            "visible_text": entry.get("visible_text"),
            "visible_objects": entry.get("visible_objects"),
            "human_visual_decision": entry.get("human_visual_decision"),
        }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def anomaly_prompt_context(entry: dict[str, Any], review_type: str, anomaly_types: list[str]) -> dict[str, Any]:
    """Compact auditable context passed alongside the source crop/image."""
    kind = "text" if str(review_type).lower() in {"text", "anomaly_text"} else "vision"
    base: dict[str, Any] = {
        "anomaly_types": list(anomaly_types),
        "primary_verdict": entry.get("verification_verdict"),
        "primary_status": entry.get("status"),
        "ai_review_assistant": entry.get("ai_review_assistant"),
        "human_verified": bool(entry.get("human_verified")),
        "human_review": entry.get("human_review"),
    }
    if kind == "text":
        base.update({
            "docling_original": entry.get("original_text") or "",
            "primary_proposed": entry.get("proposed_text") or "",
            "source_type": entry.get("source_type") or "text",
        })
    else:
        base.update({
            "diagram_category": entry.get("diagram_category"),
            "generated_summary": entry.get("generated_summary") or "",
            "visible_text": entry.get("visible_text") or [],
            "visible_objects": entry.get("visible_objects") or [],
            "human_visual_decision": entry.get("human_visual_decision"),
        })
    return base


def text_anomaly_acceptance_plan(entry: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """Translate an explicitly accepted Colab anomaly verdict into a safe human action.

    The caller still records the human Yes/No decision. This helper never mutates
    ledger state and never grants the model authority on its own.
    """
    verdict = str((review or {}).get("verdict") or "NEEDS_HUMAN").upper().strip()
    original = str(entry.get("original_text") or "")
    proposed = str(entry.get("proposed_text") or "").strip()
    corrected = str((review or {}).get("corrected_text") or "").strip()

    if verdict == "KEEP_ORIGINAL":
        return {"mode": "human_text", "action": "reject", "text": original, "resolved": True}
    if verdict == "USE_PRIMARY_PROPOSAL":
        if proposed:
            return {"mode": "human_text", "action": "apply", "text": proposed, "resolved": True}
        return {"mode": "acknowledge", "resolved": False, "reason": "Primary proposal is empty"}
    if verdict == "REPLACE_TEXT":
        if corrected:
            return {"mode": "human_text", "action": "apply", "text": corrected, "resolved": True}
        return {"mode": "acknowledge", "resolved": False, "reason": "Colab replacement text is empty"}
    if verdict == "CONFIRM_CURRENT":
        if entry.get("human_verified"):
            return {"mode": "confirm_current", "resolved": True}
        status = str(entry.get("status") or "").lower()
        if status == "applied" and proposed:
            return {"mode": "human_text", "action": "apply", "text": proposed, "resolved": True}
        if status == "rejected":
            return {"mode": "human_text", "action": "reject", "text": original, "resolved": True}
        return {"mode": "acknowledge", "resolved": False, "reason": "Current automatic text state is not a final human decision"}
    return {"mode": "acknowledge", "resolved": False, "reason": "Colab requested human review"}


def vision_anomaly_acceptance_plan(entry: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """Translate an explicitly accepted visual anomaly verdict into a safe plan."""
    verdict = str((review or {}).get("verdict") or "NEEDS_HUMAN").upper().strip()
    if verdict == "TECHNICAL":
        return {"mode": "human_visual", "decision": "technical", "resolved": True}
    if verdict == "DECORATIVE":
        return {"mode": "human_visual", "decision": "decorative", "resolved": True}
    if verdict == "USEFUL":
        return {"mode": "human_visual", "decision": "useful", "resolved": True}
    if verdict == "NOT_USEFUL":
        return {"mode": "human_visual", "decision": "not_useful", "resolved": True}
    if verdict == "REPLACE_EVIDENCE":
        has_evidence = bool(
            str((review or {}).get("corrected_summary") or "").strip()
            or list((review or {}).get("visible_text") or [])
            or list((review or {}).get("visible_objects") or [])
        )
        return {
            "mode": "replace_evidence" if has_evidence else "acknowledge",
            "resolved": bool(entry.get("human_visual_decision")) and has_evidence,
            "reason": None if has_evidence else "Colab replacement evidence is empty",
        }
    if verdict == "CONFIRM_CURRENT":
        if entry.get("human_visual_decision"):
            return {"mode": "confirm_current", "resolved": True}
        primary = str(entry.get("verification_verdict") or "").upper()
        artifact = bool(entry.get("artifact_sweep")) or str(entry.get("route_id") or "").upper().startswith("AV")
        if primary == "TECHNICAL_USEFUL":
            return {"mode": "human_visual", "decision": "technical" if artifact else "useful", "resolved": True}
        if primary == "DECORATIVE_OR_LOW_VALUE":
            return {"mode": "human_visual", "decision": "decorative" if artifact else "not_useful", "resolved": True}
        return {"mode": "acknowledge", "resolved": False, "reason": "Current visual state is still uncertain"}
    return {"mode": "acknowledge", "resolved": False, "reason": "Colab requested human review"}
