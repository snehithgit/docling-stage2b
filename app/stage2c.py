from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter
import io


TECHNICAL_DIAGRAM_CATEGORIES = {
    "electrical_schematic", "wiring_diagram", "single_line_diagram", "ladder_logic",
    "block_diagram", "hydraulic_schematic", "pneumatic_schematic", "pid",
    "control_logic", "terminal_diagram", "timing_or_waveform", "mechanical_section",
    "exploded_view", "installation_layout", "safety_diagram",
}
TECHNICAL_IMAGE_CATEGORIES = TECHNICAL_DIAGRAM_CATEGORIES | {
    "technical_photo", "nameplate_or_data_plate", "table_or_schedule", "graph_or_plot",
    "map", "control_panel", "other_technical",
}
DECORATIVE_IMAGE_CATEGORIES = {"decorative_photo", "logo", "signature_or_stamp", "cover_art"}
ALL_DIAGRAM_CATEGORIES = TECHNICAL_IMAGE_CATEGORIES | DECORATIVE_IMAGE_CATEGORIES | {"unknown"}
STAGE2C_RULE_VERSION = "stage2c-structural-v4"

# Pattern classes, not book/manufacturer-specific values.
_NUM = r"[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)"
_UNIT = (
    r"(?:%|°\s*[CFK]|(?:[pnumkMGTµμ]?)(?:V|A|W|Wh|J|N|Nm|Pa|Hz|Ω|Ohm|F|H|S|m|g|s|K)"
    r"|bar|mbar|rpm|r/min|L|l|min|h|mm|cm|km|mA|kA|kW|MW|kWh|MPa|kPa|psi|m/s|m\^2|m\^3)"
)
TECHNICAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("unit_value", re.compile(rf"(?<!\w){_NUM}\s*{_UNIT}(?!\w)", re.I)),
    ("tolerance", re.compile(rf"(?:±\s*{_NUM}|{_NUM}\s*(?:to|[-–—])\s*{_NUM})", re.I)),
    ("standard_or_spec", re.compile(r"\b[A-Z]{2,8}[ -]?\d{2,6}(?:[-/:.]\d+[A-Z]?)?\b")),
    ("identifier", re.compile(r"\b(?=[A-Z0-9._/-]{3,}\b)(?=[A-Z0-9._/-]*[A-Z])(?=[A-Z0-9._/-]*\d)[A-Z0-9]+(?:[._/-][A-Z0-9]+)*\b", re.I)),
    ("terminal_or_wire", re.compile(r"\b(?:[A-Z]{1,4}\d{1,5}|\d{1,5}[A-Z]{1,3})(?:[:./-][A-Z0-9]+)*\b", re.I)),
    ("rating", re.compile(r"\b(?:IP|IK)\s*\d{2,3}[A-Z]?\b", re.I)),
    ("formula_like", re.compile(r"\b[A-Za-z][A-Za-z0-9_]*\s*=\s*[^\s,;]{1,40}")),
)

CRITICAL_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:[A-Z]{1,4}\d{1,5}|\d{1,5}[A-Z]{1,3})(?:[:./-][A-Z0-9]+)*\b", re.I),
    re.compile(rf"(?<!\w){_NUM}\s*{_UNIT}(?!\w)", re.I),
    re.compile(rf"(?:±\s*{_NUM}|{_NUM}\s*(?:to|[-–—])\s*{_NUM})", re.I),
    re.compile(r"\b[A-Z]{2,8}[ -]?\d{2,6}(?:[-/:.]\d+[A-Z]?)?\b"),
    re.compile(r"\b(?=[A-Z0-9._/-]{3,}\b)(?=[A-Z0-9._/-]*[A-Z])(?=[A-Z0-9._/-]*\d)[A-Z0-9]+(?:[._/-][A-Z0-9]+)*\b", re.I),
    re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?(?!\w)"),
)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


def _compact_token(value: str) -> str:
    return re.sub(r"\s+", "", _norm(value)).casefold()


def technical_format_profile(text: str) -> dict[str, Any]:
    text = _norm(text)
    spans: list[tuple[int, int, str, str]] = []
    classes: set[str] = set()
    for name, pattern in TECHNICAL_PATTERNS:
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end(), name, match.group(0)))
            classes.add(name)
    covered: set[int] = set()
    for start, end, _, _ in spans:
        covered.update(range(start, end))
    nonspace = max(1, sum(1 for ch in text if not ch.isspace()))
    covered_nonspace = sum(1 for idx in covered if idx < len(text) and not text[idx].isspace())
    coverage = covered_nonspace / nonspace
    return {
        "is_technical_format": bool(spans),
        "dominant": bool(spans) and coverage >= 0.60,
        "coverage": round(coverage, 4),
        "classes": sorted(classes),
        "matches": [{"class": name, "text": value[:120]} for _, _, name, value in spans[:20]],
    }


def _looks_like_identifier(token: str) -> bool:
    token = token.strip("()[]{}.,;:")
    if not token:
        return False
    if re.fullmatch(r"[A-Z]{2,8}", token):
        return True
    if re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9._/+%-]{2,}", token):
        return True
    return False


def garble_profile(text: str) -> dict[str, Any]:
    """Cheap book-agnostic OCR structure score.

    Deliberately avoids a raw no-vowel rule: PLC/RPM/MCC/PT100 and similar
    technical identifiers are valid. Signals are character/fragment structure,
    not vocabulary or manufacturer knowledge.
    """
    value = _norm(text).strip()
    if not value:
        return {"score": 1.0, "confirmed": True, "structurally_normal": False, "signals": {"empty": 1.0}}
    tokens = re.findall(r"\S+", value)
    lexical = [t.strip("()[]{}.,;:") for t in tokens if t.strip("()[]{}.,;:")]
    ordinary = [t for t in lexical if not _looks_like_identifier(t)]

    replacement = sum(value.count(ch) for ch in ("�", "□", "¤")) / max(1, len(value))
    punct_runs = len(re.findall(r"[^\w\s]{4,}", value)) / max(1, len(tokens))
    spaced_chars = len(re.findall(r"(?:\b[A-Za-z0-9]\s+){3,}[A-Za-z0-9]\b", value)) / max(1, len(tokens))
    tiny = sum(1 for t in ordinary if len(t) <= 2 and t.isalpha()) / max(1, len(ordinary))
    mixed_fragments = sum(
        1 for t in ordinary
        if len(t) >= 4 and len(re.findall(r"[^A-Za-z0-9._/+%°µμΩΩ-]", t)) >= 2
    ) / max(1, len(ordinary))

    normalized_fragments = [re.sub(r"[^A-Za-z0-9]", "", t).casefold() for t in ordinary]
    normalized_fragments = [t for t in normalized_fragments if 1 <= len(t) <= 4]
    counts: dict[str, int] = {}
    for token in normalized_fragments:
        counts[token] = counts.get(token, 0) + 1
    repeated_broken = sum(max(0, n - 2) for n in counts.values()) / max(1, len(tokens))

    # Excessive transitions between alpha/digit/punctuation inside ordinary tokens.
    transition_total = 0
    transition_tokens = 0
    for token in ordinary:
        if len(token) < 5:
            continue
        kinds = []
        for ch in token:
            if ch.isalpha():
                kind = "a"
            elif ch.isdigit():
                kind = "d"
            else:
                kind = "p"
            if not kinds or kinds[-1] != kind:
                kinds.append(kind)
        if len(kinds) >= 4 and not _looks_like_identifier(token):
            transition_total += min(1.0, (len(kinds) - 3) / 4)
        transition_tokens += 1
    transitions = transition_total / max(1, transition_tokens)

    signals = {
        "replacement_chars": round(min(1.0, replacement * 20), 4),
        "punctuation_runs": round(min(1.0, punct_runs * 2), 4),
        "spaced_character_fragments": round(min(1.0, spaced_chars * 2), 4),
        "tiny_token_ratio": round(tiny, 4),
        "mixed_fragment_ratio": round(mixed_fragments, 4),
        "repeated_broken_fragments": round(min(1.0, repeated_broken * 2), 4),
        "abnormal_token_transitions": round(transitions, 4),
    }
    score = (
        0.18 * signals["replacement_chars"]
        + 0.10 * signals["punctuation_runs"]
        + 0.15 * signals["spaced_character_fragments"]
        + 0.34 * signals["tiny_token_ratio"]
        + 0.08 * signals["mixed_fragment_ratio"]
        + 0.10 * signals["repeated_broken_fragments"]
        + 0.05 * signals["abnormal_token_transitions"]
    )
    score = min(1.0, score)
    return {
        "score": round(score, 4),
        "confirmed": score >= 0.12,
        "structurally_normal": score <= 0.08,
        "signals": signals,
        "token_count": len(tokens),
    }


def analyze_text_structure(text: str) -> dict[str, Any]:
    return {
        "technical_format": technical_format_profile(text),
        "garble": garble_profile(text),
    }


def extract_critical_tokens(text: str) -> set[str]:
    found: set[str] = set()
    for pattern in CRITICAL_TOKEN_PATTERNS:
        for match in pattern.finditer(_norm(text)):
            token = _compact_token(match.group(0))
            if token:
                found.add(token)
    return found


def _critical_sequence(text: str) -> list[str]:
    matches: dict[tuple[int, int], str] = {}
    for pattern in CRITICAL_TOKEN_PATTERNS:
        for match in pattern.finditer(_norm(text)):
            matches[(match.start(), match.end())] = _compact_token(match.group(0))
    # Keep the longest match at each position; nested bare-number matches
    # must not make harmless spacing changes (24 V -> 24V) look destructive.
    sequence = []
    end = -1
    for (start, stop), token in sorted(matches.items(), key=lambda item: (item[0][0], -item[0][1])):
        if start >= end:
            sequence.append(token)
            end = stop
    return sequence


def source_transcription_safety_profile(original: str, proposed: str) -> dict[str, Any]:
    """Deterministic final safety profile for direct source transcriptions.

    A vision model may faithfully read *some* pixels while the crop itself is
    localized to the wrong nearby label.  This guard therefore does not decide
    which wording is semantically correct; it only asks whether the returned
    transcription still aligns enough with the immutable Docling target to be
    safe for automatic replacement.  High-risk numeric/identifier changes need
    stronger alignment than ordinary spelling cleanup.
    """
    original_norm = " ".join(_norm(original).split()).casefold()
    proposed_norm = " ".join(_norm(proposed).split()).casefold()
    sequence_similarity = (
        SequenceMatcher(None, original_norm, proposed_norm, autojunk=False).ratio()
        if original_norm and proposed_norm else 0.0
    )

    def token_set(value: str) -> set[str]:
        return {
            token.casefold()
            for token in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", _norm(value))
            if len(token) >= 2
        }

    source_tokens = token_set(original)
    proposed_tokens = token_set(proposed)
    shared = source_tokens & proposed_tokens
    target_token_recall = len(shared) / max(1, len(source_tokens)) if source_tokens else 0.0
    target_token_precision = len(shared) / max(1, len(proposed_tokens)) if proposed_tokens else 0.0
    length_similarity = (
        min(len(original_norm), len(proposed_norm)) / max(1, max(len(original_norm), len(proposed_norm)))
        if (original_norm or proposed_norm) else 0.0
    )

    source_critical = _critical_sequence(original)
    proposed_critical = _critical_sequence(proposed)
    critical_changed = source_critical != proposed_critical

    reasons: list[str] = []
    if original_norm and proposed_norm and target_token_recall == 0.0 and sequence_similarity < 0.50:
        reasons.append("WRONG_REGION_LOW_OVERLAP")

    if critical_changed and (
        sequence_similarity < 0.72
        or target_token_recall < 0.35
        or length_similarity < 0.60
    ):
        reasons.append("HIGH_RISK_TECHNICAL_TOKEN_CHANGE_LOW_ALIGNMENT")

    return {
        "accepted": not reasons,
        "reasons": reasons,
        "sequence_similarity": round(sequence_similarity, 4),
        "target_token_recall": round(target_token_recall, 4),
        "target_token_precision": round(target_token_precision, 4),
        "length_similarity": round(length_similarity, 4),
        "critical_tokens_changed": critical_changed,
        "source_critical_tokens": source_critical[:40],
        "proposed_critical_tokens": proposed_critical[:40],
    }


def correction_fidelity(original: str, proposed: str, *, min_similarity: float = 0.45, min_length_ratio: float = 0.50, max_length_ratio: float = 1.80) -> dict[str, Any]:
    original = _norm(original).strip()
    proposed = _norm(proposed).strip()
    if not proposed:
        return {"accepted": False, "reasons": ["EMPTY_CORRECTION"]}
    original_crit = extract_critical_tokens(original)
    proposed_crit = extract_critical_tokens(proposed.replace("[UNREADABLE]", ""))
    new_critical = sorted(proposed_crit - original_crit)
    similarity = SequenceMatcher(None, original.casefold(), proposed.casefold()).ratio()
    length_ratio = len(proposed) / max(1, len(original))

    # Require most surviving alphanumeric characters from the source to remain
    # represented in order. This permits OCR cleanup without wholesale rewrite.
    orig_alnum = "".join(ch.casefold() for ch in original if ch.isalnum())
    prop_alnum = "".join(ch.casefold() for ch in proposed if ch.isalnum())
    preservation = SequenceMatcher(None, orig_alnum, prop_alnum).ratio() if orig_alnum else 1.0

    reasons: list[str] = []
    if new_critical:
        reasons.append("UNSUPPORTED_NEW_TECHNICAL_TOKEN")
    if _critical_sequence(original) != _critical_sequence(proposed.replace("[UNREADABLE]", "")):
        reasons.append("TECHNICAL_TOKENS_NOT_PRESERVED")
    if similarity < min_similarity:
        reasons.append("EDIT_DISTANCE_TOO_LARGE")
    if not min_length_ratio <= length_ratio <= max_length_ratio:
        reasons.append("LENGTH_RATIO_OUT_OF_RANGE")
    if preservation < 0.50:
        reasons.append("SOURCE_CHARACTERS_NOT_PRESERVED")
    return {
        "accepted": not reasons,
        "reasons": reasons,
        "similarity": round(similarity, 4),
        "length_ratio": round(length_ratio, 4),
        "source_preservation": round(preservation, 4),
        "new_critical_tokens": new_critical,
        "source_critical_tokens": sorted(original_crit),
        "proposed_critical_tokens": sorted(proposed_crit),
    }


def image_structure_evidence(image_bytes: bytes) -> dict[str, Any]:
    """Cheap PIL-only structural corroborator for line-dominant diagrams.

    This intentionally avoids raw row/column ink occupancy: bold logos and
    filled artwork can have high occupancy without containing schematic line
    structure. Instead we measure how much dark foreground disappears after
    one/two binary erosion-equivalent passes (PIL MaxFilter on dark strokes).
    Thin wires/lines vanish quickly; bold filled glyphs/shapes retain a dark
    core. Spatial spread prevents a small patch of thin text from looking like
    a page-wide schematic.
    """
    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = opened.convert("L")
        width, height = image.size
        scale = min(1.0, 256 / max(width, height, 1))
        if scale < 1.0:
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS,
            )

        pixels = max(1, image.width * image.height)
        # Normalize to a binary dark-foreground mask. Values below 160 are
        # treated as ink; antialiasing is intentionally discarded here.
        binary = image.point(lambda px: 0 if px < 160 else 255).convert("L")

        def dark_count(img: Image.Image) -> int:
            hist = img.histogram()
            return int(sum(hist[:128]))

        dark0 = dark_count(binary)
        dark_density = dark0 / pixels
        eroded1 = binary.filter(ImageFilter.MaxFilter(3))
        eroded2 = eroded1.filter(ImageFilter.MaxFilter(3))
        dark1 = dark_count(eroded1)
        dark2 = dark_count(eroded2)

        # Fraction removed after one erosion: high for thin strokes.
        thin_stroke_fraction = max(0.0, min(1.0, 1.0 - (dark1 / max(1, dark0))))
        # Fraction surviving two erosions: high for bold/filled shapes.
        filled_core_fraction = max(0.0, min(1.0, dark2 / max(1, dark0)))

        edges = image.filter(ImageFilter.FIND_EDGES)
        edge_hist = edges.histogram()
        edge_pixels = sum(edge_hist[80:])
        edge_density = edge_pixels / pixels

        # Require ink to be distributed over the image, not just a centered
        # wordmark/signature. A 4x4 grid keeps this cheap on the N150.
        grid = 4
        active_cells = 0
        for gy in range(grid):
            y0 = gy * binary.height // grid
            y1 = (gy + 1) * binary.height // grid
            for gx in range(grid):
                x0 = gx * binary.width // grid
                x1 = (gx + 1) * binary.width // grid
                area = max(1, (x1 - x0) * (y1 - y0))
                cell_dark = 0
                for y in range(y0, y1):
                    for x in range(x0, x1):
                        if binary.getpixel((x, y)) < 128:
                            cell_dark += 1
                if cell_dark / area >= 0.003:
                    active_cells += 1
        spatial_spread = active_cells / float(grid * grid)

        # Positive evidence: thin strokes, edges, and spatial distribution.
        # Negative evidence: a thick filled core. The explicit guards below
        # are more important than the scalar score and prevent logo-like fills
        # from corroborating a model-generated schematic category.
        diagram_score = (
            0.50 * thin_stroke_fraction
            + 0.20 * min(1.0, edge_density / 0.16)
            + 0.30 * spatial_spread
            - 0.35 * filled_core_fraction
        )
        diagram_score = max(0.0, min(1.0, diagram_score))
        diagram_like = bool(
            dark_density >= 0.002
            and thin_stroke_fraction >= 0.45
            and filled_core_fraction <= 0.35
            and spatial_spread >= 0.35
            and diagram_score >= 0.42
        )
        return {
            "width": width,
            "height": height,
            "dark_density": round(dark_density, 4),
            "edge_density": round(edge_density, 4),
            "thin_stroke_fraction": round(thin_stroke_fraction, 4),
            "filled_core_fraction": round(filled_core_fraction, 4),
            "spatial_spread": round(spatial_spread, 4),
            "diagram_score": round(diagram_score, 4),
            "diagram_like": diagram_like,
            "method": "binary_erosion_thin_stroke_v2",
        }


def vision_enrichment_status(parsed: dict[str, Any]) -> tuple[str, str]:
    """Independent Stage 2C policy for one vision-classification result."""
    verdict = str(parsed.get("verdict") or "UNCERTAIN")
    structure = parsed.get("structural_image_evidence")
    diagram_like = bool(structure.get("diagram_like")) if isinstance(structure, dict) else False
    if verdict == "TECHNICAL_USEFUL":
        return "applied", "TECHNICAL_VISUAL_ENRICHMENT"
    if verdict == "DECORATIVE_OR_LOW_VALUE" and diagram_like:
        return "pending", "VISION_DIAGRAM_CONFLICT_REVIEW"
    if verdict == "DECORATIVE_OR_LOW_VALUE":
        return "excluded", "DECORATIVE_OR_LOW_VALUE"
    return "pending", "VISION_UNCERTAIN"


def source_sha256(value: str | bytes) -> str:
    data = value.encode("utf-8", errors="replace") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)




def apply_human_correction_to_entry(entry: dict[str, Any], *, text: str, action: str) -> dict[str, Any]:
    """Mark one ledger entry as explicitly human-reviewed.

    Human provenance is authoritative over automatic verifier/corrector metadata.
    The previous automatic status fields are retained only inside ``human_review``
    for audit, while the live status/reason fields become HUMAN_VERIFIED.
    """
    if action not in {"apply", "reject", "propose"}:
        raise ValueError("Unsupported human correction action")
    before_text = str(entry.get("original_text") or "")
    after_text = str(text).strip()
    if not after_text:
        raise ValueError("Human correction text cannot be empty")
    previous_status = entry.get("status")
    previous_reason = entry.get("reason")
    previous_status_reason = entry.get("status_reason")
    entry["proposed_text"] = after_text
    entry["status"] = {"apply": "applied", "reject": "rejected", "propose": "proposed"}[action]
    entry["reason"] = "HUMAN_VERIFIED"
    entry["status_reason"] = "HUMAN_VERIFIED"
    entry["human_verified"] = True
    entry["human_review"] = {
        "action": action,
        "before_text": before_text,
        "after_text": after_text,
        "changed": before_text != after_text,
        "previous_status": previous_status,
        "previous_reason": previous_reason,
        "previous_status_reason": previous_status_reason,
        "saved_at_epoch": time.time(),
    }
    return entry




def _normalized_overlay_text(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def unsafe_automatic_source_transcription(entry: dict[str, Any]) -> bool:
    """Return True when an automatic source transcription is incomplete.

    Human edits are never second-guessed here. This guard exists to stop an
    older/truncated vision response from shortening otherwise valid immutable
    Docling text in a derived overlay.
    """
    if entry.get("human_verified") or entry.get("entry_type") != "text_correction":
        return False
    if str(entry.get("status") or "").lower() != "applied":
        return False
    if str(entry.get("status_reason") or "") not in {
        "SOURCE_IMAGE_TARGET_RECONSTRUCTION", "GROQ_VISION_DIRECT_TRANSCRIPTION"
    }:
        return False
    reconstruction = entry.get("source_reconstruction")
    if isinstance(reconstruction, dict):
        if reconstruction.get("truncated") or str(reconstruction.get("finish_reason") or "").lower() == "length":
            return True
    guard = entry.get("scope_guard")
    if isinstance(guard, dict):
        reasons = {str(x) for x in (guard.get("reasons") or [])}
        if {
            "PARTIAL_TARGET_PREFIX_OR_SUFFIX",
            "EXCESSIVE_TARGET_CONTRACTION",
            "WRONG_REGION_LOW_OVERLAP",
            "HIGH_RISK_TECHNICAL_TOKEN_CHANGE_LOW_ALIGNMENT",
            "NOVEL_TOKEN_DUPLICATION",
        } & reasons:
            return True
    original_raw = str(entry.get("original_text") or "")
    proposed_raw = str(entry.get("proposed_text") or "")
    # Independent Stage 2C safety boundary: do not trust an upstream ``applied``
    # flag when the persisted source/target alignment is clearly contradictory.
    safety = source_transcription_safety_profile(original_raw, proposed_raw)
    if not safety.get("accepted"):
        return True
    original = _normalized_overlay_text(original_raw)
    proposed = _normalized_overlay_text(proposed_raw)
    if len(original) >= 80 and proposed:
        ratio = len(proposed) / max(1, len(original))
        if ratio < 0.90 and (original.startswith(proposed) or original.endswith(proposed)):
            return True
    return False


def _demote_unsafe_automatic_source_transcription(entry: dict[str, Any]) -> bool:
    if not unsafe_automatic_source_transcription(entry):
        return False
    profile = source_transcription_safety_profile(
        str(entry.get("original_text") or ""), str(entry.get("proposed_text") or "")
    )
    guard_reasons = []
    if isinstance(entry.get("scope_guard"), dict):
        guard_reasons = [str(x) for x in (entry["scope_guard"].get("reasons") or [])]
    reason = (profile.get("reasons") or guard_reasons or ["INCOMPLETE_SOURCE_TRANSCRIPTION"])[0]
    entry["safety_revalidation"] = {
        "previous_status": entry.get("status"),
        "previous_status_reason": entry.get("status_reason"),
        "previous_proposed_text": entry.get("proposed_text"),
        "revalidated_at_epoch": time.time(),
        "model_rerun": False,
        "reason": reason,
        "alignment_profile": profile,
    }
    entry["status"] = "pending"
    entry["status_reason"] = f"{reason}_KEEP_ORIGINAL"
    entry["proposed_text"] = None
    return True


def normalize_human_verified_ledger(result_dir: Path) -> int:
    """Normalize legacy human-reviewed entries without changing their decision/text.

    Older builds could leave a stale automatic ``status_reason`` on an entry
    after a person had already applied/rejected/proposed it.  This migration is
    safe and idempotent: the old live reason is copied into ``human_review``
    when missing, then the authoritative live reason fields become
    ``HUMAN_VERIFIED``.  Overlays are rebuilt from the unchanged decisions.
    """
    path = result_dir / "correction_ledger.json"
    try:
        ledger = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return 0
    entries = list(ledger.get("entries") or [])
    changed = 0
    for entry in entries:
        if entry.get("human_verified"):
            live_reason = entry.get("reason")
            live_status_reason = entry.get("status_reason")
            review = entry.get("human_review")
            if not isinstance(review, dict):
                review = {}
                entry["human_review"] = review
            if live_reason and live_reason != "HUMAN_VERIFIED" and "previous_reason" not in review:
                review["previous_reason"] = live_reason
            if live_status_reason and live_status_reason != "HUMAN_VERIFIED" and "previous_status_reason" not in review:
                review["previous_status_reason"] = live_status_reason
            if live_reason != "HUMAN_VERIFIED" or live_status_reason != "HUMAN_VERIFIED":
                entry["reason"] = "HUMAN_VERIFIED"
                entry["status_reason"] = "HUMAN_VERIFIED"
                changed += 1
            continue
        if _demote_unsafe_automatic_source_transcription(entry):
            changed += 1
    if not changed:
        return 0
    ledger["entries"] = entries
    ledger["updated_at_epoch"] = time.time()
    _atomic_json(path, ledger)
    rebuild_chunk_overlays(result_dir, entries)
    return changed



def human_review_summary(result_dir: Path, require_human: bool = True) -> dict[str, Any]:
    """Return manual-audit and unresolved counts for current text routes.

    Automatic source-image reconstructions with ``status=applied`` are already
    downstream corrections and must never appear as waiting for a Save click.
    When human review is optional, only genuinely unresolved ``pending`` or
    ``proposed`` entries are surfaced as automation-unresolved. Human review
    remains available as an audit/manual override for every current text entry.
    """
    path = result_dir / "correction_ledger.json"
    try:
        ledger = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError, TypeError):
        ledger = {}
    entries = [
        item for item in (ledger.get("entries") or [])
        if item.get("status") != "superseded" and item.get("entry_type") == "text_correction"
    ]
    review_verdicts = {"LIKELY_CORRUPT", "UNCERTAIN"}
    candidates = [
        item for item in entries
        if str(item.get("verification_verdict") or "").upper() in review_verdicts
    ]
    reviewed = [item for item in candidates if bool(item.get("human_verified"))]
    unreviewed = [item for item in candidates if not bool(item.get("human_verified"))]
    unresolved_statuses = {"pending", "proposed"}
    automation_unresolved_entries = [
        item for item in unreviewed if str(item.get("status") or "").lower() in unresolved_statuses
    ]
    required = unreviewed if require_human else automation_unresolved_entries
    uncertain_required = [
        item for item in required
        if str(item.get("verification_verdict") or "").upper() == "UNCERTAIN"
    ]
    corrupt_required = [
        item for item in required
        if str(item.get("verification_verdict") or "").upper() == "LIKELY_CORRUPT"
    ]
    auto_applied = [
        item for item in unreviewed if str(item.get("status") or "").lower() == "applied"
    ]
    return {
        "total_text_corrections": len(entries),
        "review_candidates": len(candidates),
        "human_reviewed": len(reviewed),
        "auto_applied": len(auto_applied),
        "review_required": len(required),
        "corrupt_review_required": len(corrupt_required),
        "uncertain_review_required": len(uncertain_required),
        "review_complete": len(required) == 0,
        "review_mandatory": bool(require_human),
        "blocking_review_required": len(required) if require_human else 0,
        "automation_unresolved": len(automation_unresolved_entries),
        "required_entry_ids": [str(item.get("entry_id")) for item in required],
    }


def revalidate_unreviewed_text_entries(result_dir: Path, updates: dict[str, dict[str, Any]]) -> int:
    """Apply deterministic verifier-policy revalidation without model calls.

    Only unreviewed text entries may change. Human decisions remain authoritative.
    This is used when validator hardening can reinterpret persisted raw Pi5
    responses without rerunning the device.
    """
    path = result_dir / "correction_ledger.json"
    try:
        ledger = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError, TypeError):
        ledger = {}
    entries = list(ledger.get("entries") or [])
    changed = 0
    for entry in entries:
        if entry.get("entry_type") != "text_correction" or entry.get("human_verified"):
            continue
        update = updates.get(str(entry.get("entry_id")))
        if not update:
            continue
        old_verdict = str(entry.get("verification_verdict") or "")
        new_verdict = str(update.get("verdict") or "UNCERTAIN")
        entry["verification_verdict"] = new_verdict
        entry["verification"] = update.get("verification") or entry.get("verification")
        entry["policy_revalidation"] = {
            "previous_verdict": old_verdict,
            "new_verdict": new_verdict,
            "revalidated_at_epoch": time.time(),
            "model_rerun": False,
        }
        if new_verdict == "LIKELY_OK":
            entry["status"] = "excluded"
            entry["status_reason"] = "REVALIDATED_LIKELY_OK"
            entry["proposed_text"] = None
        elif new_verdict == "UNCERTAIN":
            if entry.get("status") != "applied":
                entry["status"] = "pending"
            entry["status_reason"] = "REVALIDATED_UNCERTAIN_HUMAN_REVIEW"
        else:
            if entry.get("status") not in {"applied", "proposed"}:
                entry["status"] = "pending"
            entry["status_reason"] = "REVALIDATED_LIKELY_CORRUPT_HUMAN_REVIEW"
        changed += 1
    if changed:
        ledger["entries"] = entries
        ledger["updated_at_epoch"] = time.time()
        _atomic_json(path, ledger)
        rebuild_chunk_overlays(result_dir, entries)
    return changed


def upsert_ledger_entry(result_dir: Path, source_zip_sha256: str, entry: dict[str, Any]) -> None:
    """Upsert by stable entry_id and rebuild chunk overlays atomically."""
    path = result_dir / "correction_ledger.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        current = {}
    entries = list(current.get("entries") or [])
    entry_id = str(entry["entry_id"])
    existing_entry = next(
        (item for item in entries if str(item.get("entry_id")) == entry_id),
        None,
    )
    # Second safety boundary: even if an upstream verifier accidentally labels
    # an incomplete direct transcription as applied, never persist it as an
    # active overlay. Human-verified text remains authoritative.
    if not entry.get("human_verified"):
        _demote_unsafe_automatic_source_transcription(entry)
    # Human review is authoritative. Automatic Stage 2B/2C processing must
    # never replace a correction that a user already verified, even if the
    # verifier route is retried or Stage 2C is rebuilt later. A later human
    # action may still update the same entry because it also carries
    # human_verified=True.
    if existing_entry and existing_entry.get("human_verified") and not entry.get("human_verified"):
        entry = existing_entry
    elif existing_entry:
        # Preserve audit-only manual crossover history when an automatic
        # correction suggestion refreshes the same unreviewed text entry.
        # Human-reviewed entries are already protected by the branch above.
        if existing_entry.get("manual_crosschecks") and not entry.get("manual_crosschecks"):
            entry["manual_crosschecks"] = list(existing_entry.get("manual_crosschecks") or [])
    entries = [item for item in entries if str(item.get("entry_id")) != entry_id]
    entries.append(entry)
    entries.sort(key=lambda item: (str(item.get("entry_type")), str(item.get("route_id")), str(item.get("entry_id"))))
    ledger = {
        "schema": "docling-correction-ledger/v2",
        "source_zip_sha256": source_zip_sha256 or current.get("source_zip_sha256") or "",
        "policy": (
            "Raw Docling output is immutable. Text corrections may not invent technical values/identifiers. "
            "Vision descriptions are enrichment, not extracted source facts. Only applied entries are exposed as chunk overlays."
        ),
        "rule_version": STAGE2C_RULE_VERSION,
        "updated_at_epoch": time.time(),
        "entries": entries,
    }
    _atomic_json(path, ledger)
    rebuild_chunk_overlays(result_dir, entries)


def rebuild_chunk_overlays(result_dir: Path, entries: list[dict[str, Any]]) -> None:
    """Write only applied patches/enrichment; original Docling data is untouched."""
    overlays = []
    for entry in entries:
        if entry.get("status") != "applied":
            continue
        if unsafe_automatic_source_transcription(entry):
            continue
        if entry.get("entry_type") == "text_correction":
            overlays.append({
                "entry_id": entry.get("entry_id"),
                "entry_type": "text_correction",
                "route_id": entry.get("route_id"),
                "page": entry.get("page"),
                "source_index": entry.get("source_index"),
                "source_type": entry.get("source_type") or "text",
                "table_index": entry.get("table_index"),
                "cell_index": entry.get("cell_index"),
                "text": entry.get("proposed_text"),
                "provenance": (
                    "human_verified_manual_correction" if entry.get("human_verified")
                    else "source_image_direct_transcription" if entry.get("status_reason") in {
                        "GROQ_VISION_DIRECT_TRANSCRIPTION", "SOURCE_IMAGE_TARGET_RECONSTRUCTION"
                    }
                    else "text_corrector_verified"
                ),
                "human_verified": bool(entry.get("human_verified")),
            })
        elif entry.get("entry_type") == "vision_enrichment":
            overlays.append({
                "entry_id": entry.get("entry_id"),
                "entry_type": "vision_enrichment",
                "route_id": entry.get("route_id"),
                "page": entry.get("page"),
                "source_index": entry.get("source_index"),
                "diagram_category": entry.get("diagram_category"),
                "visible_text": entry.get("visible_text") or [],
                "generated_summary": entry.get("generated_summary") or "",
                "unresolved": entry.get("unresolved", True),
                "crop_coverage": entry.get("crop_coverage"),
                "visible_objects": entry.get("visible_objects") or [],
                "provenance": "selected_vision_processor_generated_enrichment",
            })
    path = result_dir / "chunk_overlays.jsonl"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in overlays), encoding="utf-8")
    tmp.replace(path)
