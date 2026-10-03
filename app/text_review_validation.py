from __future__ import annotations

import io
import unicodedata
from typing import Any

from PIL import Image, ImageDraw


def orient_text_crop(data: bytes, mime: str, metadata: dict[str, Any]):
    """Give the model both upright candidates for a very tall sideways crop."""
    with Image.open(io.BytesIO(data)) as opened:
        image = opened.convert("RGB")
        if image.height <= image.width * 2.5:
            return data, mime, metadata
        # The crop is not enlarged to include any new source content. Neither
        # cardinal direction is assumed correct without reading the pixels.
        candidates = [image.rotate(angle, expand=True) for angle in (90, 270)]
        label_height = 24
        view = Image.new("RGB", (image.height + image.width, max((image.width + label_height) * 2, image.height + label_height)), "white")
        draw = ImageDraw.Draw(view)
        for i, candidate in enumerate(candidates):
            top = i * (image.width + label_height)
            draw.text((8, top + 5), f"Orientation {i + 1}: SAME target crop, rotated {90 if i == 0 else 270} degrees", fill="black")
            view.paste(candidate, (0, top + label_height))
        draw.text((image.height + 4, 5), "Original: SAME crop, 0 deg", fill="black")
        view.paste(image, (image.height, label_height))
        buffer = io.BytesIO()
        view.save(buffer, format="PNG")
        return buffer.getvalue(), "image/png", {**metadata, "orientation_candidates": [0, 90, 270], "model_image_width": view.width, "model_image_height": view.height}


def _same_text(left: str, right: str) -> bool:
    def normalized(text):
        return " ".join(unicodedata.normalize("NFC", text).split())
    return normalized(left) == normalized(right)


def validate_text_review(parsed: dict[str, Any], entry: dict[str, Any], *, anomaly: bool, before=None, after=None) -> dict[str, Any]:
    """Derive an advisory decision from a scoped transcription, not explanation."""
    from .stage2b import _scope_target_transcription
    result = dict(parsed)
    decision_key = "verdict" if anomaly else "recommendation"
    proposal_key = "corrected_text" if anomaly else "suggested_text"
    result["model_" + decision_key] = parsed.get(decision_key)
    result["model_reason"] = parsed.get("reason")
    transcription = str(parsed.get("source_transcription") or "").strip()
    result["source_transcription"] = transcription
    result[proposal_key] = ""
    if parsed.get("source_readable") is not True or not transcription or "[UNREADABLE]" in transcription:
        result[decision_key] = "NEEDS_HUMAN"
        result["confidence"] = None
        result["reason"] = "No readable source transcription was supplied. A fluent explanation cannot verify the garbled Docling text. Re-review the upright source crop or enter a human correction."
        result["source_validation"] = {"verified": False, "reason": "SOURCE_TRANSCRIPTION_MISSING_OR_UNREADABLE"}
        return result
    original = str(entry.get("original_text") or "")
    scope = _scope_target_transcription(transcription, original, before, after, source_type=str(entry.get("source_type") or "text"))
    scoped = str(scope.get("text") or "").strip()
    result["source_validation"] = {"verified": bool(scope.get("accepted")), "scope": scope}
    result["source_transcription"] = scoped or transcription
    if not scope.get("accepted"):
        result[decision_key] = "NEEDS_HUMAN"
        result[proposal_key] = scoped
        result["confidence"] = None
        result["reason"] = "The model supplied a source transcription, but its target scope is not verified. Compare the candidate with the PDF before accepting it. Scope checks: " + ", ".join(scope.get("reasons") or [])
    elif _same_text(scoped, original):
        result[decision_key] = "KEEP_ORIGINAL"
        result["reason"] = "The scoped source transcription matches the immutable Docling text. Keeping the original requires your source review."
    elif str(entry.get("proposed_text") or "").strip() and _same_text(scoped, str(entry["proposed_text"])):
        result[decision_key] = "USE_PRIMARY_PROPOSAL" if anomaly else "APPLY_PROPOSED"
        result[proposal_key] = scoped
        result["reason"] = "The scoped source transcription differs from Docling and matches the primary proposal. A human must approve the replacement."
    else:
        result[decision_key] = "REPLACE_TEXT" if anomaly else "EDIT_SUGGESTED"
        result[proposal_key] = scoped
        result["reason"] = "The scoped source transcription differs from immutable Docling text. Readable PDF pixels do not justify keeping a different garbled transcription. Review this candidate before saving a human override."
    if anomaly and result[decision_key] in {"REPLACE_TEXT", "USE_PRIMARY_PROPOSAL"}:
        confirmed = parsed.get("anomaly_types_confirmed")
        confirmed = list(confirmed) if isinstance(confirmed, list) else []
        if "SOURCE_TRANSCRIPTION_MISMATCH" not in confirmed:
            confirmed.append("SOURCE_TRANSCRIPTION_MISMATCH")
        result["anomaly_types_confirmed"] = confirmed
    return result
