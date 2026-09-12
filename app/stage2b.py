from __future__ import annotations

import asyncio
import difflib
import hashlib
import io
import json
import logging
import os
import re
import time
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
import fitz

from .archive import select_docling_document
from .verifier_checkpoint import CheckpointVerifier
from .events import EventBroker
from .groq_quota import CloudQuotaPausedError, GroqQuotaGuard
from .postprocess_store import PostprocessStore
from .stage2b_store import Stage2BStore
from .verifier_clients import GroqStructuredVerifier, GroqVisionVerifier, OpenAICompatibleVerifier
from .stage2c import (
    ALL_DIAGRAM_CATEGORIES, DECORATIVE_IMAGE_CATEGORIES, TECHNICAL_DIAGRAM_CATEGORIES,
    TECHNICAL_IMAGE_CATEGORIES, analyze_text_structure, correction_fidelity,
    STAGE2C_RULE_VERSION, human_review_summary, image_structure_evidence, normalize_human_verified_ledger, revalidate_unreviewed_text_entries, source_sha256, source_transcription_safety_profile, upsert_ledger_entry, vision_enrichment_status,
)


PI5_VERDICTS = {"LIKELY_CORRUPT", "LIKELY_OK", "UNCERTAIN"}
VISION_VERDICTS = {"TECHNICAL_USEFUL", "DECORATIVE_OR_LOW_VALUE", "UNCERTAIN"}
PI5_EVIDENCE_MAX_CHARS = 120
logger = logging.getLogger("uvicorn.error")

TEXT_TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["LIKELY_CORRUPT", "LIKELY_OK", "UNCERTAIN"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason_code": {"type": "string", "enum": ["OCR_GARBLE", "TECHNICAL_FORMAT", "CLEAN_PROSE", "AMBIGUOUS"]},
        "evidence": {"type": "string"},
    },
    "required": ["verdict", "confidence", "reason_code", "evidence"],
    "additionalProperties": False,
}

TEXT_CORRECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "corrected_text": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["corrected_text", "confidence"],
    "additionalProperties": False,
}

TEXT_CORRECTION_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["SUPPORTED", "REJECT", "UNCERTAIN"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "string"},
    },
    "required": ["verdict", "confidence", "evidence"],
    "additionalProperties": False,
}

CROSSCHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["SUPPORTED", "CONTRADICTED", "NOT_ENOUGH_EVIDENCE"]},
        "evidence": {"type": "string"},
    },
    "required": ["verdict", "evidence"],
    "additionalProperties": False,
}


def _is_cloud_text_client(client: Any) -> bool:
    return bool(getattr(client, "supports_strict_json_schema", False))


async def _chat_text_json(
    client: Any,
    system: str,
    user: str,
    *,
    model: str | None,
    max_tokens: int,
    schema_name: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"model": model, "max_tokens": int(max_tokens)}
    if _is_cloud_text_client(client):
        kwargs.update({"schema_name": schema_name, "schema": schema})
    return await client.chat_text(system, user, **kwargs)


def _docling_page_text(doc: dict[str, Any], page: int | None, max_chars: int = 5000) -> str:
    if page is None:
        return ""
    parts: list[str] = []
    total = 0
    for item in doc.get("texts") or []:
        if _page_of(item) != int(page):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        piece = text[:remaining]
        parts.append(piece)
        total += len(piece) + 1
    return "\n".join(parts)


class VisionParseError(ValueError):
    def __init__(self, message: str, attempts: list[dict[str, Any]]):
        super().__init__(message)
        self.attempts = attempts


def _json_from_model_response(response: dict[str, Any]) -> dict[str, Any]:
    """Extract the first JSON object from an OpenAI-compatible chat response.

    Small local models occasionally wrap JSON in markdown or a <think> block.
    We keep the raw response for audit and parse only the first balanced object.
    """
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Verifier response does not contain assistant content") from exc
    if isinstance(content, list):
        content = "\n".join(str(item.get("text") or "") if isinstance(item, dict) else str(item) for item in content)
    text = str(content or "").strip()
    if not text:
        raise ValueError("Verifier returned empty content")
    # Direct JSON first.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # Balanced object extraction tolerates markdown fences and think text.
    start = text.find("{")
    if start < 0:
        raise ValueError("Verifier did not return JSON")
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                obj = json.loads(text[start:index + 1])
                if not isinstance(obj, dict):
                    raise ValueError("Verifier JSON root must be an object")
                return obj
    raise ValueError("Verifier returned incomplete JSON")


def _load_docling_document_fast(path: Path) -> dict[str, Any]:
    """Read Docling JSON without repeating Stage 2A's full CRC/integrity scan."""
    if not zipfile.is_zipfile(path):
        raise ValueError("Converted output is not a ZIP archive")
    with zipfile.ZipFile(path) as archive:
        document, _ = select_docling_document(archive)
    return document


def _page_of(item: dict[str, Any]) -> int | None:
    prov = item.get("prov") or []
    if prov and isinstance(prov[0], dict):
        value = prov[0].get("page_no")
        return int(value) if isinstance(value, (int, float)) else None
    return None


def _text_context_parts(
    doc: dict[str, Any], text_index: int, page: int | None, before_count: int = 3, after_count: int = 3
) -> tuple[str, list[str], list[str]]:
    """Return target text plus same-page anchors before/after it in Docling order.

    The anchors are location cues only.  The vision processor must read the
    source page and reconstruct the text physically between them; it must never
    judge or rewrite the anchors themselves.
    """
    texts = doc.get("texts") or []
    if text_index < 0 or text_index >= len(texts):
        raise IndexError(f"Text index {text_index} is outside the Docling document")
    suspect = str(texts[text_index].get("text") or "").strip()
    before: list[str] = []
    after: list[str] = []
    for idx in range(text_index - 1, -1, -1):
        item = texts[idx]
        if page is not None and _page_of(item) != page:
            continue
        value = str(item.get("text") or "").strip()
        if value:
            before.append(value[:600])
        if len(before) >= max(0, int(before_count)):
            break
    before.reverse()
    for idx in range(text_index + 1, len(texts)):
        item = texts[idx]
        if page is not None and _page_of(item) != page:
            continue
        value = str(item.get("text") or "").strip()
        if value:
            after.append(value[:600])
        if len(after) >= max(0, int(after_count)):
            break
    return suspect, before, after


def _same_page_text_indices(
    doc: dict[str, Any], text_index: int, page: int | None, before_count: int = 3, after_count: int = 3
) -> tuple[list[int], list[int]]:
    """Return Docling text indices around a target, preserving same-page order."""
    texts = doc.get("texts") or []
    before: list[int] = []
    after: list[int] = []
    for idx in range(text_index - 1, -1, -1):
        item = texts[idx]
        if page is not None and _page_of(item) != page:
            continue
        if str(item.get("text") or "").strip():
            before.append(idx)
        if len(before) >= max(0, int(before_count)):
            break
    before.reverse()
    for idx in range(text_index + 1, len(texts)):
        item = texts[idx]
        if page is not None and _page_of(item) != page:
            continue
        if str(item.get("text") or "").strip():
            after.append(idx)
        if len(after) >= max(0, int(after_count)):
            break
    return before, after


def _table_cell_context_parts(
    doc: dict[str, Any], table_index: int, cell_index: int, before_count: int = 2, after_count: int = 2
) -> tuple[str, list[str], list[str]]:
    tables = doc.get("tables") or []
    if table_index < 0 or table_index >= len(tables):
        raise IndexError(f"Table index {table_index} is outside the Docling document")
    cells = ((tables[table_index].get("data") or {}).get("table_cells") or [])
    if cell_index < 0 or cell_index >= len(cells):
        raise IndexError(f"Cell index {cell_index} is outside table {table_index}")
    suspect = str(cells[cell_index].get("text") or "").strip()
    before = [str(cells[i].get("text") or "").strip()[:600] for i in range(max(0, cell_index-before_count), cell_index)]
    after = [str(cells[i].get("text") or "").strip()[:600] for i in range(cell_index+1, min(len(cells), cell_index+1+after_count))]
    return suspect, [x for x in before if x], [x for x in after if x]


def _render_source_table_cell(
    config: AppConfig, source_filename: str, page: int | None, doc: dict[str, Any], table_index: int, cell_index: int
) -> tuple[bytes, str, dict[str, Any]] | None:
    if page is None:
        return None
    tables = doc.get("tables") or []
    if table_index < 0 or table_index >= len(tables):
        return None
    cells = ((tables[table_index].get("data") or {}).get("table_cells") or [])
    if cell_index < 0 or cell_index >= len(cells):
        return None
    cell = cells[cell_index]
    bbox = cell.get("bbox")
    if not isinstance(bbox, dict):
        return None
    # Reuse the proven target-crop renderer with a synthetic one-item Docling text list.
    synthetic = {"texts": [{"text": cell.get("text") or "", "prov": [{"page_no": page, "bbox": bbox}]}]}
    rendered = _render_source_target(config, source_filename, page, synthetic, 0)
    if rendered:
        image, mime, meta = rendered
        meta = dict(meta)
        meta.update({"source_type": "table_cell", "table_index": table_index, "cell_index": cell_index})
        return image, mime, meta
    return None


def _text_context(doc: dict[str, Any], text_index: int, page: int | None) -> tuple[str, str]:
    """Backward-compatible flattened context used by older audit/backfill code."""
    suspect, before, after = _text_context_parts(doc, text_index, page)
    return suspect, "\n".join(before + after)


def _docling_bbox_to_fitz(item: dict[str, Any], page_height: float) -> fitz.Rect | None:
    """Convert a Docling provenance bbox into PyMuPDF top-left coordinates."""
    rects: list[fitz.Rect] = []
    for prov in item.get("prov") or []:
        if not isinstance(prov, dict) or not isinstance(prov.get("bbox"), dict):
            continue
        bbox = prov["bbox"]
        try:
            left = float(bbox.get("l")); right = float(bbox.get("r"))
            top = float(bbox.get("t")); bottom = float(bbox.get("b"))
        except (TypeError, ValueError):
            continue
        origin = str(bbox.get("coord_origin") or "BOTTOMLEFT").upper()
        if origin == "BOTTOMLEFT":
            y0, y1 = page_height - top, page_height - bottom
        else:
            y0, y1 = top, bottom
        rect = fitz.Rect(min(left, right), min(y0, y1), max(left, right), max(y0, y1))
        if rect.width > 0.5 and rect.height > 0.5:
            rects.append(rect)
    if not rects:
        return None
    merged = fitz.Rect(rects[0])
    for rect in rects[1:]:
        merged |= rect
    return merged



def _text_token_recall(reference: str, observed: str) -> float:
    """Generic lexical coverage used only to validate a source crop.

    This does not decide whether OCR is correct. It asks whether the PDF's own
    native text layer inside the rendered clip contains enough of the immutable
    Docling target to trust that the clip physically covers the target region.
    """
    ref = {
        token.casefold()
        for token in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", str(reference or ""))
        if len(token) >= 2
    }
    obs = {
        token.casefold()
        for token in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", str(observed or ""))
        if len(token) >= 2
    }
    if not ref or not obs:
        return 0.0
    return len(ref & obs) / max(1, len(ref))


def _neighbor_gap_crop_rect(
    page_rect: fitz.Rect,
    before_rect: fitz.Rect | None,
    after_rect: fitz.Rect | None,
    *,
    x_margin: float,
    max_vertical_fraction: float = 0.35,
) -> fitz.Rect | None:
    """Return the physical gap between two same-page neighboring blocks.

    This is a conservative recovery path for a missing/unreliable target bbox.
    It never expands to the whole page and is allowed only when the two anchors
    overlap horizontally enough to plausibly belong to the same reading band.
    """
    if before_rect is None or after_rect is None:
        return None
    if before_rect.y1 >= after_rect.y0:
        return None
    gap_h = after_rect.y0 - before_rect.y1
    if gap_h <= 3.0 or gap_h > page_rect.height * float(max_vertical_fraction):
        return None
    horizontal_overlap = max(0.0, min(before_rect.x1, after_rect.x1) - max(before_rect.x0, after_rect.x0))
    min_width = max(1.0, min(before_rect.width, after_rect.width))
    if horizontal_overlap / min_width < 0.20:
        return None
    clip = fitz.Rect(
        min(before_rect.x0, after_rect.x0) - float(x_margin),
        before_rect.y1 + 1.0,
        max(before_rect.x1, after_rect.x1) + float(x_margin),
        after_rect.y0 - 1.0,
    )
    clip &= page_rect
    if clip.width <= 1.0 or clip.height <= 1.0:
        return None
    return clip


def _source_transcription_token_budget(original: str) -> int:
    """Give direct source transcription enough room to finish without bloat.

    Direct transcription must be complete; unlike classification JSON, a
    length-truncated prefix can never be considered usable. The dynamic budget
    keeps short labels cheap while allowing long technical paragraphs to end.
    """
    chars = len(str(original or ""))
    return max(512, min(1024, int(chars / 2.0) + 256))


def _source_transcription_truncated(raw: dict[str, Any]) -> bool:
    if _response_finish_reason(raw) == "length":
        return True
    stream = raw.get("_stream") if isinstance(raw, dict) else None
    return bool(isinstance(stream, dict) and (stream.get("truncated") or stream.get("finish_reason") == "length"))


def _target_crop_rect(
    page_rect: fitz.Rect,
    target_rect: fitz.Rect,
    before_rect: fitz.Rect | None,
    after_rect: fitz.Rect | None,
    *,
    x_margin: float,
    y_margin: float,
    max_vertical_fraction: float,
) -> fitz.Rect:
    """Build a target-only clip while using neighboring blocks as boundaries.

    BEFORE/AFTER are never included deliberately. They only prevent the crop
    from growing into unrelated page content. This is the core safeguard that
    stops a vision model from transcribing the whole page.
    """
    max_y_extra = max(float(y_margin), page_rect.height * float(max_vertical_fraction))
    x_extra = max(float(x_margin), min(page_rect.width * 0.10, target_rect.width * 0.35))
    y_extra = min(max_y_extra, max(float(y_margin), target_rect.height * 1.5))
    clip = fitz.Rect(
        target_rect.x0 - x_extra,
        target_rect.y0 - y_extra,
        target_rect.x1 + x_extra,
        target_rect.y1 + y_extra,
    )

    # If the nearest Docling anchors are physically above/below the target,
    # stop at the midpoint of each gap so their text remains outside the crop.
    if before_rect is not None and before_rect.y1 <= target_rect.y0:
        clip.y0 = max(clip.y0, (before_rect.y1 + target_rect.y0) / 2.0)
    if after_rect is not None and after_rect.y0 >= target_rect.y1:
        clip.y1 = min(clip.y1, (target_rect.y1 + after_rect.y0) / 2.0)

    clip &= page_rect
    # Never clip into the actual target due to malformed neighbor geometry.
    clip.x0 = min(clip.x0, target_rect.x0)
    clip.y0 = min(clip.y0, target_rect.y0)
    clip.x1 = max(clip.x1, target_rect.x1)
    clip.y1 = max(clip.y1, target_rect.y1)
    clip &= page_rect
    return clip


def _render_source_target(
    config: AppConfig,
    source_filename: str,
    page: int | None,
    doc: dict[str, Any],
    text_index: int,
) -> tuple[bytes, str, dict[str, Any]] | None:
    """Render only the source-image area belonging to one Docling text block.

    PDF and raster-image sources are supported. A whole-page fallback is
    disabled by default. If Docling has no usable bbox, preserving the original
    OCR is safer than asking a vision model to guess which text on a dense page
    is the target.
    """
    if page is None:
        return None
    source = Path(config.input_dir) / Path(source_filename).name
    if not source.is_file():
        return None
    texts = doc.get("texts") or []
    if text_index < 0 or text_index >= len(texts):
        return None

    def build_clip(page_rect: fitz.Rect, page_height: float) -> dict[str, Any] | None:
        before_indices, after_indices = _same_page_text_indices(doc, text_index, page, 1, 1)
        before_rect = _docling_bbox_to_fitz(texts[before_indices[-1]], page_height) if before_indices else None
        after_rect = _docling_bbox_to_fitz(texts[after_indices[0]], page_height) if after_indices else None
        target_rect = _docling_bbox_to_fitz(texts[text_index], page_height)
        x_margin = float(getattr(config, "stage2b_text_target_crop_x_margin_points", 30.0))
        if target_rect is None:
            gap = _neighbor_gap_crop_rect(page_rect, before_rect, after_rect, x_margin=x_margin)
            if gap is not None:
                return {
                    "clip": gap, "mode": "neighbor_gap_bbox_recovery", "bbox_available": False,
                    "target_rect": None, "before_rect": before_rect, "after_rect": after_rect,
                }
            if not bool(getattr(config, "stage2b_text_allow_full_page_fallback", False)):
                return None
            return {
                "clip": fitz.Rect(page_rect), "mode": "full_page_explicit_fallback", "bbox_available": False,
                "target_rect": None, "before_rect": before_rect, "after_rect": after_rect,
            }
        clip = _target_crop_rect(
            page_rect,
            target_rect,
            before_rect,
            after_rect,
            x_margin=x_margin,
            y_margin=float(getattr(config, "stage2b_text_target_crop_y_margin_points", 14.0)),
            max_vertical_fraction=float(getattr(config, "stage2b_text_target_crop_max_vertical_fraction", 0.12)),
        )
        return {
            "clip": clip, "mode": "docling_target_bbox", "bbox_available": True,
            "target_rect": target_rect, "before_rect": before_rect, "after_rect": after_rect,
        }

    try:
        if source.suffix.lower() == ".pdf":
            with fitz.open(source) as pdf:
                page_index = int(page) - 1
                if page_index < 0 or page_index >= len(pdf):
                    return None
                pdf_page = pdf[page_index]
                built = build_clip(pdf_page.rect, pdf_page.rect.height)
                if built is None:
                    return None
                clip = fitz.Rect(built["clip"])
                crop_mode = str(built["mode"])
                bbox_available = bool(built["bbox_available"])
                target_text = str(texts[text_index].get("text") or "")
                native_text = pdf_page.get_text("text", clip=clip) or ""
                # Only trust the PDF-native coverage check when the page itself
                # has a usable native text layer. An empty target clip on such a
                # page is evidence that the bbox may miss the physical text and
                # should be eligible for bounded neighbor-gap recovery.
                page_native_text = pdf_page.get_text("text") or ""
                page_native_tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", page_native_text)
                native_coverage = _text_token_recall(target_text, native_text) if len(page_native_tokens) >= 3 else None
                recovered_from_gap = False
                # If a PDF has a usable native text layer and the bbox crop
                # demonstrably misses the target, retry the bounded physical
                # gap between the nearest neighbors. Never use the whole page.
                if (
                    crop_mode == "docling_target_bbox"
                    and native_coverage is not None
                    and len(target_text.strip()) >= 24
                    and native_coverage < 0.45
                ):
                    gap = _neighbor_gap_crop_rect(
                        pdf_page.rect,
                        built.get("before_rect"), built.get("after_rect"),
                        x_margin=float(getattr(config, "stage2b_text_target_crop_x_margin_points", 30.0)),
                    )
                    if gap is not None:
                        gap_text = pdf_page.get_text("text", clip=gap) or ""
                        gap_coverage = _text_token_recall(target_text, gap_text)
                        if gap_coverage >= max(0.45, native_coverage + 0.15):
                            clip = gap
                            native_text = gap_text
                            native_coverage = gap_coverage
                            crop_mode = "neighbor_gap_text_coverage_recovery"
                            recovered_from_gap = True
                scale = float(getattr(config, "stage2b_text_target_crop_scale", 2.5))
                rendered = pdf_page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
                meta = {
                    "mode": crop_mode,
                    "source_kind": "pdf",
                    "page": int(page),
                    "clip_points": [round(clip.x0, 2), round(clip.y0, 2), round(clip.x1, 2), round(clip.y1, 2)],
                    "scale": scale,
                    "pixel_width": int(rendered.width),
                    "pixel_height": int(rendered.height),
                    "target_bbox_available": bbox_available,
                    "full_page_fallback": crop_mode == "full_page_explicit_fallback",
                    "native_text_coverage": round(native_coverage, 4) if native_coverage is not None else None,
                    "native_text_chars": len(native_text.strip()),
                    "neighbor_gap_recovery": recovered_from_gap or crop_mode == "neighbor_gap_bbox_recovery",
                }
                return rendered.tobytes("png"), "image/png", meta

        if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}:
            with Image.open(source) as opened:
                try:
                    opened.seek(max(0, int(page) - 1))
                except EOFError:
                    return None
                image = opened.convert("RGB")
                page_info = (doc.get("pages") or {}).get(str(page)) or {}
                size = page_info.get("size") or {}
                try:
                    doc_w = float(size.get("width") or image.width)
                    doc_h = float(size.get("height") or image.height)
                except (TypeError, ValueError):
                    doc_w, doc_h = float(image.width), float(image.height)
                if doc_w <= 0 or doc_h <= 0:
                    return None
                built = build_clip(fitz.Rect(0, 0, doc_w, doc_h), doc_h)
                if built is None:
                    return None
                clip = fitz.Rect(built["clip"])
                crop_mode = str(built["mode"])
                bbox_available = bool(built["bbox_available"])
                sx, sy = image.width / doc_w, image.height / doc_h
                px_box = (
                    max(0, int(clip.x0 * sx)),
                    max(0, int(clip.y0 * sy)),
                    min(image.width, max(1, int(round(clip.x1 * sx)))),
                    min(image.height, max(1, int(round(clip.y1 * sy)))),
                )
                if px_box[2] <= px_box[0] or px_box[3] <= px_box[1]:
                    return None
                cropped = image.crop(px_box)
                scale = float(getattr(config, "stage2b_text_target_crop_scale", 2.5))
                if scale > 1.0:
                    cropped = cropped.resize(
                        (max(1, int(cropped.width * scale)), max(1, int(cropped.height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                buffer = io.BytesIO()
                cropped.save(buffer, format="PNG", optimize=True)
                meta = {
                    "mode": crop_mode,
                    "source_kind": "raster_image",
                    "page": int(page),
                    "clip_points": [round(clip.x0, 2), round(clip.y0, 2), round(clip.x1, 2), round(clip.y1, 2)],
                    "pixel_box": list(px_box),
                    "scale": scale,
                    "pixel_width": int(cropped.width),
                    "pixel_height": int(cropped.height),
                    "target_bbox_available": bbox_available,
                    "full_page_fallback": crop_mode == "full_page_explicit_fallback",
                }
                return buffer.getvalue(), "image/png", meta
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def _render_source_page(config: AppConfig, source_filename: str, page: int | None) -> tuple[bytes, str] | None:
    """Legacy full-page renderer retained only for optional audit UI paths."""
    if page is None:
        return None
    source = Path(config.input_dir) / Path(source_filename).name
    if source.suffix.lower() != ".pdf" or not source.is_file():
        return None
    try:
        with fitz.open(source) as pdf:
            page_index = int(page) - 1
            if page_index < 0 or page_index >= len(pdf):
                return None
            rendered = pdf[page_index].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            return rendered.tobytes("png"), "image/png"
    except (OSError, RuntimeError, ValueError):
        return None


def _closed_json_string_field(text: str, key: str) -> str | None:
    """Recover one fully closed JSON string field from a truncated object.

    The OnePlus verifier may finish with ``length`` after it has already emitted
    the two fields we need.  Recover only syntactically complete quoted strings;
    never guess an unfinished value.
    """
    match = re.search(
        rf'"{re.escape(key)}"\s*:\s*("(?:\\.|[^"\\])*")',
        str(text or ""),
    )
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return str(value)


def _crosscheck_content(raw: dict[str, Any]) -> str:
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content or "")



def _adjacent_duplicate_tokens(text: str) -> list[dict[str, Any]]:
    """Return consecutive duplicate lexical tokens with stable positions.

    This is deliberately language-agnostic and does not decide whether a
    repetition is grammatically correct.  It is used only to detect a *new*
    repetition introduced by source-image transcription relative to immutable
    Docling text.
    """
    tokens = [
        match.group(0).casefold()
        for match in re.finditer(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", _normalize_evidence_text(str(text or "")))
        if len(match.group(0)) >= 2
    ]
    found: list[dict[str, Any]] = []
    for index in range(1, len(tokens)):
        if tokens[index] == tokens[index - 1]:
            found.append({"token": tokens[index], "token_index": index - 1})
    return found


def _novel_adjacent_duplicate_tokens(candidate: str, original: str) -> list[dict[str, Any]]:
    """Find adjacent duplicate tokens introduced only by the candidate.

    A newly duplicated word is a characteristic local-model transcription
    hallucination (for example ``on`` -> ``on on``).  We do not delete one of
    the words automatically because a repeated word may genuinely be printed
    in the source.  Instead, the direct transcription is made ineligible for
    automatic application and the immutable Docling text is preserved.
    """
    original_duplicates = {item["token"] for item in _adjacent_duplicate_tokens(original)}
    return [
        item for item in _adjacent_duplicate_tokens(candidate)
        if item["token"] not in original_duplicates
    ]


def _scope_target_transcription(
    candidate: str,
    original: str,
    before_anchors: list[str] | None,
    after_anchors: list[str] | None,
) -> dict[str, Any]:
    """Deterministically isolate the transcription that belongs to TARGET.

    The source-image model is not asked to judge OCR quality.  This post-pass
    uses only the immutable Docling target plus neighboring BEFORE/AFTER blocks
    to remove obvious model contamination and adjacent-region leakage.  It does
    not substitute vocabulary or infer engineering meaning.
    """
    raw = str(candidate or "")
    original_text = str(original or "").strip()
    before = [str(x).strip() for x in (before_anchors or []) if str(x).strip()]
    after = [str(x).strip() for x in (after_anchors or []) if str(x).strip()]

    contamination_removed = False
    duplicate_removed = False

    # Reasoning/format wrappers are transport contamination, never source text.
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", raw)
    cleaned = re.sub(r"(?is)</?think>", "", cleaned)
    cleaned = re.sub(r"(?is)^\s*```(?:text|plaintext|markdown)?\s*", "", cleaned)
    cleaned = re.sub(r"(?is)\s*```\s*$", "", cleaned).strip()
    if cleaned != raw.strip():
        contamination_removed = True

    # Keep explicit separators so leaked context labels split candidate regions.
    records: list[tuple[str | None, int]] = []
    source_line_no = 0
    boilerplate_re = re.compile(
        r"^\s*(?:before|after)(?:\s+context)?\s*:?(?:\s*)$|"
        r"^\s*(?:target(?:\s+transcription)?|answer|transcription)\s*:\s*$",
        re.I,
    )
    preamble_re = re.compile(
        r"^\s*(?:here(?:'s| is)\s+(?:the\s+)?(?:target\s+)?transcription|"
        r"the\s+target\s+(?:text|transcription)\s+is)\s*:?\s*$",
        re.I,
    )
    inline_label_re = re.compile(r"^\s*(?:target(?:\s+transcription)?|answer)\s*:\s*(.+)$", re.I)

    for raw_line in cleaned.splitlines():
        source_line_no += 1
        line = raw_line.strip()
        if not line:
            continue
        if line in {"---", "***", "___"}:
            records.append((None, source_line_no))
            continue
        if boilerplate_re.match(line) or preamble_re.match(line):
            contamination_removed = True
            records.append((None, source_line_no))
            continue
        inline = inline_label_re.match(line)
        if inline:
            contamination_removed = True
            line = inline.group(1).strip()
            if not line:
                records.append((None, source_line_no))
                continue
        records.append((line, source_line_no))

    content_lines = [line for line, _no in records if line is not None]
    if not content_lines:
        return {
            "accepted": False,
            "status": "UNRESOLVED",
            "text": "",
            "scoped_text": "",
            "reasons": ["EMPTY_TRANSCRIPTION"],
            "reason": "EMPTY_TRANSCRIPTION",
            "trimmed": bool(contamination_removed),
            "removed_prefix": "",
            "removed_suffix": "",
            "duplicate_removed": False,
            "contamination_removed": contamination_removed,
            "anchor_overlap": {"before": 0, "after": 0},
        }

    # Remove exact adjacent duplication while retaining legitimate repeated text
    # separated by other source lines.
    deduped_records: list[tuple[str | None, int]] = []
    last_norm = ""
    for line, no in records:
        if line is None:
            deduped_records.append((line, no))
            last_norm = ""
            continue
        norm_line = _normalize_evidence_text(line)
        if last_norm and norm_line == last_norm:
            duplicate_removed = True
            continue
        deduped_records.append((line, no))
        last_norm = norm_line
    records = deduped_records

    # Detect an exact duplicated whole generation (A...A) after presentation
    # separators have been removed. This is common with small local models.
    flattened = [(line, no) for line, no in records if line is not None]
    if len(flattened) >= 2 and len(flattened) % 2 == 0:
        half = len(flattened) // 2
        left = [_normalize_evidence_text(x[0] or "") for x in flattened[:half]]
        right = [_normalize_evidence_text(x[0] or "") for x in flattened[half:]]
        if left == right:
            keep_nos = {no for _line, no in flattened[:half]}
            records = [(line, no) for line, no in records if line is None or no in keep_nos]
            duplicate_removed = True

    def line_matches_anchor(line: str, anchor: str) -> bool:
        ln = _normalize_evidence_text(line)
        an = _normalize_evidence_text(anchor)
        if not ln or not an:
            return False
        if ln == an:
            return True
        if len(an) >= 8 and (an in ln or ln in an):
            return True
        if min(len(ln), len(an)) >= 12:
            return difflib.SequenceMatcher(None, ln, an, autojunk=False).ratio() >= 0.92
        return False

    before_hits = 0
    after_hits = 0
    marked: list[tuple[str | None, int, bool]] = []
    for line, no in records:
        if line is None:
            marked.append((None, no, True))
            continue
        is_before = any(line_matches_anchor(line, anchor) for anchor in before)
        is_after = any(line_matches_anchor(line, anchor) for anchor in after)
        before_hits += int(is_before)
        after_hits += int(is_after)
        marked.append((line, no, is_before or is_after))

    # Split at every recognized neighbor/presentation separator.  Do not assume
    # visual BEFORE/AFTER order because Docling order can differ on columns.
    segments: list[list[tuple[str, int]]] = []
    current: list[tuple[str, int]] = []
    for line, no, separator in marked:
        if separator:
            if current:
                segments.append(current)
                current = []
            continue
        if line is not None:
            current.append((line, no))
    if current:
        segments.append(current)

    orig_norm = _normalize_evidence_text(original_text)

    def token_set(text: str) -> set[str]:
        return {
            token.casefold()
            for token in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", _normalize_evidence_text(text))
            if len(token) >= 2
        }

    orig_tokens = token_set(original_text)

    def window_metrics(lines: list[tuple[str, int]]) -> tuple[float, float, float, float, float]:
        joined = "\n".join(line for line, _no in lines).strip()
        cand_norm = _normalize_evidence_text(joined)
        cand_tokens = token_set(joined)
        shared = orig_tokens & cand_tokens
        token_recall = len(shared) / max(1, len(orig_tokens)) if orig_tokens else 0.0
        token_precision = len(shared) / max(1, len(cand_tokens)) if cand_tokens else 0.0
        seq = difflib.SequenceMatcher(None, orig_norm, cand_norm, autojunk=False).ratio() if orig_norm and cand_norm else 0.0
        length_similarity = min(len(orig_norm), len(cand_norm)) / max(1, max(len(orig_norm), len(cand_norm))) if (orig_norm or cand_norm) else 0.0
        # Recall dominates precision so a corrupted Docling target may safely
        # recover missing continuation text, while unrelated trailing labels
        # still lose on sequence/length similarity.
        score = 1.4 * seq + 2.0 * token_recall + 0.2 * token_precision + 0.2 * length_similarity
        return score, seq, token_recall, token_precision, length_similarity

    # Within every anchor-delimited segment, select the contiguous line window
    # that best aligns structurally with the Docling target. This fixes cases
    # where the crop contains target text followed by unrelated diagram labels.
    best: tuple[float, float, float, float, float, list[tuple[str, int]]] | None = None
    for segment in segments:
        n = len(segment)
        for i in range(n):
            for j in range(i + 1, n + 1):
                window = segment[i:j]
                metrics = window_metrics(window)
                candidate_key = (*metrics, window)
                if best is None:
                    best = candidate_key
                    continue
                # Score first; when virtually tied, prefer the window whose
                # length is closer to the immutable target, then fewer lines.
                score, _seq, _recall, _precision, length_similarity = metrics
                bscore, _bseq, _brecall, _bprecision, blength_similarity, bwindow = best
                if score > bscore + 0.015:
                    best = candidate_key
                elif abs(score - bscore) <= 0.015:
                    if length_similarity > blength_similarity + 0.02 or (
                        abs(length_similarity - blength_similarity) <= 0.02 and len(window) < len(bwindow)
                    ):
                        best = candidate_key

    if best is None:
        return {
            "accepted": False,
            "status": "UNRESOLVED",
            "text": "",
            "scoped_text": "",
            "reasons": ["ONLY_CONTEXT_OR_ADJACENT_BLOCK_RETURNED"],
            "reason": "ONLY_CONTEXT_OR_ADJACENT_BLOCK_RETURNED",
            "trimmed": True,
            "removed_prefix": "",
            "removed_suffix": "",
            "duplicate_removed": duplicate_removed,
            "contamination_removed": contamination_removed,
            "anchor_overlap": {"before": before_hits, "after": after_hits},
        }

    score, seq, token_recall, token_precision, length_similarity, best_window = best
    scoped = "\n".join(line for line, _no in best_window).strip()
    chosen_nos = [no for _line, no in best_window]
    first_no, last_no = min(chosen_nos), max(chosen_nos)
    surviving_lines = [(line, no) for line, no, _sep in marked if line is not None]
    removed_prefix_lines = [line for line, no in surviving_lines if no < first_no]
    removed_suffix_lines = [line for line, no in surviving_lines if no > last_no]
    removed_prefix = "\n".join(removed_prefix_lines).strip()[:1200]
    removed_suffix = "\n".join(removed_suffix_lines).strip()[:1200]

    reasons: list[str] = []
    scoped_norm = _normalize_evidence_text(scoped)
    scoped_tokens = token_set(scoped)
    shared = orig_tokens & scoped_tokens

    # Reject a structurally unrelated region. Exact token overlap is only one
    # signal; character alignment also permits ordinary OCR corrections such as
    # "Transmilter" -> "Transmitter" or "Curent Settig" -> "Current Setting".
    if len(orig_tokens) >= 4 and scoped_tokens:
        overlap = len(shared) / max(1, min(len(orig_tokens), len(scoped_tokens)))
        if len(shared) < 2 and overlap < 0.18 and seq < 0.52:
            reasons.append("NO_TARGET_LEXICAL_OVERLAP")

    original_len = len(original_text)
    # Exact long prefixes/suffixes are a characteristic failure mode of
    # completion truncation and clipped source regions. Even if a provider
    # incorrectly reports a clean stop, never auto-apply a partial immutable
    # target merely because every returned token happens to match its prefix.
    if original_len >= 80 and scoped_norm and orig_norm:
        ratio = len(scoped_norm) / max(1, len(orig_norm))
        if ratio < 0.90 and (orig_norm.startswith(scoped_norm) or orig_norm.endswith(scoped_norm)):
            reasons.append("PARTIAL_TARGET_PREFIX_OR_SUFFIX")
    if original_len >= 40 and len(scoped) > max(int(original_len * 2.0), original_len + 250):
        reasons.append("EXCESSIVE_TARGET_EXPANSION")
    # A long Docling block reconstructed as only a heading or sentence fragment
    # is not a safe correction. Preserve the original rather than auto-applying
    # a partial crop/model response. Short targets are exempt.
    if original_len >= 80 and length_similarity < 0.55:
        reasons.append("EXCESSIVE_TARGET_CONTRACTION")
    if re.search(r"(?i)</?think>", scoped):
        reasons.append("REASONING_WRAPPER_REMAINS")

    # Direct transcription must not invent local repetitions.  This is not an
    # autocorrect rule: if a repeated word is newly introduced relative to the
    # immutable target, reject auto-apply and leave the image/model output for
    # human audit instead of guessing which copy should be removed.
    novel_duplicate_tokens = _novel_adjacent_duplicate_tokens(scoped, original_text)
    if novel_duplicate_tokens:
        reasons.append("NOVEL_TOKEN_DUPLICATION")

    # Final target-localization safety: a verifier can faithfully transcribe
    # the wrong nearby label if the Docling bbox/crop points at the wrong region.
    # Reject only strong contradiction signatures; ordinary one-letter OCR
    # corrections remain eligible. Numeric/unit/identifier changes require
    # stronger alignment than ordinary prose edits.
    alignment_safety = source_transcription_safety_profile(original_text, scoped)
    for reason in alignment_safety.get("reasons") or []:
        if reason not in reasons:
            reasons.append(str(reason))

    # A model returning only a neighbor is unresolved even if the neighbor was
    # recognized and removed. No empty correction may auto-apply.
    if not scoped:
        reasons.append("ONLY_CONTEXT_OR_ADJACENT_BLOCK_RETURNED")

    trimmed = bool(
        contamination_removed
        or duplicate_removed
        or before_hits
        or after_hits
        or removed_prefix
        or removed_suffix
        or _normalize_evidence_text(scoped) != _normalize_evidence_text(cleaned)
    )

    base = {
        "text": scoped[:4000] if not reasons else "",
        "scoped_text": scoped[:4000] if not reasons else "",
        "candidate_text": scoped[:4000] if reasons else scoped[:4000],
        "trimmed": trimmed,
        "removed_prefix": removed_prefix,
        "removed_suffix": removed_suffix,
        "duplicate_removed": duplicate_removed,
        "contamination_removed": contamination_removed,
        "anchor_overlap": {"before": before_hits, "after": after_hits},
        "before_anchor_hits": before_hits,
        "after_anchor_hits": after_hits,
        "scope_score": round(score, 4),
        "sequence_similarity": round(seq, 4),
        "target_token_recall": round(token_recall, 4),
        "target_token_precision": round(token_precision, 4),
        "length_similarity": round(length_similarity, 4),
        "novel_duplicate_tokens": novel_duplicate_tokens,
        "alignment_safety": alignment_safety,
    }
    if reasons:
        base.update({
            "accepted": False,
            "status": "UNRESOLVED",
            "reasons": reasons,
            "reason": reasons[0],
            "confidence": "low",
        })
        return base

    status = "SCOPED" if trimmed else "UNCHANGED"
    confidence = "high" if (seq >= 0.70 or token_recall >= 0.70) else "medium"
    base.update({
        "accepted": True,
        "status": status,
        "reasons": ["TARGET_SCOPE_CONFIRMED"],
        "reason": "TARGET_SCOPE_CONFIRMED",
        "confidence": confidence,
    })
    return base


async def _oneplus_text_crosscheck(
    client,
    image: bytes,
    mime: str,
    original: str,
    proposed: str,
    model: str | None,
    first_token_timeout_seconds: int = 1200,
    idle_timeout_seconds: int = 300,
    *,
    before_anchors: list[str] | None = None,
    after_anchors: list[str] | None = None,
) -> dict[str, Any]:
    """Directly transcribe one cropped target region from the source image.

    Pi5 and OnePlus get a deliberately simple plain-text task because small
    local models are more reliable when they do not also have to satisfy a JSON
    schema. Groq keeps strict two-field JSON. BEFORE/AFTER text is only a
    document-position hint; the image crop itself contains the target, not the
    neighboring blocks. There is no semantic approve/reject vote.
    """
    original_for_scope = str(original or "")
    del proposed  # never show another model's guess to the source transcription model
    provider = str(getattr(client, "provider", "oneplus") or "oneplus").lower()
    before = [str(x).strip() for x in (before_anchors or []) if str(x).strip()]
    after = [str(x).strip() for x in (after_anchors or []) if str(x).strip()]
    # The crop is the primary locator. Give the model only the nearest
    # boundary snippets so small local models do not waste completion tokens
    # echoing long Docling context blocks.
    nearest_before = before[-1][-320:] if before else ""
    nearest_after = after[0][:320] if after else ""
    before_text = nearest_before or "[NO BEFORE ANCHOR]"
    after_text = nearest_after or "[NO AFTER ANCHOR]"

    rules = (
        "The attached image is already cropped to the TARGET region. Transcribe ONLY text visibly present in "
        "that crop. BEFORE and AFTER below are context/location hints only and are NOT part of the answer. "
        "Do not summarize. Do not describe diagrams or pictures. Do not infer what equipment does. Do not "
        "improve grammar. Do not replace technical words with more likely words. Do not accidentally repeat a "
        "word; if the source visibly repeats a word, preserve that repetition exactly. Preserve visible spelling, "
        "capitalization, punctuation, symbols, numbers, units and identifiers exactly. If characters in the "
        "target cannot be read reliably, return [UNREADABLE]. Never reproduce BEFORE or AFTER."
    )
    if provider == "groq":
        prompt = (
            rules + " Return ONLY one compact JSON object with exactly two keys: status and corrected_text. "
            "status must be READABLE or UNREADABLE. For READABLE, corrected_text is exactly the transcription "
            "from the TARGET crop. For UNREADABLE, corrected_text must be empty.\n\n"
            f"BEFORE CONTEXT:\n{before_text[:1800]}\n\n"
            f"AFTER CONTEXT:\n{after_text[:1800]}"
        )
    else:
        prompt = (
            rules + " Return ONLY the target transcription as plain text, with no JSON, label, explanation, "
            "markdown or commentary. If unreadable, return exactly [UNREADABLE].\n\n"
            f"BEFORE CONTEXT:\n{before_text[:1800]}\n\n"
            f"AFTER CONTEXT:\n{after_text[:1800]}"
        )
    def scope_candidate(candidate: str) -> dict[str, Any]:
        return _scope_target_transcription(candidate, original=original_for_scope, before_anchors=before, after_anchors=after)

    try:
        raw = await client.inspect_image_stream(
            image,
            prompt,
            mime_type=mime,
            model=model,
            max_tokens=_source_transcription_token_budget(original_for_scope),
            first_token_timeout_seconds=int(first_token_timeout_seconds),
            idle_timeout_seconds=int(idle_timeout_seconds),
        )
    except Exception as exc:
        return {
            "verdict": "UNREADABLE", "status": "UNREADABLE", "corrected_text": "",
            "raw_response": None, "usable": False, "transport_failed": True,
            "direct_transcription": True, "provider": provider,
            "error_type": type(exc).__name__, "error_message": str(exc)[:500],
        }

    finish_reason = _response_finish_reason(raw)
    text = _crosscheck_content(raw).strip()
    # Direct transcription is an all-or-nothing source read. A length-limited
    # prefix is not a correction and must never replace immutable Docling text.
    if _source_transcription_truncated(raw):
        return {
            "verdict": "UNREADABLE", "status": "UNREADABLE", "corrected_text": "",
            "raw_response": raw, "usable": False, "direct_transcription": True, "provider": provider,
            "finish_reason": finish_reason, "truncated": True,
            "error_type": "SourceTranscriptionTruncated",
            "error_message": "Source-image transcription reached the completion-token limit; original Docling text preserved.",
        }

    # Groq is schema-constrained; local llama.cpp is intentionally plain-text.
    if provider == "groq":
        try:
            parsed = _json_from_model_response(raw)
            status = str(parsed.get("status") or "UNREADABLE").upper()
            corrected_text = str(parsed.get("corrected_text") or "")[:4000]
            if status not in {"READABLE", "UNREADABLE"} or (status == "READABLE" and not corrected_text.strip()):
                status, corrected_text = "UNREADABLE", ""
            scope_guard = None
            if status == "READABLE":
                scope_guard = scope_candidate(corrected_text)
                if not scope_guard.get("accepted"):
                    return {
                        "verdict": "UNREADABLE", "status": "UNREADABLE", "corrected_text": "",
                        "raw_response": raw, "usable": False, "direct_transcription": True, "provider": provider,
                        "scope_guard": scope_guard, "scope_rejected": True, "finish_reason": finish_reason,
                        "truncated": finish_reason == "length",
                    }
                corrected_text = str(scope_guard.get("text") or "")
            return {
                "verdict": status, "status": status, "corrected_text": corrected_text,
                "raw_response": raw, "usable": status == "READABLE", "direct_transcription": True,
                "provider": provider, "partial_response_recovered": False, "scope_guard": scope_guard,
                "finish_reason": finish_reason, "truncated": finish_reason == "length",
            }
        except ValueError as exc:
            status = (_closed_json_string_field(text, "status") or "UNREADABLE").upper()
            corrected_text = _closed_json_string_field(text, "corrected_text")
            if status == "READABLE" and corrected_text is not None and corrected_text.strip():
                scope_guard = scope_candidate(corrected_text)
                if not scope_guard.get("accepted"):
                    return {
                        "verdict": "UNREADABLE", "status": "UNREADABLE", "corrected_text": "",
                        "raw_response": raw, "usable": False, "direct_transcription": True, "provider": provider,
                        "scope_guard": scope_guard, "scope_rejected": True, "partial_response_recovered": True,
                        "finish_reason": finish_reason, "truncated": finish_reason == "length",
                    }
                return {
                    "verdict": "READABLE", "status": "READABLE", "corrected_text": str(scope_guard.get("text") or "")[:4000],
                    "raw_response": raw, "usable": True, "direct_transcription": True, "provider": provider,
                    "scope_guard": scope_guard, "partial_response_recovered": True, "finish_reason": finish_reason,
                    "truncated": finish_reason == "length", "parse_error": str(exc)[:500],
                }
            return {
                "verdict": "UNREADABLE", "status": "UNREADABLE", "corrected_text": "",
                "raw_response": raw, "usable": False, "direct_transcription": True, "provider": provider,
                "parse_failed": True, "finish_reason": finish_reason, "truncated": finish_reason == "length",
                "parse_error": str(exc)[:500],
            }

    # Local processors: strip only transport/common formatting, not technical
    # punctuation or wording. The crop is the scope boundary, so no text-model
    # comparison is needed.
    cleaned = text
    cleaned = re.sub(r"(?is)^\s*```(?:text|plaintext)?\s*", "", cleaned)
    cleaned = re.sub(r"(?is)\s*```\s*$", "", cleaned).strip()
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", cleaned).strip()
    if not cleaned or cleaned.upper() in {"[UNREADABLE]", "UNREADABLE"}:
        return {
            "verdict": "UNREADABLE", "status": "UNREADABLE", "corrected_text": "",
            "raw_response": raw, "usable": False, "direct_transcription": True, "provider": provider,
            "finish_reason": finish_reason, "truncated": finish_reason == "length",
        }
    scope_guard = scope_candidate(cleaned)
    if not scope_guard.get("accepted"):
        return {
            "verdict": "UNREADABLE", "status": "UNREADABLE", "corrected_text": "",
            "raw_response": raw, "usable": False, "direct_transcription": True, "provider": provider,
            "scope_guard": scope_guard, "scope_rejected": True, "finish_reason": finish_reason,
            "truncated": finish_reason == "length",
        }
    return {
        "verdict": "READABLE", "status": "READABLE", "corrected_text": str(scope_guard.get("text") or "")[:4000],
        "raw_response": raw, "usable": True, "direct_transcription": True, "provider": provider,
        "scope_guard": scope_guard, "finish_reason": finish_reason, "truncated": finish_reason == "length",
    }

def _normalize_member_name(uri: str) -> str:
    return uri.replace("\\", "/").lstrip("./")


def _read_picture(zip_path: Path, doc: dict[str, Any], picture_index: int, artifact_hint: str | None) -> tuple[bytes, str, str]:
    pictures = doc.get("pictures") or []
    if picture_index < 0 or picture_index >= len(pictures):
        raise IndexError(f"Picture index {picture_index} is outside the Docling document")
    picture = pictures[picture_index]
    image = picture.get("image") or {}
    uri = str(artifact_hint or image.get("uri") or "")
    if not uri:
        raise ValueError("Picture has no referenced artifact URI")
    wanted = _normalize_member_name(uri)
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        by_normalized = {_normalize_member_name(name): name for name in names}
        actual = by_normalized.get(wanted)
        if actual is None:
            # Some exporters prefix a book directory. Fall back to unique suffix.
            candidates = [name for name in names if _normalize_member_name(name).endswith(wanted)]
            if len(candidates) == 1:
                actual = candidates[0]
        if actual is None:
            raise FileNotFoundError(f"Referenced picture artifact not found in ZIP: {uri}")
        data = archive.read(actual)
    mime = str(image.get("mimetype") or "image/png")
    return data, mime, actual


def _vision_crops(image_bytes: bytes, overlap: float, upscale: float, max_crops: int) -> list[tuple[str, bytes, str]]:
    """Create at most four overlapping 2x2 crops in reading-order sequence."""
    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = opened.convert("RGB")
        width, height = image.size
        if width < 160 or height < 160:
            return []
        half_w, half_h = width / 2.0, height / 2.0
        extra_w = half_w * max(0.0, min(overlap, 0.45))
        extra_h = half_h * max(0.0, min(overlap, 0.45))
        regions = [
            ("top-left", (0, 0, min(width, int(half_w + extra_w)), min(height, int(half_h + extra_h)))),
            ("top-right", (max(0, int(half_w - extra_w)), 0, width, min(height, int(half_h + extra_h)))),
            ("bottom-left", (0, max(0, int(half_h - extra_h)), min(width, int(half_w + extra_w)), height)),
            ("bottom-right", (max(0, int(half_w - extra_w)), max(0, int(half_h - extra_h)), width, height)),
        ]
        output: list[tuple[str, bytes, str]] = []
        for label, box in regions[:max_crops]:
            crop = image.crop(box)
            if upscale > 1.0:
                crop = crop.resize((max(1, int(crop.width * upscale)), max(1, int(crop.height * upscale))), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            crop.save(buf, format="JPEG", quality=90, optimize=True)
            output.append((label, buf.getvalue(), "image/jpeg"))
        return output


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _pi5_prompt_payload(job: dict[str, Any], suspect: str, context: str, structure: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    structure = structure or analyze_text_structure(suspect)
    source = json.loads(job["source_json"])
    recall_evidence = source.get("ocr_recall_evidence") or []
    logical = {
        "task": "ocr_quality_triage",
        "route_id": job["route_id"],
        "page": source.get("page"),
        "reason": job.get("reason") or "",
        "suspect_text": suspect,
        "nearby_context": context,
        "structural_prefilter": structure,
        "document_internal_candidate_evidence": recall_evidence,
    }
    system = (
        "You are a strict OCR quality triage verifier for technical manuals. "
        "Judge only whether SUSPECT TEXT is structurally corrupted by OCR. Nearby text is context only. "
        "Technical-looking text is NOT corruption: units with numbers, tolerances, part/model numbers, "
        "specification codes, terminal/wire identifiers, abbreviations and formulas are normal technical formats. "
        "Do not repair or rewrite the text. Do not use outside engineering knowledge. "
        "Return JSON only with verdict (LIKELY_CORRUPT, LIKELY_OK, or UNCERTAIN), confidence (0 to 1), "
        "reason_code (OCR_GARBLE, TECHNICAL_FORMAT, CLEAN_PROSE, or AMBIGUOUS), and evidence. "
        "EVIDENCE MUST BE THE SHORTEST VERBATIM SUPPORTING SPAN FROM SUSPECT TEXT ONLY, maximum 120 characters. "
        "Never copy a whole paragraph/table and never use nearby context as evidence."
    )
    user = (
        f"ROUTE REASON: {logical['reason']}\n\n"
        f"DETERMINISTIC STRUCTURAL PREFILTER (advisory; do not contradict source):\n{json.dumps(structure, ensure_ascii=False)}\n\n"
        f"DOCUMENT-INTERNAL OCR CANDIDATE EVIDENCE (advisory only; a frequent variant is not proof):\n{json.dumps(recall_evidence, ensure_ascii=False)}\n\n"
        f"SUSPECT TEXT:\n{suspect}\n\n"
        f"NEARBY TEXT:\n{context or '(none)'}"
    )
    return system, {**logical, "system_instruction": system, "user_prompt": user}


VISION_CATEGORIES_TEXT = ", ".join(sorted(ALL_DIAGRAM_CATEGORIES))


def _vision_prompt(job: dict[str, Any], region: str = "full image") -> str:
    return (
        "Inspect only what is visibly present in this technical-manual image. "
        "Do not infer hidden wiring, hydraulic function, object identity, or symbol meaning from outside knowledge. "
        "A schematic, wiring diagram, hydraulic/pneumatic diagram, block/control/terminal diagram, mechanical section, "
        "exploded view or relationship diagram is TECHNICAL_USEFUL even when it has few or no dimensions or readable labels. "
        "A book/manual cover, title page, publisher page, or promotional page is cover_art and DECORATIVE_OR_LOW_VALUE, "
        "even when it contains a photograph or illustration of technical equipment. "
        "Return JSON only with: verdict (TECHNICAL_USEFUL, DECORATIVE_OR_LOW_VALUE, or UNCERTAIN), confidence (0 to 1), "
        "visible_text (array, up to 8 short exact strings actually legible in the image; prioritize technical labels), "
        "visible_objects (array, max 4 short model descriptions of visible objects/structures; these are NOT extracted text), "
        f"diagram_category (exactly one of: {VISION_CATEGORIES_TEXT}), "
        "summary (max 25 words describing only visible structure), unresolved (true/false), and unresolved_reason (CLASSIFICATION, UNREADABLE_DETAILS, OMITTED_DETAILS, or empty). "
        "Keep the whole JSON compact. Mark unresolved true if important labels or details remain unreadable or omitted. "
        "Never put inferred object descriptions into visible_text. "
        f"Region: {region}. Stage-2 route reason: {job.get('reason') or 'visual ambiguity'}."
    )


def _normalize_evidence_text(value: str) -> str:
    """Normalize representation details without semantic/fuzzy matching."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    # Quotation marks are presentation, not evidence content. Keep engineering
    # symbols such as %, °, Λ, / and - so a context-only symbol still fails.
    text = text.translate(str.maketrans("", "", "\"'‘’“”"))
    # OCR/model output often differs only by spaces around punctuation/units.
    text = re.sub(r"\s*([^\w\s])\s*", r"\1", text)
    return " ".join(text.split())


def _recover_source_evidence(model_evidence: str, suspect_text: str) -> tuple[str, str | None]:
    """Recover a compact exact-source span from verbose/wrapped model evidence.

    This is deliberately not fuzzy matching. Every accepted candidate must,
    after harmless representation normalization, occur literally inside the
    SUSPECT TEXT. It only repairs presentation mistakes such as an explanatory
    wrapper, quotes, or a copied paragraph longer than the 120-character audit
    limit.
    """
    raw = str(model_evidence or "").strip()
    suspect = str(suspect_text or "")
    if not raw or not suspect:
        return "", None
    suspect_norm = _normalize_evidence_text(suspect)

    quoted: list[str] = []
    for match in re.finditer(r'''["'‘’“”]([^"'‘’“”]{1,500})["'‘’“”]''', raw):
        candidate = match.group(1).strip()
        norm = _normalize_evidence_text(candidate)
        if norm and norm in suspect_norm:
            quoted.append(candidate)
    if quoted:
        candidate = min(quoted, key=len)
        if len(candidate) <= PI5_EVIDENCE_MAX_CHARS:
            return candidate, "QUOTED_SOURCE_SPAN"

    raw_norm = _normalize_evidence_text(raw)
    if raw_norm and raw_norm in suspect_norm and len(raw) <= PI5_EVIDENCE_MAX_CHARS:
        return raw, None

    token_matches = list(re.finditer(r"\S+", suspect))
    best = ""
    max_window = min(16, len(token_matches))
    for size in range(max_window, 0, -1):
        for start in range(0, len(token_matches) - size + 1):
            a = token_matches[start].start()
            b = token_matches[start + size - 1].end()
            candidate = suspect[a:b].strip()
            if not candidate or len(candidate) > PI5_EVIDENCE_MAX_CHARS:
                continue
            if len(re.sub(r"[^\w]+", "", candidate, flags=re.UNICODE)) < 4:
                continue
            norm = _normalize_evidence_text(candidate)
            if norm and norm in raw_norm:
                best = candidate
                break
        if best:
            break
    if best:
        method = "TRIMMED_EXACT_SOURCE_SPAN" if len(raw) > PI5_EVIDENCE_MAX_CHARS else "WRAPPED_SOURCE_SPAN"
        return best, method
    return raw[:PI5_EVIDENCE_MAX_CHARS].rstrip(), None


def _recall_evidence_points_to_model_span(recall_evidence: list[dict[str, Any]], model_evidence: str) -> bool:
    """Require the model to actually point at the Stage 2A observed anomaly.

    A rare-near-frequent candidate is advisory and may be a legitimate word
    (e.g. blocking/locking). It may override the low structural garble score
    only when Pi5's own source-grounded evidence contains the observed form.
    Strong non-lexical recall signals remain review candidates but do not get
    this corruption override automatically.
    """
    evidence_norm = _normalize_evidence_text(model_evidence)
    if not evidence_norm:
        return False
    for item in recall_evidence or []:
        observed = str(item.get("observed") or "").strip()
        if not observed:
            continue
        observed_norm = _normalize_evidence_text(observed)
        if observed_norm and observed_norm in evidence_norm:
            return True
    return False


def _recover_pi5_partial_json(raw_response: dict[str, Any]) -> dict[str, Any] | None:
    """Salvage fully closed Pi5 fields from otherwise truncated JSON."""
    try:
        content = raw_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if isinstance(content, list):
        content = "\n".join(str(item.get("text") or "") if isinstance(item, dict) else str(item) for item in content)
    text = str(content or "")
    if not text:
        return None

    def string_field(name: str) -> str | None:
        m = re.search(rf'"{re.escape(name)}"\s*:\s*("(?:\\.|[^"\\])*")', text, flags=re.I | re.S)
        if not m:
            return None
        try:
            return str(json.loads(m.group(1)))
        except (json.JSONDecodeError, TypeError):
            return None

    verdict = string_field("verdict")
    reason = string_field("reason_code")
    evidence = string_field("evidence")
    cm = re.search(r'"confidence"\s*:\s*(-?(?:\d+(?:\.\d*)?|\.\d+))', text, flags=re.I)
    if not (verdict and reason and evidence is not None and cm):
        return None
    try:
        confidence = float(cm.group(1))
    except ValueError:
        return None
    return {
        "verdict": verdict,
        "confidence": confidence,
        "reason_code": reason,
        "evidence": evidence,
        "recovered_from_partial_json": True,
    }


def _validate_pi5(
    parsed: dict[str, Any],
    suspect_text: str | None = None,
    structure: dict[str, Any] | None = None,
    recall_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    model_verdict = str(parsed.get("verdict") or "").upper()
    verdict = model_verdict if model_verdict in PI5_VERDICTS else "UNCERTAIN"
    model_reason_code = str(parsed.get("reason_code") or "AMBIGUOUS").upper()[:80]
    if model_reason_code == "UNIT_SYMBOL":
        model_reason_code = "TECHNICAL_FORMAT"
    model_evidence = str(parsed.get("evidence") or "").strip()
    evidence = model_evidence[:PI5_EVIDENCE_MAX_CHARS].rstrip()
    evidence_recovery = None
    confidence = _safe_float(parsed.get("confidence"))
    structure = structure or (analyze_text_structure(suspect_text or "") if suspect_text is not None else None)
    recall_evidence = recall_evidence or []

    evidence_valid: bool | None = None
    validation_note = ""
    if suspect_text is not None:
        evidence, evidence_recovery = _recover_source_evidence(model_evidence, suspect_text)
        normalized_evidence = _normalize_evidence_text(evidence)
        normalized_suspect = _normalize_evidence_text(suspect_text)
        evidence_valid = bool(normalized_evidence) and normalized_evidence in normalized_suspect
        if not evidence_valid:
            verdict = "UNCERTAIN"
            confidence = min(confidence, 0.25)
            validation_note = "Model evidence was not found in SUSPECT TEXT; decisive verdict rejected."

    deterministic_override = None
    recall_supported_corruption = (
        bool(recall_evidence)
        and model_reason_code == "OCR_GARBLE"
        and evidence_valid is True
        and _recall_evidence_points_to_model_span(recall_evidence, model_evidence)
    )
    if evidence_valid is not False and structure:
        tech = structure.get("technical_format") or {}
        garble = structure.get("garble") or {}
        if model_verdict == "LIKELY_CORRUPT" and bool(garble.get("structurally_normal")):
            if bool(tech.get("dominant")):
                verdict = "LIKELY_OK"
                confidence = max(0.75, 1.0 - float(garble.get("score") or 0.0))
                deterministic_override = "CLEAN_TECHNICAL_FORMAT"
                validation_note = "Deterministic structural gate rejected technical-format-as-corruption."
            elif recall_supported_corruption:
                # Generic Stage 2A OCR-recall candidates are specifically meant
                # to catch plausible alphabetic OCR errors whose structural
                # score is low. Keep the model's source-grounded corruption
                # verdict for human review; the low garble score still prevents
                # automatic Stage 2C application.
                verdict = "LIKELY_CORRUPT"
                deterministic_override = "DOCUMENT_RECALL_CORROBORATED_OCR"
                validation_note = "Document-internal OCR candidate plus source-grounded Pi5 evidence retained for human review."
            else:
                verdict = "UNCERTAIN"
                confidence = min(confidence, 0.5)
                deterministic_override = "NORMAL_STRUCTURE_MODEL_DISAGREEMENT"
                validation_note = "Structurally normal text conflicted with model corruption verdict; downgraded to UNCERTAIN."

    reason_code = "INVALID_EVIDENCE" if evidence_valid is False else model_reason_code
    if deterministic_override == "CLEAN_TECHNICAL_FORMAT":
        reason_code = "TECHNICAL_FORMAT"
    return {
        "verdict": verdict,
        "confidence": confidence,
        "reason_code": reason_code,
        "evidence": evidence,
        "model_evidence_char_count": len(model_evidence),
        "evidence_truncated_for_storage": len(model_evidence) > PI5_EVIDENCE_MAX_CHARS,
        "evidence_recovery": evidence_recovery,
        "evidence_valid": evidence_valid,
        "model_verdict": model_verdict or "UNKNOWN",
        "model_reason_code": model_reason_code,
        "validation_note": validation_note,
        "deterministic_override": deterministic_override,
        "document_recall_supported": bool(recall_evidence),
        "structural_analysis": structure,
    }


def _validate_vision(parsed: dict[str, Any]) -> dict[str, Any]:
    verdict = str(parsed.get("verdict") or "").upper()
    if verdict not in VISION_VERDICTS:
        verdict = "UNCERTAIN"
    # Old OnePlus responses used `visible_labels` for both literal text and
    # generated object descriptions. Those labels are ambiguous and must never
    # be promoted into source-like `visible_text` during Stage 2C backfill.
    legacy_schema = (
        "visible_text" not in parsed
        and "visible_objects" not in parsed
        and "diagram_category" not in parsed
        and "visible_labels" in parsed
    )
    legacy_visible_labels = parsed.get("visible_labels") or [] if legacy_schema else []
    if not isinstance(legacy_visible_labels, list):
        legacy_visible_labels = []
    visible_text = parsed.get("visible_text") or []
    if not isinstance(visible_text, list):
        visible_text = []
    visible_objects = parsed.get("visible_objects") or []
    if not isinstance(visible_objects, list):
        visible_objects = []
    category = str(parsed.get("diagram_category") or "unknown").strip().lower().replace(" ", "_")
    if category not in ALL_DIAGRAM_CATEGORIES:
        category = "unknown"
    full_image = parsed.get("full_image") if isinstance(parsed.get("full_image"), dict) else {}
    summary = str(parsed.get("summary") or full_image.get("summary") or "")[:600]
    return {
        "verdict": verdict,
        "confidence": _safe_float(parsed.get("confidence")),
        "visible_text": [str(item)[:120] for item in visible_text[:12]],
        "visible_labels": [str(item)[:120] for item in visible_text[:12]],  # new-schema compatibility only
        "legacy_visible_labels": [str(item)[:160] for item in legacy_visible_labels[:20]],
        "legacy_schema": legacy_schema,
        "visible_objects": [str(item)[:160] for item in visible_objects[:10]],
        "diagram_category": category,
        "summary": summary,
        "unresolved": bool(parsed.get("unresolved", verdict == "UNCERTAIN")),
        "unresolved_reason": str(parsed.get("unresolved_reason") or "")[:300],
    }


def _should_crop_vision(full: dict[str, Any], enabled: bool) -> bool:
    # If the full-image model response itself could not be parsed after the
    # one repair attempt, complete the route conservatively as UNCERTAIN.
    # Starting up to eight more crop calls here can consume the entire route
    # budget and turn a format problem back into a timeout/retry loop.
    return bool(enabled) and not bool(full.get("parse_failed")) and (
        full.get("verdict") == "UNCERTAIN" or bool(full.get("unresolved"))
    )


def _merge_vision(full: dict[str, Any], crops: list[dict[str, Any]]) -> dict[str, Any]:
    all_results = [full] + crops
    visible_text: list[str] = []
    visible_objects: list[str] = []
    seen_text: set[str] = set()
    seen_objects: set[str] = set()
    legacy_visible_labels: list[str] = []
    seen_legacy: set[str] = set()
    categories: list[str] = []
    for result in all_results:
        category = str(result.get("diagram_category") or "unknown")
        if category != "unknown":
            categories.append(category)
        for label in result.get("visible_text") or []:
            key = str(label).casefold().strip()
            if key and key not in seen_text:
                seen_text.add(key)
                visible_text.append(str(label))
        for label in result.get("legacy_visible_labels") or []:
            key = str(label).casefold().strip()
            if key and key not in seen_legacy:
                seen_legacy.add(key)
                legacy_visible_labels.append(str(label))
        for obj in result.get("visible_objects") or []:
            key = str(obj).casefold().strip()
            if key and key not in seen_objects:
                seen_objects.add(key)
                visible_objects.append(str(obj))
    if any(item.get("verdict") == "TECHNICAL_USEFUL" for item in all_results):
        verdict = "TECHNICAL_USEFUL"
    elif all(item.get("verdict") == "DECORATIVE_OR_LOW_VALUE" for item in all_results):
        verdict = "DECORATIVE_OR_LOW_VALUE"
    else:
        verdict = "UNCERTAIN"
    confidences = [float(item.get("confidence") or 0) for item in all_results if item.get("verdict") == verdict]
    # Prefer a technical diagram category over generic/decorative categories if
    # any region confidently saw one; structural corroboration is applied later.
    category = "unknown"
    for candidate in categories:
        if candidate in TECHNICAL_DIAGRAM_CATEGORIES:
            category = candidate
            break
    if category == "unknown" and categories:
        category = categories[0]
    return {
        "verdict": verdict,
        "confidence": round(max(confidences) if confidences else 0.0, 4),
        "visible_text": visible_text[:12],
        "visible_labels": visible_text[:12],
        "legacy_visible_labels": legacy_visible_labels[:20],
        "legacy_schema": any(bool(item.get("legacy_schema")) for item in all_results),
        "visible_objects": visible_objects[:10],
        "diagram_category": category,
        "summary": full.get("summary") or next((item.get("summary") for item in crops if item.get("summary")), ""),
        "unresolved": any(item.get("unresolved", True) for item in all_results),
        "full_image": full,
        "crops": crops,
        "crop_count": len(crops),
    }


def _apply_vision_structural_gate(merged: dict[str, Any], structure: dict[str, Any]) -> dict[str, Any]:
    result = dict(merged)
    category = str(result.get("diagram_category") or "unknown")
    diagram_like = bool(structure.get("diagram_like"))
    confidence = float(result.get("confidence") or 0)
    if category in TECHNICAL_DIAGRAM_CATEGORIES and confidence >= 0.55 and diagram_like:
        if result.get("verdict") != "TECHNICAL_USEFUL":
            result["model_merged_verdict"] = result.get("verdict")
            result["verdict"] = "TECHNICAL_USEFUL"
            result["confidence"] = max(confidence, 0.70)
            result["deterministic_override"] = "STRUCTURALLY_CORROBORATED_TECHNICAL_DIAGRAM"
    elif result.get("verdict") == "DECORATIVE_OR_LOW_VALUE" and diagram_like:
        # A line-dominant technical-looking source image must never be silently
        # discarded merely because the vision model called it decorative. Keep
        # the disagreement for the read-only audit / human review path.
        result["model_merged_verdict"] = result.get("verdict")
        result["verdict"] = "UNCERTAIN"
        result["unresolved"] = True
        result["deterministic_override"] = "STRUCTURAL_DIAGRAM_CONFLICT_REQUIRES_REVIEW"
    result["structural_image_evidence"] = structure
    return result


def _stream_response_truncated(raw: dict[str, Any]) -> bool:
    stream = raw.get("_stream") if isinstance(raw, dict) else None
    return bool(isinstance(stream, dict) and stream.get("finish_reason") == "length")


async def _inspect_vision_region(
    client: OpenAICompatibleVerifier,
    image_bytes: bytes,
    prompt: str,
    mime: str,
    model: str | None,
    max_tokens: int,
    *,
    first_token_timeout_seconds: int | None = None,
    stream_idle_timeout_seconds: int | None = None,
    on_progress: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Inspect one image region with one JSON-format repair attempt.

    Production OnePlus calls use streaming when the client supports it. This
    lets the worker wait through slow phone-side vision evaluation, observe
    generated chunks, and finish from the protocol's finish_reason/[DONE]
    instead of a fixed 240-second read timeout. Test/legacy clients can still
    use the non-streaming ``inspect_image`` method.
    """
    attempts: list[dict[str, Any]] = []

    async def call(current_prompt: str) -> dict[str, Any]:
        use_stream = (
            first_token_timeout_seconds is not None
            and stream_idle_timeout_seconds is not None
            and hasattr(client, "inspect_image_stream")
        )
        if use_stream:
            return await client.inspect_image_stream(
                image_bytes,
                current_prompt,
                mime_type=mime,
                model=model,
                max_tokens=max_tokens,
                first_token_timeout_seconds=int(first_token_timeout_seconds),
                idle_timeout_seconds=int(stream_idle_timeout_seconds),
                on_progress=on_progress,
            )
        return await client.inspect_image(
            image_bytes, current_prompt, mime_type=mime, model=model, max_tokens=max_tokens
        )

    def parse(raw_response: dict[str, Any]) -> dict[str, Any]:
        if _stream_response_truncated(raw_response):
            raise ValueError("Verifier stopped at max_tokens before a clean completion")
        return _validate_vision(_json_from_model_response(raw_response))

    raw = await call(prompt)
    attempts.append(raw)
    try:
        return parse(raw), raw, attempts
    except ValueError:
        repair_prompt = (
            prompt
            + "\n\nYour previous response was not valid/complete JSON. Return ONLY one complete JSON object now. "
              "Do not use markdown fences and do not include thinking text. Keep it compact; shorten visible_text, visible_objects, and summary rather than truncating JSON."
        )
        raw_retry = await call(repair_prompt)
        attempts.append(raw_retry)
        try:
            return parse(raw_retry), raw_retry, attempts
        except ValueError as exc:
            # A model-format failure is not a transport or inference failure.
            # Preserve both raw attempts and return a conservative result so a
            # useful route is not permanently lost solely because JSON was bad.
            fallback = _validate_vision({
                "verdict": "UNCERTAIN",
                "confidence": 0.0,
                "visible_text": [],
                "visible_objects": [],
                "diagram_category": "unknown",
                "summary": "",
                "unresolved": True,
                "unresolved_reason": "MODEL_RESPONSE_PARSE_FAILED",
            })
            fallback["parse_failed"] = True
            fallback["parse_error"] = str(exc)[:500]
            fallback["parse_attempt_count"] = len(attempts)
            return fallback, raw_retry, attempts



def _response_finish_reason(raw: dict[str, Any]) -> str | None:
    try:
        value = raw["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return str(value) if value is not None else None


async def _inspect_pi5_text(
    client: OpenAICompatibleVerifier,
    system: str,
    user: str,
    suspect: str,
    model: str | None,
    max_tokens: int,
    structure: dict[str, Any] | None = None,
    recall_evidence: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Run Pi5 OCR triage with one compact JSON repair attempt.

    A malformed/truncated model response is a formatting failure, not proof that
    the route itself failed. After one repair reprompt, preserve both raw
    attempts and return a conservative UNCERTAIN result rather than permanently
    failing the verification job.
    """
    attempts: list[dict[str, Any]] = []

    async def call(current_system: str, current_user: str) -> dict[str, Any]:
        return await _chat_text_json(
            client, current_system, current_user, model=model, max_tokens=max_tokens,
            schema_name="ocr_quality_triage", schema=TEXT_TRIAGE_SCHEMA,
        )

    def parse(raw_response: dict[str, Any]) -> dict[str, Any]:
        recovered_partial = False
        try:
            parsed_obj = _json_from_model_response(raw_response)
        except ValueError:
            parsed_obj = _recover_pi5_partial_json(raw_response)
            if parsed_obj is None:
                raise
            recovered_partial = True
        # A length finish may still be safely usable when the required JSON
        # object/fields are already complete. If they are not, parsing above
        # fails and the normal bounded repair prompt is used.
        validated = _validate_pi5(parsed_obj, suspect, structure, recall_evidence=recall_evidence)
        if recovered_partial:
            validated["recovered_from_partial_json"] = True
        return validated

    raw = await call(system, user)
    attempts.append(raw)
    try:
        return parse(raw), raw, attempts
    except ValueError:
        repair_system = (
            system
            + " Your previous response was malformed or incomplete. "
              "Return ONLY one compact complete JSON object. No markdown, no thinking text, no explanation."
        )
        repair_user = (
            user
            + "\n\nFORMAT REPAIR: Output exactly one compact JSON object with keys "
              "verdict, confidence, reason_code, evidence. Evidence must be one exact span from SUSPECT TEXT and <=120 characters."
        )
        raw_retry = await call(repair_system, repair_user)
        attempts.append(raw_retry)
        try:
            return parse(raw_retry), raw_retry, attempts
        except ValueError as exc:
            fallback = {
                "verdict": "UNCERTAIN",
                "confidence": 0.0,
                "reason_code": "AMBIGUOUS",
                "evidence": "",
                "evidence_valid": None,
                "model_verdict": "UNKNOWN",
                "model_reason_code": "AMBIGUOUS",
                "validation_note": "Model response could not be parsed after one compact JSON repair attempt.",
                "parse_failed": True,
                "parse_error": str(exc)[:500],
                "parse_attempt_count": len(attempts),
                "fallback_reason": "MODEL_RESPONSE_PARSE_FAILED",
            }
            return fallback, raw_retry, attempts


def _text_segments(text: str, limit: int = 1800) -> list[str]:
    """Preserve every character and keep whitespace-delimited identifiers intact."""
    segments, current = [], ""
    for token in re.findall(r"[+-]?(?:\d+(?:[.,]\d+)?)\s+[^\s]+\s*|\S+\s*|\s+", text):
        if current and len(current) + len(token) > limit:
            segments.append(current)
            current = ""
        current += token
    if current:
        segments.append(current)
    return segments or [""]


async def _inspect_pi5_bounded(client, job, suspect, context, model, max_tokens):
    segments = _text_segments(suspect)
    results, raw_results, attempts = [], [], []
    for segment in segments:
        if len(segment) > 1800:
            results.append({"verdict": "UNCERTAIN", "confidence": 0,
                            "reason_code": "OVERSIZED_TOKEN_REQUIRES_REVIEW"})
            continue
        structure = analyze_text_structure(segment)
        system, logical = _pi5_prompt_payload(job, segment, context[:1000], structure)
        try:
            source_meta = json.loads(job.get("source_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            source_meta = {}
        recall_evidence = source_meta.get("ocr_recall_evidence") or []
        parsed, raw, calls = await _inspect_pi5_text(
            client, system, logical["user_prompt"], segment, model, max_tokens, structure, recall_evidence,
        )
        results.append(parsed)
        raw_results.append(raw)
        attempts.extend(calls)
    if len(segments) == 1 and raw_results:
        return results[0], raw_results[0], attempts
    verdict = "LIKELY_OK" if all(r["verdict"] == "LIKELY_OK" for r in results) else "UNCERTAIN"
    return {
        "verdict": verdict, "confidence": min(r.get("confidence", 0) for r in results),
        "reason_code": "SEGMENTED_TEXT", "segment_count": len(segments),
        "checked_segments": len(raw_results), "segments": results,
        "coverage_complete": len(raw_results) == len(segments),
        "evidence_valid": None,
    }, {"segments": raw_results}, attempts


def _correction_scope_guard(original: str, context: str, proposed: str) -> dict[str, Any]:
    """Reject correction outputs that reproduce prompt/context scaffolding.

    Nearby context is advisory only. A correction must stay scoped to the
    original OCR span; it must not append neighboring paragraphs, headings, or
    prompt labels. This guard is intentionally generic and book-agnostic.
    """
    proposed_norm = " ".join(str(proposed or "").split())
    original_norm = " ".join(str(original or "").split())
    context_norm = " ".join(str(context or "").split())
    reasons: list[str] = []
    upper = proposed_norm.upper()
    if any(marker in upper for marker in (
        "NEARBY CONTEXT:", "ORIGINAL TEXT:", "SUSPECT TEXT:",
        "CONTEXT:", "CORRECTED TEXT:", "PI5 PROPOSAL:",
    )):
        reasons.append("PROMPT_OR_CONTEXT_MARKER_LEAKAGE")

    # Detect copying a substantial contiguous phrase from nearby context that
    # was not present in the original span. Six words is long enough to avoid
    # penalizing ordinary technical phrases while catching paragraph leakage.
    context_words = context_norm.split()
    proposed_fold = proposed_norm.casefold()
    original_fold = original_norm.casefold()
    for size in (8, 7, 6):
        if len(context_words) < size:
            continue
        leaked = False
        for start in range(0, len(context_words) - size + 1):
            phrase = " ".join(context_words[start:start + size]).casefold()
            if phrase and phrase in proposed_fold and phrase not in original_fold:
                leaked = True
                break
        if leaked:
            reasons.append("NEARBY_CONTEXT_COPIED_INTO_CORRECTION")
            break
    return {"accepted": not reasons, "reasons": reasons}


async def _attempt_pi5_correction(
    client: OpenAICompatibleVerifier,
    original: str,
    context: str,
    verification: dict[str, Any],
    model: str | None,
    config: Any,
) -> dict[str, Any]:
    """Conservative OCR correction/suggestion.

    Detection and correction are intentionally decoupled.  Any high-confidence,
    evidence-valid OCR_GARBLE finding may receive a correction *suggestion* for
    human review, even when the deterministic garble score is below the stricter
    automatic-apply threshold.  Automatic application still requires the full
    Stage 2C gate plus fidelity and Pi5 re-verification.
    """
    context = context[:1000]
    garble = ((verification.get("structural_analysis") or {}).get("garble") or {})
    suggestion_enabled = bool(getattr(config, "stage2c_correction_suggestions_enabled", True))
    proposal_eligible = (
        len(original) <= 1800
        and bool(config.stage2c_enabled)
        and bool(config.stage2c_text_correction_enabled)
        and suggestion_enabled
        and verification.get("verdict") == "LIKELY_CORRUPT"
        and verification.get("reason_code") == "OCR_GARBLE"
        and verification.get("evidence_valid") is True
        and float(verification.get("confidence") or 0) >= float(config.stage2c_correction_min_confidence)
    )
    cloud_auto = bool(_is_cloud_text_client(client) and getattr(config, "stage2c_cloud_auto_apply", True))
    automatic_eligible = (
        proposal_eligible
        and (
            cloud_auto
            or float(garble.get("score") or 0) >= float(config.stage2c_correction_min_garble_score)
        )
    )
    eligibility = {
        "eligible": automatic_eligible,
        "proposal_eligible": proposal_eligible,
        "automatic_apply_eligible": automatic_eligible,
        "cloud_auto_mode": cloud_auto,
        "verdict": verification.get("verdict"),
        "reason_code": verification.get("reason_code"),
        "evidence_valid": verification.get("evidence_valid"),
        "confidence": verification.get("confidence"),
        "garble_score": garble.get("score"),
        "thresholds": {
            "min_confidence": config.stage2c_correction_min_confidence,
            "min_garble_score": config.stage2c_correction_min_garble_score,
        },
    }
    if not proposal_eligible:
        return {
            "attempted": False, "status": "pending", "eligibility": eligibility,
            "reason": "CORRECTION_SUGGESTION_GATE_NOT_MET",
        }

    system = (
        "You are a conservative OCR reconstruction worker. Repair ONLY the characters inside ORIGINAL_SPAN. "
        "Make the SMALLEST POSSIBLE edit. Prefer fixing obvious OCR spacing/splitting artifacts such as "
        "'d on\'t' -> 'don\'t', 'don \' t' -> 'don\'t', 'system \' s' -> 'system\'s', or a split word "
        "like 'destroyin g' -> 'destroying'. Do not rewrite grammar or improve style when the source is otherwise readable. "
        "NEARBY_CONTEXT is read-only boundary help and MUST NEVER be copied, summarized, appended, or quoted in corrected_text. "
        "corrected_text must replace exactly the same span and contain no headings, prompt labels, surrounding paragraphs, "
        "page numbers, explanations, or extra context. Never invent or infer a missing number, unit-bearing value, tolerance, "
        "model/part number, terminal identifier, standard/spec code, or setting. If a technical value/identifier cannot be "
        "recovered from surviving ORIGINAL_SPAN characters, use [UNREADABLE] at that exact position. Preserve meaning, order, "
        "punctuation and surviving characters. Return ONE compact JSON object only with corrected_text and confidence."
    )
    user = (
        "<ORIGINAL_SPAN>\n" + original + "\n</ORIGINAL_SPAN>\n\n"
        "<NEARBY_CONTEXT_READ_ONLY>\n" + (context or "(none)") + "\n</NEARBY_CONTEXT_READ_ONLY>"
    )
    raw = None
    attempts: list[dict[str, Any]] = []
    try:
        parsed: dict[str, Any] | None = None
        proposed = ""
        fidelity: dict[str, Any] = {"accepted": False, "reasons": ["NO_CANDIDATE"]}
        scope: dict[str, Any] = {"accepted": False, "reasons": ["NO_CANDIDATE"]}
        parse_errors: list[str] = []

        for attempt_index in range(2):
            current_system = system
            current_user = user
            if attempt_index == 1:
                current_system += (
                    " Your previous attempt was malformed, out of scope, or failed deterministic fidelity. "
                    "Try once more. Output ONLY the replacement for ORIGINAL_SPAN in corrected_text. "
                    "Make only minimal OCR repairs. Do not copy any NEARBY_CONTEXT. "
                    "If uncertain, preserve source characters or use [UNREADABLE]."
                )
                current_user = "<ORIGINAL_SPAN>\n" + original + "\n</ORIGINAL_SPAN>"

            raw = await _chat_text_json(
                client, current_system, current_user, model=model,
                max_tokens=int(config.stage2c_pi5_correction_max_tokens),
                schema_name="ocr_correction", schema=TEXT_CORRECTION_SCHEMA,
            )
            attempts.append(raw)
            try:
                if _response_finish_reason(raw) == "length":
                    raise ValueError("Correction response truncated at max_tokens")
                parsed = _json_from_model_response(raw)
                proposed = str(parsed.get("corrected_text") or "").strip()
                scope = _correction_scope_guard(original, context, proposed)
                fidelity = correction_fidelity(
                    original,
                    proposed,
                    min_similarity=float(config.stage2c_correction_min_similarity),
                    min_length_ratio=float(config.stage2c_correction_min_length_ratio),
                    max_length_ratio=float(config.stage2c_correction_max_length_ratio),
                )
                if scope.get("accepted") and fidelity.get("accepted"):
                    break
            except (ValueError, KeyError, TypeError) as exc:
                parse_errors.append(f"{type(exc).__name__}: {exc}"[:500])
                parsed = None

        if parsed is None:
            raise ValueError(parse_errors[-1] if parse_errors else "Correction response could not be parsed")

        if not scope.get("accepted") or not fidelity.get("accepted"):
            rejection_reasons = list(scope.get("reasons") or []) + list(fidelity.get("reasons") or [])
            return {
                "attempted": True, "status": "rejected", "eligibility": eligibility,
                "proposed_text": proposed, "model_confidence": _safe_float(parsed.get("confidence")),
                "model_note": str(parsed.get("note") or "")[:500], "fidelity": fidelity,
                "scope_guard": scope, "correction_attempt_count": len(attempts),
                "correction_parse_errors": parse_errors,
                "raw_response": raw, "raw_attempts": attempts,
                "reason": "CORRECTION_SCOPE_OR_FIDELITY_GATE_REJECTED",
                "rejection_reasons": rejection_reasons,
            }

        # Below the automatic garble threshold, a good candidate is still useful
        # to the human reviewer.  Keep it proposal-only and do not spend a second
        # verifier pass pretending it is eligible for automatic application.
        if not automatic_eligible:
            return {
                "attempted": True,
                "status": "proposed",
                "eligibility": eligibility,
                "proposed_text": proposed,
                "model_confidence": _safe_float(parsed.get("confidence")),
                "model_note": str(parsed.get("note") or "")[:500],
                "fidelity": fidelity,
                "scope_guard": scope,
                "correction_attempt_count": len(attempts),
                "correction_parse_errors": parse_errors,
                "raw_response": raw,
                "raw_attempts": attempts,
                "suggestion_only": True,
                "reason": "HUMAN_REVIEW_SUGGESTION_BELOW_AUTO_GATE",
            }

        if cloud_auto:
            review_model = str(getattr(config, "text_cloud_fallback_model", "") or model or "")
            review_system = (
                "You are the second-pass OCR correction verifier. Decide whether PROPOSED_TEXT is a minimal, safe OCR "
                "reconstruction of ORIGINAL_OCR in this local context. General spelling/grammar knowledge may be used only "
                "to recognize OCR mistakes. Never invent engineering facts, numbers, units, tolerances, identifiers, model/part "
                "numbers, terminal/wire tags, standards or hidden words. If the original could reasonably be valid as written, "
                "or the correction changes meaning rather than OCR form, return REJECT or UNCERTAIN. Evidence must be the "
                "shortest exact span from ORIGINAL_OCR that supports the decision."
            )
            review_user = (
                "ORIGINAL_OCR:\n" + original + "\n\nPROPOSED_TEXT:\n" + proposed +
                "\n\nNEARBY_CONTEXT_READ_ONLY:\n" + (context or "(none)")
            )
            reverify_raw = await _chat_text_json(
                client, review_system, review_user, model=review_model,
                max_tokens=int(config.stage2b_pi5_max_tokens),
                schema_name="ocr_correction_second_pass", schema=TEXT_CORRECTION_REVIEW_SCHEMA,
            )
            review_obj = _json_from_model_response(reverify_raw)
            review_verdict = str(review_obj.get("verdict") or "UNCERTAIN").upper()
            review_confidence = _safe_float(review_obj.get("confidence"))
            review_evidence, review_recovery = _recover_source_evidence(
                str(review_obj.get("evidence") or ""), original
            )
            review_evidence_valid = bool(_normalize_evidence_text(review_evidence)) and (
                _normalize_evidence_text(review_evidence) in _normalize_evidence_text(original)
            )
            reverified = {
                "verdict": review_verdict,
                "confidence": review_confidence,
                "evidence": review_evidence,
                "evidence_valid": review_evidence_valid,
                "evidence_recovery": review_recovery,
                "model": review_model,
                "provider": "groq",
            }
            applied = (
                review_verdict == "SUPPORTED"
                and review_evidence_valid
                and review_confidence >= float(getattr(config, "stage2c_cloud_secondary_min_confidence", 0.90))
            )
            if applied:
                correction_status = "applied"
                correction_reason = "CLOUD_SECOND_PASS_SUPPORTED"
            elif review_verdict == "REJECT":
                correction_status = "rejected"
                correction_reason = "CLOUD_SECOND_PASS_REJECTED_KEEP_ORIGINAL"
            else:
                correction_status = "pending"
                correction_reason = "CLOUD_UNRESOLVED_KEEP_ORIGINAL"
            return {
                "attempted": True, "status": correction_status, "eligibility": eligibility,
                "proposed_text": proposed, "model_confidence": _safe_float(parsed.get("confidence")),
                "fidelity": fidelity, "scope_guard": scope, "correction_attempt_count": len(attempts),
                "correction_parse_errors": parse_errors, "reverification": reverified,
                "raw_response": raw, "raw_attempts": attempts, "reverify_raw_response": reverify_raw,
                "reverify_attempts": [reverify_raw], "suggestion_only": False, "reason": correction_reason,
                "keep_original_if_not_applied": not applied,
            }

        corrected_structure = analyze_text_structure(proposed)
        verify_system, verify_logical = _pi5_prompt_payload(
            {"route_id": "CORRECTION_REVERIFY", "source_json": json.dumps({"page": None}), "reason": "correction_reverification"},
            proposed, context, corrected_structure,
        )
        reverified, reverify_raw, reverify_attempts = await _inspect_pi5_text(
            client, verify_system, verify_logical["user_prompt"], proposed, model,
            int(config.stage2b_pi5_max_tokens), corrected_structure,
        )
        applied = reverified.get("verdict") == "LIKELY_OK" and reverified.get("evidence_valid") is not False
        if applied:
            correction_status = "applied"
            correction_reason = "REVERIFIED_CLEAN"
        elif reverified.get("verdict") == "LIKELY_CORRUPT":
            correction_status = "rejected"
            correction_reason = "REVERIFICATION_STILL_CORRUPT"
        else:
            correction_status = "proposed"
            correction_reason = "REVERIFICATION_UNCERTAIN_REVIEW_REQUIRED"
        return {
            "attempted": True, "status": correction_status, "eligibility": eligibility,
            "proposed_text": proposed, "model_confidence": _safe_float(parsed.get("confidence")),
            "model_note": str(parsed.get("note") or "")[:500], "fidelity": fidelity,
            "scope_guard": scope, "correction_attempt_count": len(attempts),
            "correction_parse_errors": parse_errors, "reverification": reverified,
            "raw_response": raw, "raw_attempts": attempts, "reverify_raw_response": reverify_raw,
            "reverify_attempts": reverify_attempts, "suggestion_only": False, "reason": correction_reason,
        }
    except (httpx.HTTPError, TimeoutError, ConnectionError, ValueError, KeyError, TypeError) as exc:
        return {
            "attempted": True,
            "status": "pending",
            "eligibility": eligibility,
            "reason": "CORRECTOR_UNAVAILABLE_OR_UNPARSEABLE",
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:1000],
            "raw_response": raw,
            "raw_attempts": attempts,
            "correction_attempt_count": len(attempts),
        }



class Stage2BWorker:
    def __init__(
        self,
        config_getter: Any,
        store: Stage2BStore,
        postprocess_store: PostprocessStore,
        events: EventBroker,
        groq_quota: GroqQuotaGuard | None = None,
    ) -> None:
        self._config_getter = config_getter
        self._store = store
        self._postprocess_store = postprocess_store
        self._events = events
        self._groq_quota = groq_quota
        self._stopping = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self.worker_state: dict[str, dict[str, Any]] = {
            "pi5": {"active_job_id": None, "active_stage": None, "last_completed_job_id": None},
            "oneplus": {
                "active_job_id": None,
                "active_stage": None,
                "last_completed_job_id": None,
                "active_started_epoch": None,
                "stream_phase": None,
                "stream_chunk_count": 0,
                "stream_content_chunk_count": 0,
                "stream_completion_tokens": None,
                "stream_first_content_seconds": None,
                "stream_last_activity_epoch": None,
                "stream_finish_reason": None,
                "stream_done_received": False,
            },
        }
        # Stage 2A already validated CRC/reference integrity. Cache only the
        # parsed Docling JSON here so a book with many routes is not reparsed
        # and CRC-scanned for every local-model call.
        self._doc_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
        self._device_locks = {"pi5": asyncio.Lock(), "oneplus": asyncio.Lock(), "groq": asyncio.Lock()}
        self._model_cache: dict[str, tuple[str, str | None]] = {}
        # Route discovery is shared by the background scanner and explicit
        # start actions. Coalesce concurrent calls and skip unchanged books so
        # read-only dashboard polling never turns into repeated disk/DB work.
        self._route_sync_lock = asyncio.Lock()
        self._route_sync_signatures: dict[int, tuple[Any, ...]] = {}
        # Pi5 and OnePlus can finish concurrently; serialize file-ledger updates.
        self._stage2c_ledger_lock = asyncio.Lock()
        # Stage 2C backfill is used for converted-folder imports whose Stage 2B
        # verification finished before the correction/enrichment stage existed.
        # It never reruns Docling or completed OnePlus/Pi5 verification.
        self._stage2c_backfill_tasks: dict[int, asyncio.Task[Any]] = {}
        self.stage2c_backfill_state: dict[int, dict[str, Any]] = {}
        self._manual_crosscheck_tasks: dict[int, asyncio.Task[Any]] = {}
        self.manual_crosscheck_state: dict[int, dict[str, Any]] = {}
        # Existing books from older releases may have completed Stage 2B
        # findings but no Pi5 correction proposal because the old 0.12 garble
        # gate prevented the corrector from running.  This independent worker
        # generates missing human-review suggestions without rerunning Stage 2B.
        self._correction_suggestion_tasks: dict[int, asyncio.Task[Any]] = {}
        self.correction_suggestion_state: dict[int, dict[str, Any]] = {}

    async def start(self) -> None:
        await self._store.initialize()
        await self._store.recover_interrupted()
        if not self._config_getter().stage2b_enabled:
            return
        self._tasks = [
            asyncio.create_task(self._discovery_loop(), name="stage2b-route-discovery"),
            asyncio.create_task(self._device_loop("pi5"), name="stage2b-pi5-worker"),
            asyncio.create_task(self._device_loop("oneplus"), name="stage2b-oneplus-worker"),
        ]

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for task in self._stage2c_backfill_tasks.values():
            task.cancel()
        if self._stage2c_backfill_tasks:
            await asyncio.gather(*self._stage2c_backfill_tasks.values(), return_exceptions=True)
        self._stage2c_backfill_tasks.clear()
        for task in self._manual_crosscheck_tasks.values():
            task.cancel()
        if self._manual_crosscheck_tasks:
            await asyncio.gather(*self._manual_crosscheck_tasks.values(), return_exceptions=True)
        self._manual_crosscheck_tasks.clear()
        for task in self._correction_suggestion_tasks.values():
            task.cancel()
        if self._correction_suggestion_tasks:
            await asyncio.gather(*self._correction_suggestion_tasks.values(), return_exceptions=True)
        self._correction_suggestion_tasks.clear()

    def _auto_run(self, target: str) -> bool:
        config = self._config_getter()
        return bool(config.stage2b_pi5_auto_run if target == "pi5" else config.stage2b_oneplus_auto_run)

    def _paused(self, target: str) -> bool:
        config = self._config_getter()
        return bool(config.stage2b_pi5_paused if target == "pi5" else config.stage2b_oneplus_paused)

    async def start_manual(self, target: str) -> int:
        if target not in {"pi5", "oneplus"}:
            raise ValueError("Unknown Stage 2B target")
        return await self._store.start_manual_batch(target)

    def manual_crosscheck_state_for(self, job_id: int) -> dict[str, Any] | None:
        state = self.manual_crosscheck_state.get(int(job_id))
        return dict(state) if state else None

    async def start_manual_crosscheck(self, job_id: int) -> dict[str, Any]:
        job_id = int(job_id)
        running = self._manual_crosscheck_tasks.get(job_id)
        if running and not running.done():
            return {"accepted": False, "reason": "already_running", **(self.manual_crosscheck_state_for(job_id) or {})}
        job = await self._store.get_job(job_id)
        if not job or job.get("status") != "completed" or job.get("target") not in {"pi5", "oneplus"}:
            raise ValueError("Only a completed Pi5 or OnePlus verification result can be cross-checked")
        opposite = "oneplus" if job.get("target") == "pi5" else "pi5"
        state = {
            "job_id": job_id,
            "source_target": job.get("target"),
            "crosscheck_target": opposite,
            "status": "queued",
            "verdict": None,
            "summary": "Waiting for the selected opposite verifier",
            "started_at_epoch": None,
            "completed_at_epoch": None,
            "error": None,
        }
        self.manual_crosscheck_state[job_id] = state
        task = asyncio.create_task(self._run_manual_crosscheck(job), name=f"manual-crosscheck-{job_id}")
        self._manual_crosscheck_tasks[job_id] = task
        self._events.notify("stage2b_manual_crosscheck_started")
        return {"accepted": True, **state}

    async def _persist_manual_crosscheck(self, job: dict[str, Any], payload: dict[str, Any]) -> None:
        config = self._config_getter()
        result_dir = Path(config.processed_dir) / Path(str(job.get("result_dir") or "")).name
        ledger_path = result_dir / "correction_ledger.json"
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            ledger = {}
        entry_type = "text_correction" if job.get("target") == "pi5" else "vision_enrichment"
        entry_id = f"{job.get('generation')}:{'text' if job.get('target') == 'pi5' else 'vision'}:{job.get('route_id')}"
        entry = next((item for item in ledger.get("entries") or [] if str(item.get("entry_id")) == entry_id), None)
        if not entry:
            return
        history = list(entry.get("manual_crosschecks") or [])
        history.append(payload)
        entry["manual_crosschecks"] = history[-10:]
        # Any explicitly selected vision-capable processor (Pi5, OnePlus,
        # Groq) performs direct source-page reconstruction. Apply a readable
        # result immediately unless a human-verified correction owns the entry.
        if (
            entry_type == "text_correction"
            and payload.get("direction") == "text_to_vision"
            and payload.get("direct_transcription")
            and not entry.get("human_verified")
        ):
            corrected = str(payload.get("corrected_text") or "").strip()
            if payload.get("verdict") == "READABLE" and corrected:
                entry["proposed_text"] = corrected
                entry["status"] = "applied"
                entry["status_reason"] = "SOURCE_IMAGE_TARGET_RECONSTRUCTION"
                entry["scope_guard"] = payload.get("scope_guard")
                entry["vision_direct_transcription"] = {
                    "provider": payload.get("provider"),
                    "model": payload.get("model"),
                    "applied_at_epoch": payload.get("checked_at_epoch"),
                    "manual_crossover": True,
                }
            elif payload.get("verdict") == "UNREADABLE":
                entry["proposed_text"] = None
                entry["status"] = "pending"
                entry["status_reason"] = "SOURCE_IMAGE_UNREADABLE_KEEP_ORIGINAL"
        await asyncio.to_thread(
            upsert_ledger_entry, result_dir, str(ledger.get("source_zip_sha256") or ""), entry
        )

    async def _run_manual_crosscheck(self, job: dict[str, Any]) -> None:
        job_id = int(job["id"])
        state = self.manual_crosscheck_state[job_id]
        config = self._config_getter()
        opposite = str(state["crosscheck_target"])
        state["status"] = "waiting_device"
        try:
            request = json.loads(job.get("request_json") or "{}")
            result = json.loads(job.get("result_json") or "{}")
            source = json.loads(job.get("source_json") or "{}")
            state["started_at_epoch"] = time.time()
            if job.get("target") == "pi5":
                original = str(request.get("suspect_text") or "")
                proposed = str(((result.get("correction") or {}).get("proposed_text") or ""))
                conversion = await self._postprocess_store.get_conversion_job(int(job.get("conversion_job_id") or 0))
                source_filename = str((conversion or {}).get("filename") or (conversion or {}).get("source_filename") or "")
                zip_path = Path(config.output_dir) / str(job.get("output_filename") or "")
                doc = await self._document_for(zip_path)
                source_page = int(source.get("page")) if source.get("page") is not None else None
                source_type = str(source.get("type") or "text")
                if source_type == "table_cell":
                    source_index = None
                    table_index = int(source.get("table_index")); cell_index = int(source.get("cell_index"))
                    target_image = await asyncio.to_thread(
                        _render_source_table_cell, config, source_filename, source_page, doc, table_index, cell_index
                    )
                else:
                    source_index = int(source.get("index"))
                    target_image = await asyncio.to_thread(
                        _render_source_target, config, source_filename, source_page, doc, source_index
                    )
                if not target_image:
                    raise ValueError("Original source target crop is unavailable for visual re-read")
                image_bytes, image_mime, crop_meta = target_image
                client = self._vision_client_for_role("oneplus", job)
                effective_provider = str(getattr(client, "provider", self._selected_provider("oneplus")) or self._selected_provider("oneplus"))
                endpoint = str(getattr(client, "endpoint", self._endpoint_for_provider(effective_provider)))
                model = await self._model_for(f"manual-vision:{effective_provider}", endpoint, client)
                vision_label = {"pi5": "Pi5 Vision", "oneplus": "OnePlus Vision", "groq": "Groq Vision"}.get(effective_provider, effective_provider)
                state["status"] = "running"
                state["summary"] = f"{vision_label} is reconstructing the target from the source page"
                before_anchors = request.get("before_anchors") if isinstance(request.get("before_anchors"), list) else []
                after_anchors = request.get("after_anchors") if isinstance(request.get("after_anchors"), list) else []
                if not before_anchors and not after_anchors:
                    try:
                        if source_type == "table_cell":
                            _target, before_anchors, after_anchors = _table_cell_context_parts(doc, table_index, cell_index)
                        else:
                            _target, before_anchors, after_anchors = _text_context_parts(doc, source_index, source_page)
                    except Exception:
                        before_anchors, after_anchors = [], []
                checked = await _oneplus_text_crosscheck(
                    client, image_bytes, image_mime, original, "", model,
                    first_token_timeout_seconds=config.stage2b_oneplus_first_token_timeout_seconds,
                    idle_timeout_seconds=config.stage2b_oneplus_stream_idle_timeout_seconds,
                    before_anchors=before_anchors, after_anchors=after_anchors,
                )
                direct_transcription = bool(checked.get("direct_transcription"))
                payload = {
                    "manual": True, "direction": "text_to_vision", "checked_at_epoch": time.time(),
                    "provider": effective_provider, "model": model,
                    "verdict": checked.get("verdict"), "corrected_text": checked.get("corrected_text") or "",
                    "usable": bool(checked.get("usable", True)), "direct_transcription": direct_transcription,
                    "finish_reason": checked.get("finish_reason"),
                    "truncated": bool(checked.get("truncated")), "parse_failed": bool(checked.get("parse_failed")),
                    "transport_failed": bool(checked.get("transport_failed")),
                    "target_crop": crop_meta,
                    "scope_guard": checked.get("scope_guard"),
                    "source_image_scope": "target_crop_only",
                    "note": "Selected vision processor reconstructs only the isolated target crop; READABLE applies, UNREADABLE preserves Docling.",
                }
                verdict = str(payload.get("verdict") or "UNREADABLE")
                summary = f"{vision_label}: {verdict}" + (f" · {payload['corrected_text'][:140]}" if payload.get("corrected_text") else "")
            else:
                zip_path = Path(config.output_dir) / str(job.get("output_filename") or "")
                doc = await self._document_for(zip_path)
                page_text = _docling_page_text(doc, source.get("page"), max_chars=5000)
                parsed = result.get("parsed") or {}
                candidate = {
                    "diagram_category": parsed.get("diagram_category"),
                    "visible_text": parsed.get("visible_text") or [],
                    "visible_objects": parsed.get("visible_objects") or [],
                    "summary": parsed.get("summary") or ((parsed.get("full_image") or {}).get("summary") if isinstance(parsed.get("full_image"), dict) else "") or "",
                }
                system = (
                    "You are a strict cross-checker. You cannot see the image. Compare the vision extraction only "
                    "against RAW DOCLING TEXT FROM THE SAME PAGE. Return JSON only with verdict SUPPORTED, "
                    "CONTRADICTED, or NOT_ENOUGH_EVIDENCE and evidence. Evidence must be a short verbatim span from "
                    "the raw Docling page text. Do not use outside knowledge and do not claim visual confirmation."
                )
                user = "RAW DOCLING PAGE TEXT:\n" + page_text[:5000] + "\n\nVISION EXTRACTION:\n" + json.dumps(candidate, ensure_ascii=False)
                client = self._client_for("pi5", job)
                endpoint = str(getattr(client, "endpoint", config.pi5_url))
                model = await self._model_for("pi5", endpoint, client)
                state["status"] = "running"
                selected_text_provider = str(getattr(client, "provider", self._selected_provider("pi5")) or self._selected_provider("pi5"))
                provider_label = {
                    "pi5": "Pi5 Text",
                    "oneplus": "OnePlus Text",
                    "groq": "Groq Text",
                }.get(selected_text_provider, f"{selected_text_provider} Text")
                state["summary"] = f"{provider_label} is checking vision extraction against Docling page text"
                raw = await _chat_text_json(
                    client, system, user, model=model, max_tokens=140,
                    schema_name="vision_text_crosscheck", schema=CROSSCHECK_SCHEMA,
                )
                checked = _json_from_model_response(raw)
                verdict = str(checked.get("verdict") or "NOT_ENOUGH_EVIDENCE").upper()
                if verdict not in {"SUPPORTED", "CONTRADICTED", "NOT_ENOUGH_EVIDENCE"}:
                    verdict = "NOT_ENOUGH_EVIDENCE"
                evidence = str(checked.get("evidence") or "")[:300]
                payload = {
                    "manual": True, "direction": "vision_to_text", "checked_at_epoch": time.time(),
                    "verdict": verdict, "evidence": evidence,
                    "note": "Manual text-consistency second opinion only. The selected text verifier did not see the image and cannot visually confirm the vision result.",
                    "provider": str(getattr(client, "provider", "pi5")),
                }
                summary = f"{provider_label}: {verdict}" + (f" · {evidence[:140]}" if evidence else "")
            await self._persist_manual_crosscheck(job, payload)
            state["status"] = "completed"
            state["verdict"] = verdict
            state["summary"] = summary
            state["result"] = payload
            state["completed_at_epoch"] = time.time()
            self._events.notify("stage2b_manual_crosscheck_completed")
        except asyncio.CancelledError:
            state["status"] = "cancelled"
            state["completed_at_epoch"] = time.time()
            raise
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
            state["summary"] = "Cross-check failed"
            state["completed_at_epoch"] = time.time()
            logger.exception("Manual cross-check failed for job %s", job_id)
            self._events.notify("stage2b_manual_crosscheck_failed")

    def correction_suggestion_state_for(self, postprocess_job_id: int) -> dict[str, Any] | None:
        state = self.correction_suggestion_state.get(int(postprocess_job_id))
        return dict(state) if state else None

    async def start_correction_suggestion_backfill(self, postprocess_job_id: int) -> dict[str, Any]:
        """Generate missing Pi5 correction suggestions for unresolved human review.

        This never reruns Stage 2A/Stage 2B verification and never contacts
        OnePlus. It reuses persisted Pi5 findings and asks only the Pi5 corrector
        for a conservative replacement candidate, one route at a time.
        """
        postprocess_job_id = int(postprocess_job_id)
        existing = self._correction_suggestion_tasks.get(postprocess_job_id)
        if existing and not existing.done():
            return {"accepted": False, "reason": "already_running", **(self.correction_suggestion_state_for(postprocess_job_id) or {})}

        config = self._config_getter()
        if not bool(config.stage2c_enabled) or not bool(config.stage2c_text_correction_enabled):
            raise ValueError("Stage 2C text correction is disabled")
        if not bool(getattr(config, "stage2c_correction_suggestions_enabled", True)):
            raise ValueError("Pi5 correction suggestions are disabled in config")
        post_job = await self._postprocess_store.get_job(postprocess_job_id)
        if not post_job or post_job.get("status") != "completed" or not post_job.get("result_dir"):
            raise ValueError("Stage 2A must be completed first")
        rows = await self._store.list_book_jobs_raw(postprocess_job_id)
        if any(row.get("status") in {"pending", "processing"} for row in rows):
            raise ValueError("Stage 2B is still running or pending for this book")

        result_dir = Path(config.processed_dir) / Path(str(post_job["result_dir"])).name
        ledger_path = result_dir / "correction_ledger.json"
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
        except (OSError, json.JSONDecodeError, TypeError):
            ledger = {}
        candidates: dict[str, dict[str, Any]] = {}
        for entry in ledger.get("entries") or []:
            if entry.get("entry_type") != "text_correction" or entry.get("status") == "superseded":
                continue
            if entry.get("human_verified"):
                continue
            if str(entry.get("verification_verdict") or "").upper() != "LIKELY_CORRUPT":
                continue
            original = " ".join(str(entry.get("original_text") or "").split())
            proposed = " ".join(str(entry.get("proposed_text") or "").split())
            # Missing, empty, or unchanged proposals are not useful to review.
            if proposed and proposed != original:
                continue
            candidates[str(entry.get("route_id") or "")] = entry

        work_rows = [
            row for row in rows
            if row.get("target") == "pi5" and row.get("status") == "completed"
            and str(row.get("route_id") or "") in candidates
        ]
        state = {
            "postprocess_job_id": postprocess_job_id,
            "status": "queued",
            "total": len(work_rows),
            "processed": 0,
            "suggestions_ready": 0,
            "unchanged_or_rejected": 0,
            "errors": 0,
            "current_route_id": None,
            "started_at_epoch": None,
            "completed_at_epoch": None,
        }
        self.correction_suggestion_state[postprocess_job_id] = state
        if not work_rows:
            state["status"] = "completed"
            state["completed_at_epoch"] = time.time()
            return {"accepted": False, "reason": "no_missing_suggestions", **state}

        task = asyncio.create_task(
            self._run_correction_suggestion_backfill(postprocess_job_id, work_rows),
            name=f"correction-suggestions-{postprocess_job_id}",
        )
        self._correction_suggestion_tasks[postprocess_job_id] = task
        self._events.notify("stage2c_correction_suggestions_started")
        return {"accepted": True, **state}

    async def _run_correction_suggestion_backfill(self, postprocess_job_id: int, rows: list[dict[str, Any]]) -> None:
        config = self._config_getter()
        state = self.correction_suggestion_state[postprocess_job_id]
        state["status"] = "running"
        state["started_at_epoch"] = time.time()
        pi5_model: str | None = None
        try:
            for row in rows:
                state["current_route_id"] = row.get("route_id")
                try:
                    request = json.loads(row.get("request_json") or "{}")
                    stored_result = json.loads(row.get("result_json") or "{}")
                    if not isinstance(request, dict) or not isinstance(stored_result, dict):
                        raise ValueError("Persisted Pi5 payload is not an object")
                    original = str(request.get("suspect_text") or "")
                    context = str(request.get("nearby_context") or "")
                    old_parsed = stored_result.get("parsed") or {}
                    if int(old_parsed.get("segment_count") or 1) > 1:
                        state["unchanged_or_rejected"] += 1
                        continue
                    model_like = {
                        "verdict": old_parsed.get("model_verdict") or old_parsed.get("verdict"),
                        "confidence": old_parsed.get("confidence"),
                        "reason_code": old_parsed.get("model_reason_code") or old_parsed.get("reason_code"),
                        "evidence": old_parsed.get("evidence"),
                    }
                    structure = analyze_text_structure(original)
                    parsed = _validate_pi5(model_like, original, structure)
                    client = self._client_for("pi5", row)
                    if pi5_model is None:
                        pi5_model = await self._model_for("pi5", config.pi5_url, client)
                    correction = await _attempt_pi5_correction(client, original, context, parsed, pi5_model, config)
                    proposed = str(correction.get("proposed_text") or "").strip()
                    if proposed and " ".join(proposed.split()) != " ".join(original.split()) and correction.get("status") in {"proposed", "applied"}:
                        state["suggestions_ready"] += 1
                    else:
                        state["unchanged_or_rejected"] += 1
                    backfill_result = {"parsed": parsed, "correction": correction}
                    await self._record_stage2c_entry(
                        "pi5", row, request, backfill_result, parsed.get("verdict") or "UNCERTAIN",
                        row.get("model") or pi5_model,
                    )
                except Exception as exc:
                    state["errors"] += 1
                    logger.warning("Correction suggestion failed for route %s: %s", row.get("route_id"), exc)
                finally:
                    state["processed"] += 1
                    self._events.notify("stage2c_correction_suggestion_progress")
            state["status"] = "completed" if not state["errors"] else "partial"
        except asyncio.CancelledError:
            state["status"] = "cancelled"
            raise
        finally:
            state["current_route_id"] = None
            state["completed_at_epoch"] = time.time()
            self._events.notify("stage2c_correction_suggestions_completed")

    def stage2c_state_for(self, postprocess_job_id: int) -> dict[str, Any] | None:
        state = self.stage2c_backfill_state.get(int(postprocess_job_id))
        return dict(state) if state else None

    async def revalidate_saved_pi5_results(self, postprocess_job_id: int) -> dict[str, Any]:
        """Re-apply current deterministic Pi5/source-transcription policy without model calls.

        Direct target-crop reconstructions are checked for wrong-region / high-risk
        alignment failures introduced by newer safety policy. Legacy uncertain JSON
        verifier results retain their historical parser revalidation path.
        """
        postprocess_job_id = int(postprocess_job_id)
        post_job = await self._postprocess_store.get_job(postprocess_job_id)
        if not post_job or post_job.get("status") != "completed" or not post_job.get("result_dir"):
            raise ValueError("Stage 2A must be completed before saved Pi5 results can be re-evaluated")
        rows = await self._store.list_book_jobs_raw(postprocess_job_id)
        result_dir = Path(self._config_getter().processed_dir) / Path(str(post_job["result_dir"])).name
        updates: dict[str, dict[str, Any]] = {}
        counts = {
            "checked": 0, "changed": 0, "likely_corrupt": 0, "likely_ok": 0,
            "uncertain": 0, "unrecoverable": 0,
            "direct_checked": 0, "direct_safe": 0, "direct_demoted": 0,
        }

        async def persist_row(row: dict[str, Any], request: dict[str, Any], result: dict[str, Any], new_verdict: str) -> None:
            await self._store.mark_completed(
                int(row["id"]), float(row.get("processing_seconds") or 0), row.get("model"),
                str(row.get("endpoint") or ""), new_verdict, request, result, str(row.get("artifact_path") or ""),
            )
            artifact_path = Path(str(row.get("artifact_path") or ""))
            if not artifact_path.is_file():
                fallback_artifact = result_dir / "verification" / f"stage2b_job_{int(row['id']):06d}.json"
                if fallback_artifact.is_file():
                    artifact_path = fallback_artifact
            if artifact_path.is_file():
                try:
                    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                    artifact["verdict"] = new_verdict
                    artifact["result"] = result
                    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
                except (OSError, json.JSONDecodeError, TypeError):
                    pass

        for row in rows:
            if row.get("target") != "pi5" or row.get("status") != "completed":
                continue
            try:
                request = json.loads(row.get("request_json") or "{}")
                result = json.loads(row.get("result_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                counts["unrecoverable"] += 1
                continue
            if not isinstance(request, dict) or not isinstance(result, dict):
                counts["unrecoverable"] += 1
                continue

            correction = result.get("correction") if isinstance(result.get("correction"), dict) else {}
            direct = bool(
                request.get("task") == "source_image_target_reconstruction"
                or correction.get("direct_source_transcription")
            )
            if direct:
                counts["checked"] += 1
                counts["direct_checked"] += 1
                original = str(request.get("suspect_text") or "")
                proposed = str(correction.get("proposed_text") or "")
                if str(correction.get("status") or "") == "applied" and proposed:
                    profile = source_transcription_safety_profile(original, proposed)
                    if not profile.get("accepted"):
                        previous_verdict = str((result.get("parsed") or {}).get("verdict") or row.get("verdict") or "LIKELY_CORRUPT")
                        reason = str((profile.get("reasons") or ["SOURCE_TRANSCRIPTION_SAFETY_REJECT"])[0])
                        parsed = dict(result.get("parsed") or {})
                        parsed.update({
                            "verdict": "UNCERTAIN",
                            "model_verdict": parsed.get("model_verdict") or previous_verdict,
                            "confidence": 0.0,
                            "reason_code": reason,
                            "evidence": "",
                            "evidence_valid": False,
                            "source_image_status": "UNSAFE_ALIGNMENT",
                            "safety_revalidation": profile,
                        })
                        correction = dict(correction)
                        correction.update({
                            "status": "pending",
                            "reason": f"{reason}_KEEP_ORIGINAL",
                            "proposed_text": None,
                            "safety_revalidation": profile,
                        })
                        result["parsed"] = parsed
                        result["correction"] = correction
                        result["policy_revalidation"] = {
                            "model_rerun": False,
                            "previous_verdict": previous_verdict,
                            "new_verdict": "UNCERTAIN",
                            "reason": reason,
                        }
                        await persist_row(row, request, result, "UNCERTAIN")
                        counts["changed"] += 1
                        counts["uncertain"] += 1
                        counts["direct_demoted"] += 1
                    else:
                        counts["direct_safe"] += 1
                else:
                    counts["direct_safe"] += 1
                continue

            if str(row.get("verdict") or "").upper() != "UNCERTAIN":
                continue
            counts["checked"] += 1
            raw = result.get("raw_response") or {}
            try:
                parsed_obj = _json_from_model_response(raw)
            except ValueError:
                parsed_obj = _recover_pi5_partial_json(raw)
            if parsed_obj is None:
                counts["unrecoverable"] += 1
                continue
            parsed = _validate_pi5(
                parsed_obj, str(request.get("suspect_text") or ""),
                request.get("structural_prefilter"), request.get("document_internal_candidate_evidence") or [],
            )
            new_verdict = str(parsed.get("verdict") or "UNCERTAIN")
            key = {"LIKELY_CORRUPT": "likely_corrupt", "LIKELY_OK": "likely_ok", "UNCERTAIN": "uncertain"}.get(new_verdict, "uncertain")
            counts[key] += 1
            if new_verdict != str(row.get("verdict") or ""):
                counts["changed"] += 1
            result["parsed"] = parsed
            result["policy_revalidation"] = {"model_rerun": False, "previous_verdict": row.get("verdict"), "new_verdict": new_verdict}
            await persist_row(row, request, result, new_verdict)
            entry_id = f"{row.get('generation')}:text:{row.get('route_id')}"
            updates[entry_id] = {"verdict": new_verdict, "verification": parsed}

        async with self._stage2c_ledger_lock:
            ledger_changed = await asyncio.to_thread(revalidate_unreviewed_text_entries, result_dir, updates)
            # Also migrate old automatically-applied direct transcriptions using
            # the independent Stage 2C safety boundary; human decisions are untouched.
            ledger_changed += await asyncio.to_thread(normalize_human_verified_ledger, result_dir)
        counts["ledger_entries_updated"] = ledger_changed
        self._events.notify("stage2b_pi5_saved_results_revalidated")
        return {"accepted": True, "postprocess_job_id": postprocess_job_id, **counts}

    async def start_stage2c_backfill(self, postprocess_job_id: int) -> dict[str, Any]:
        """Build the Stage 2C ledger from persisted Stage 2B results.

        This is intentionally a reconciliation/backfill, not a live verifier rerun.
        Current source-image reconstruction results are reused directly. Legacy
        pre-reconstruction results retain their conservative compatibility path.
        """
        postprocess_job_id = int(postprocess_job_id)
        existing = self._stage2c_backfill_tasks.get(postprocess_job_id)
        if existing and not existing.done():
            return {"accepted": False, "reason": "already_running", **(self.stage2c_state_for(postprocess_job_id) or {})}

        config = self._config_getter()
        if not bool(config.stage2c_enabled):
            raise ValueError("Stage 2C is disabled in config")
        post_job = await self._postprocess_store.get_job(postprocess_job_id)
        if not post_job or post_job.get("status") != "completed" or not post_job.get("result_dir"):
            raise ValueError("Stage 2A must be completed before Stage 2C can be built")
        verification_rows = await self._store.list_book_jobs_raw(postprocess_job_id)
        if not verification_rows:
            raise ValueError("No Stage 2B verification routes exist for this book")
        if any(row.get("status") in {"pending", "processing"} for row in verification_rows):
            raise ValueError("Stage 2B verification is still running or pending for this book")
        completed = [row for row in verification_rows if row.get("status") == "completed"]
        if not completed:
            raise ValueError("No completed Stage 2B verification results are available")
        result_dir = Path(config.processed_dir) / Path(str(post_job["result_dir"])).name
        review = human_review_summary(result_dir, require_human=bool(getattr(config, "stage2c_require_human_review", True)))
        if review.get("blocking_review_required", review.get("review_required", 0)):
            raise ValueError(
                f"Human review is still required for {review['blocking_review_required']} corrupted/uncertain text item(s). "
                "Review them before finalizing Stage 2C."
            )

        state = {
            "postprocess_job_id": postprocess_job_id,
            "status": "queued",
            "total_verification_routes": len(verification_rows),
            "completed_verification_routes": len(completed),
            "failed_verification_routes": sum(1 for row in verification_rows if row.get("status") == "failed"),
            "processed": 0,
            "reused": 0,
            "skipped_existing": 0,
            "corrections_attempted": 0,
            "corrections_applied": 0,
            "vision_entries": 0,
            "errors": 0,
            "current_route_id": None,
            "started_at_epoch": None,
            "completed_at_epoch": None,
        }
        self.stage2c_backfill_state[postprocess_job_id] = state
        task = asyncio.create_task(
            self._run_stage2c_backfill(postprocess_job_id, post_job, completed),
            name=f"stage2c-backfill-{postprocess_job_id}",
        )
        self._stage2c_backfill_tasks[postprocess_job_id] = task
        self._events.notify("stage2c_backfill_started")
        return {"accepted": True, **state}

    async def _run_stage2c_backfill(
        self, postprocess_job_id: int, post_job: dict[str, Any], rows: list[dict[str, Any]]
    ) -> None:
        config = self._config_getter()
        state = self.stage2c_backfill_state[postprocess_job_id]
        state["status"] = "running"
        state["started_at_epoch"] = time.time()
        result_dir = Path(config.processed_dir) / Path(str(post_job["result_dir"])).name
        status_path = result_dir / "stage2c_backfill.json"

        def persist_state() -> None:
            payload = {"schema": "docling-stage2c-backfill/v1", **state}
            tmp = status_path.with_suffix(status_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(status_path)

        # Idempotency: do not repeat Pi5 correction calls for routes already
        # represented by the current Stage 2C rule version.
        existing_ids: set[str] = set()
        # Normalize human-reviewed entries from older builds before deciding
        # which Stage 2C routes are already complete. This never changes the
        # human text/decision; it only removes stale automatic live reasons.
        await asyncio.to_thread(normalize_human_verified_ledger, result_dir)
        ledger_path = result_dir / "correction_ledger.json"
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
            for entry in ledger.get("entries") or []:
                if str(entry.get("rule_version") or "") == STAGE2C_RULE_VERSION:
                    existing_ids.add(str(entry.get("entry_id") or ""))
        except (OSError, json.JSONDecodeError, TypeError):
            existing_ids = set()

        pi5_client: OpenAICompatibleVerifier | None = None
        pi5_model: str | None = None
        try:
            await asyncio.to_thread(persist_state)
            for row in rows:
                state["current_route_id"] = row.get("route_id")
                target = str(row.get("target") or "")
                generation = str(row.get("generation") or "")
                entry_id = f"{generation}:{'text' if target == 'pi5' else 'vision'}:{row.get('route_id')}"
                if entry_id in existing_ids:
                    state["skipped_existing"] += 1
                    state["processed"] += 1
                    continue
                try:
                    request = json.loads(row.get("request_json") or "{}")
                    stored_result = json.loads(row.get("result_json") or "{}")
                    if not isinstance(request, dict) or not isinstance(stored_result, dict):
                        raise ValueError("Persisted verification payload is not an object")

                    if target == "pi5":
                        original = str(request.get("suspect_text") or "")
                        context = str(request.get("nearby_context") or "")
                        current_correction = stored_result.get("correction") or {}
                        if (
                            request.get("task") == "source_image_target_reconstruction"
                            or bool(current_correction.get("direct_source_transcription"))
                        ):
                            current_parsed = stored_result.get("parsed") or {}
                            current_verdict = str(current_parsed.get("verdict") or row.get("verdict") or "UNCERTAIN")
                            if current_correction.get("attempted"):
                                state["corrections_attempted"] += 1
                            if current_correction.get("status") == "applied":
                                state["corrections_applied"] += 1
                            await self._record_stage2c_entry(
                                "pi5", row, request, stored_result, current_verdict, row.get("model")
                            )
                            state["reused"] += 1
                            continue
                        old_parsed = stored_result.get("parsed") or {}
                        model_like = {
                            "verdict": old_parsed.get("model_verdict") or old_parsed.get("verdict"),
                            "confidence": old_parsed.get("confidence"),
                            "reason_code": old_parsed.get("model_reason_code") or old_parsed.get("reason_code"),
                            "evidence": old_parsed.get("evidence"),
                        }
                        structure = analyze_text_structure(original)
                        parsed = _validate_pi5(model_like, original, structure)
                        correction = stored_result.get("correction")
                        if not isinstance(correction, dict) or "eligibility" not in correction:
                            garble = ((parsed.get("structural_analysis") or {}).get("garble") or {})
                            eligible = (
                                bool(config.stage2c_enabled)
                                and bool(config.stage2c_text_correction_enabled)
                                and parsed.get("verdict") == "LIKELY_CORRUPT"
                                and parsed.get("reason_code") == "OCR_GARBLE"
                                and parsed.get("evidence_valid") is True
                                and float(parsed.get("confidence") or 0) >= float(config.stage2c_correction_min_confidence)
                                and float(garble.get("score") or 0) >= float(config.stage2c_correction_min_garble_score)
                            )
                            if eligible:
                                if pi5_client is None:
                                    pi5_client = self._client_for("pi5", row)
                                    pi5_model = await self._model_for("pi5", config.pi5_url, pi5_client)
                                pi5_client = self._client_for("pi5", row)
                                correction = await _attempt_pi5_correction(
                                    pi5_client, original, context, parsed, pi5_model, config
                                )
                            else:
                                correction = {
                                    "attempted": False,
                                    "status": "pending",
                                    "reason": "CORRECTION_GATE_NOT_MET",
                                    "eligibility": {
                                        "eligible": False,
                                        "verdict": parsed.get("verdict"),
                                        "reason_code": parsed.get("reason_code"),
                                        "evidence_valid": parsed.get("evidence_valid"),
                                        "confidence": parsed.get("confidence"),
                                        "garble_score": garble.get("score"),
                                        "thresholds": {
                                            "min_confidence": config.stage2c_correction_min_confidence,
                                            "min_garble_score": config.stage2c_correction_min_garble_score,
                                        },
                                    },
                                }
                        if correction.get("attempted"):
                            state["corrections_attempted"] += 1
                        if correction.get("status") == "applied":
                            state["corrections_applied"] += 1
                        backfill_result = {"parsed": parsed, "correction": correction}
                        await self._record_stage2c_entry(
                            "pi5", row, request, backfill_result, parsed.get("verdict") or "UNCERTAIN",
                            row.get("model") or pi5_model,
                        )
                    elif target == "oneplus":
                        old_parsed = stored_result.get("parsed") or {}
                        parsed = _validate_vision(old_parsed)
                        for key in ("structural_image_evidence", "deterministic_override", "model_merged_verdict", "crop_coverage", "crop_early_stop"):
                            if key in old_parsed:
                                parsed[key] = old_parsed[key]
                        # Recompute only cheap N150-side structural evidence if
                        # the old result predates it. No phone inference occurs.
                        if "structural_image_evidence" not in parsed:
                            try:
                                zip_path = Path(config.output_dir) / str(row.get("output_filename") or "")
                                doc = await self._document_for(zip_path)
                                source = json.loads(row.get("source_json") or "{}")
                                image_bytes, _mime, _member = await asyncio.to_thread(
                                    _read_picture, zip_path, doc, int(source.get("index")), source.get("artifact")
                                )
                                structure = await asyncio.to_thread(image_structure_evidence, image_bytes)
                                parsed = _apply_vision_structural_gate(parsed, structure)
                            except Exception:
                                logger.exception("Stage 2C backfill could not recompute image structure for route %s", row.get("route_id"))
                        await self._record_stage2c_entry(
                            "oneplus", row, request, {"parsed": parsed}, parsed.get("verdict") or "UNCERTAIN",
                            row.get("model"),
                        )
                        state["vision_entries"] += 1
                    else:
                        continue
                    state["reused"] += 1
                except Exception as exc:
                    state["errors"] += 1
                    logger.exception("Stage 2C backfill route %s failed: %s", row.get("route_id"), exc)
                finally:
                    state["processed"] += 1
                    await asyncio.to_thread(persist_state)

            state["current_route_id"] = None
            state["completed_at_epoch"] = time.time()
            state["status"] = "partial" if state["errors"] or state["failed_verification_routes"] else "completed"
            await asyncio.to_thread(persist_state)
            self._events.notify("stage2c_backfill_completed")
        except asyncio.CancelledError:
            state["status"] = "cancelled"
            state["completed_at_epoch"] = time.time()
            await asyncio.to_thread(persist_state)
            raise
        except Exception as exc:
            state["status"] = "failed"
            state["error_message"] = str(exc)[:1000]
            state["completed_at_epoch"] = time.time()
            try:
                await asyncio.to_thread(persist_state)
            finally:
                logger.exception("Stage 2C backfill failed for book %s", postprocess_job_id)
                self._events.notify("stage2c_backfill_failed")

    async def _discovery_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                created = await self.sync_routes_once()
                if created:
                    self._events.notify("stage2b_routes_discovered")
            except Exception:
                logger.exception("Stage 2B route discovery failed")
                self._events.notify("stage2b_discovery_error")
            await asyncio.sleep(self._config_getter().stage2b_poll_interval_seconds)

    async def sync_routes_once(self) -> int:
        """Discover changed Stage-2A route files exactly once per file version.

        This method may be invoked by the background discovery loop and by a
        manual start action at the same time. The lock coalesces those callers.
        A cheap stat signature prevents rereading/parsing unchanged routes and
        prevents no-op UPDATEs against every verification row. Read-only GET
        endpoints intentionally do not call this method.
        """
        async with self._route_sync_lock:
            created_total = 0
            processed_dir = Path(self._config_getter().processed_dir)
            # SQLite LIMIT -1 includes the full library; stat signatures below
            # still prevent reparsing and rewriting unchanged books.
            jobs = await self._postprocess_store.list_jobs(limit=-1)
            live_job_ids: set[int] = set()
            for job in jobs:
                if job.get("status") != "completed" or not job.get("result_dir"):
                    continue
                postprocess_job_id = int(job["id"])
                live_job_ids.add(postprocess_job_id)
                result_dir = processed_dir / Path(str(job["result_dir"])).name
                routes_path = result_dir / "routes.json"
                if not routes_path.is_file():
                    self._route_sync_signatures.pop(postprocess_job_id, None)
                    continue
                manifest_path = result_dir / "source_manifest.json"
                try:
                    route_stat = routes_path.stat()
                    manifest_stat = manifest_path.stat() if manifest_path.is_file() else None
                except OSError:
                    continue
                signature = (
                    str(result_dir),
                    route_stat.st_mtime_ns,
                    route_stat.st_size,
                    manifest_stat.st_mtime_ns if manifest_stat else 0,
                    manifest_stat.st_size if manifest_stat else 0,
                )
                if self._route_sync_signatures.get(postprocess_job_id) == signature:
                    continue

                payload_bytes = await asyncio.to_thread(routes_path.read_bytes)
                manifest_sha = ""
                if manifest_path.is_file():
                    try:
                        manifest_bytes = await asyncio.to_thread(manifest_path.read_bytes)
                        manifest_sha = str(json.loads(manifest_bytes).get("converted_zip_sha256") or "")
                    except (OSError, json.JSONDecodeError, TypeError):
                        manifest_sha = ""
                generation = hashlib.sha256(manifest_sha.encode("utf-8") + b"\0" + payload_bytes).hexdigest()
                try:
                    payload = json.loads(payload_bytes)
                except json.JSONDecodeError:
                    # Do not cache malformed content; a corrected write with
                    # the same coarse filesystem timestamp must be retried.
                    continue
                routes = [route for route in payload.get("routes") or [] if route.get("target") in {"pi5", "oneplus"}]
                created_total += await self._store.sync_routes(
                    postprocess_job_id,
                    int(job["conversion_job_id"]),
                    generation,
                    routes,
                    result_dir.name,
                    str(job.get("output_filename") or ""),
                )
                self._route_sync_signatures[postprocess_job_id] = signature

            # Bound the cache if old Stage-2 jobs disappear from the DB.
            for cached_id in set(self._route_sync_signatures) - live_job_ids:
                self._route_sync_signatures.pop(cached_id, None)
            return created_total

    async def _device_loop(self, target: str) -> None:
        while not self._stopping.is_set():
            try:
                if self._paused(target):
                    await asyncio.sleep(self._config_getter().stage2b_poll_interval_seconds)
                    continue
                config = self._config_getter()
                uses_groq = (
                    target == "pi5" and str(getattr(config, "text_verifier_provider", "pi5")) == "groq"
                ) or (
                    target == "oneplus" and str(getattr(config, "vision_verifier_provider", "oneplus")) == "groq"
                )
                if uses_groq and self._groq_quota is not None:
                    quota = await self._groq_quota.snapshot()
                    self.worker_state[target]["quota"] = quota
                    if quota.get("paused"):
                        await asyncio.sleep(config.stage2b_poll_interval_seconds)
                        continue
                job = await self._store.next_runnable(target, self._auto_run(target))
                if job is None:
                    await asyncio.sleep(self._config_getter().stage2b_poll_interval_seconds)
                    continue
                await self._run_job(target, job)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Stage 2B %s worker loop failed", target)
                self._events.notify(f"stage2b_{target}_worker_error")
                await asyncio.sleep(self._config_getter().stage2b_poll_interval_seconds)

    def _retry_delay(self, job: dict[str, Any]) -> int:
        config = self._config_getter()
        attempt = max(1, int(job.get("attempt_count") or 0) + 1)
        delay = int(config.stage2b_retry_delay_seconds * (2 ** min(attempt - 1, 5)))
        return min(delay, int(config.stage2b_retry_max_delay_seconds))

    async def _retry_or_fail(
        self,
        target: str,
        job: dict[str, Any],
        exc: Exception,
        seconds: float,
    ) -> None:
        """Schedule a bounded retry, or permanently fail after the cap."""
        config = self._config_getter()
        retries_used = int(job.get("retry_count") or 0)
        max_retries = int(config.stage2b_max_retries)
        if retries_used >= max_retries:
            artifact = await asyncio.to_thread(
                self._write_failure_artifact, job, exc, seconds, "failed_retry_limit", None
            )
            message = f"{exc} (retry limit exhausted: {max_retries} retries)"
            await self._store.mark_failed(
                int(job["id"]), type(exc).__name__, message, artifact
            )
            logger.error(
                "Stage 2B %s job %s route %s failed at %s after retry limit %s: %s: %s",
                target, job.get("id"), job.get("route_id"), job.get("_active_stage"),
                max_retries, type(exc).__name__, exc,
            )
            self._events.notify(f"stage2b_{target}_failed")
            return

        delay = self._retry_delay(job)
        artifact = await asyncio.to_thread(
            self._write_failure_artifact, job, exc, seconds, "pending_retry", delay
        )
        await self._store.mark_retryable(
            int(job["id"]), type(exc).__name__, str(exc), delay, artifact
        )
        logger.warning(
            "Stage 2B %s job %s route %s deferred at %s for %ss (retry %s/%s): %s: %s",
            target, job.get("id"), job.get("route_id"), job.get("_active_stage"),
            delay, retries_used + 1, max_retries, type(exc).__name__, exc,
        )
        self._events.notify(f"stage2b_{target}_waiting")

    async def _maybe_auto_finalize_book(self, postprocess_job_id: int) -> None:
        config = self._config_getter()
        if not bool(getattr(config, "stage2c_auto_finalize_after_stage2b", False)):
            return
        rows = await self._store.list_book_jobs_raw(int(postprocess_job_id))
        if not rows or any(row.get("status") in {"pending", "processing", "failed"} for row in rows):
            return
        running = self._stage2c_backfill_tasks.get(int(postprocess_job_id))
        if running and not running.done():
            return
        try:
            await self.start_stage2c_backfill(int(postprocess_job_id))
        except ValueError as exc:
            logger.info("Stage 2C auto-finalize skipped for book %s: %s", postprocess_job_id, exc)

    async def _run_job(self, target: str, job: dict[str, Any]) -> None:
        config = self._config_getter()
        run_mode = "auto" if self._auto_run(target) else "manual"
        await self._store.mark_processing(int(job["id"]), run_mode)
        job["run_mode"] = run_mode
        job["_active_stage"] = "starting"
        self.worker_state[target]["active_job_id"] = int(job["id"])
        self.worker_state[target]["active_stage"] = "starting"
        self._events.notify(f"stage2b_{target}_started")
        started = time.monotonic()
        try:
            route_timeout_value = (
                config.stage2b_pi5_job_timeout_seconds
                if target == "pi5" else config.stage2b_oneplus_job_timeout_seconds
            )
            # Pi5 timeout is enforced per inference request after acquiring the
            # shared device gate, so queue wait and later correction do not
            # discard completed triage. OnePlus can have no total-duration limit
            # (0 => None). Its streaming client still enforces first-output and
            # post-output idle timers, so a dead connection is not infinite.
            route_timeout = None if target == "pi5" or int(route_timeout_value) == 0 else route_timeout_value
            async with asyncio.timeout(route_timeout):
                if target == "pi5":
                    job["_active_stage"] = "pi5_text"
                    self.worker_state[target]["active_stage"] = "text check"
                    request, result, verdict, model, endpoint = await self._run_pi5(job)
                else:
                    request, result, verdict, model, endpoint = await self._run_oneplus(job)
            seconds = time.monotonic() - started
            artifact = await asyncio.to_thread(
                self._write_result_artifact, job, request, result, verdict, seconds, model, endpoint
            )
            await self._store.mark_completed(
                int(job["id"]), seconds, model, endpoint, verdict, request, result, artifact
            )
            try:
                await self._record_stage2c_entry(target, job, request, result, verdict, model)
            except Exception:
                # Verification is authoritative Stage 2B output. A ledger write
                # problem must never convert a completed verifier job into failure.
                logger.exception("Stage 2C ledger update failed for %s job %s", target, job.get("id"))
                self._events.notify("stage2c_ledger_error")
            try:
                self._checkpoint_path(job).unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove completed inference checkpoint")
            self.worker_state[target]["last_completed_job_id"] = int(job["id"])
            self._events.notify(f"stage2b_{target}_completed")
            await self._maybe_auto_finalize_book(int(job["postprocess_job_id"]))
        except CloudQuotaPausedError as exc:
            seconds = time.monotonic() - started
            snap = dict(getattr(exc, "snapshot", {}) or {})
            resume_at = snap.get("resume_at_epoch")
            delay = 60
            try:
                if resume_at is not None:
                    delay = max(5, min(86400, int(float(resume_at) - time.time())))
            except (TypeError, ValueError):
                delay = 60
            await self._store.mark_deferred(
                int(job["id"]), "CloudQuotaPaused", str(exc), delay_seconds=delay
            )
            self.worker_state[target]["quota"] = snap
            logger.warning(
                "Stage 2B Groq request paused before quota limit on job %s route %s: %s",
                job.get("id"), job.get("route_id"), exc,
            )
            self._events.notify("stage2b_cloud_quota_paused")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            retryable = status >= 500 or status in {408, 429}
            seconds = time.monotonic() - started
            if retryable:
                await self._retry_or_fail(target, job, exc, seconds)
            else:
                artifact = await asyncio.to_thread(
                    self._write_failure_artifact, job, exc, seconds, "failed", None
                )
                await self._store.mark_failed(
                    int(job["id"]), type(exc).__name__, str(exc), artifact
                )
                logger.error(
                    "Stage 2B %s job %s route %s failed at %s: HTTP %s %s",
                    target, job.get("id"), job.get("route_id"), job.get("_active_stage"), status, exc,
                )
                self._events.notify(f"stage2b_{target}_failed")
        except (httpx.TransportError, TimeoutError, ConnectionError) as exc:
            seconds = time.monotonic() - started
            await self._retry_or_fail(target, job, exc, seconds)
        except Exception as exc:
            seconds = time.monotonic() - started
            artifact = await asyncio.to_thread(
                self._write_failure_artifact, job, exc, seconds, "failed", None
            )
            await self._store.mark_failed(
                int(job["id"]), type(exc).__name__, str(exc), artifact
            )
            logger.error(
                "Stage 2B %s job %s route %s failed at %s: %s: %s",
                target, job.get("id"), job.get("route_id"), job.get("_active_stage"),
                type(exc).__name__, exc,
            )
            self._events.notify(f"stage2b_{target}_failed")
        finally:
            self.worker_state[target]["active_job_id"] = None
            self.worker_state[target]["active_stage"] = None
            if target == "oneplus":
                self.worker_state[target]["active_started_epoch"] = None

    async def _record_stage2c_entry(
        self,
        target: str,
        job: dict[str, Any],
        request: dict[str, Any],
        result: dict[str, Any],
        verdict: str,
        model: str | None,
    ) -> None:
        config = self._config_getter()
        if not bool(config.stage2c_enabled):
            return
        current = await self._postprocess_store.get_job(int(job["postprocess_job_id"]))
        if (not current or current.get("status") != "completed"
                or current.get("result_dir") != job.get("result_dir")):
            return
        result_dir = Path(config.processed_dir) / Path(str(job["result_dir"])).name
        manifest_path = result_dir / "source_manifest.json"
        source_zip_sha = ""
        if manifest_path.is_file():
            try:
                source_zip_sha = str(json.loads(manifest_path.read_text(encoding="utf-8")).get("converted_zip_sha256") or "")
            except (OSError, json.JSONDecodeError):
                source_zip_sha = ""
        source = json.loads(job.get("source_json") or "{}")
        generation = str(job.get("generation") or "")
        common = {
            "route_id": job.get("route_id"),
            "verification_job_id": int(job["id"]),
            "generation": generation,
            "page": source.get("page"),
            "source_index": source.get("index"),
            "source_type": source.get("type") or "text",
            "table_index": source.get("table_index"),
            "cell_index": source.get("cell_index"),
            "model": model,
            "rule_version": STAGE2C_RULE_VERSION,
            "created_at_epoch": time.time(),
            "verification_verdict": verdict,
        }
        if target == "pi5":
            parsed = result.get("parsed") or {}
            correction = result.get("correction") or {}
            # LIKELY_OK requires no patch. UNCERTAIN/corrupt candidates stay
            # visible in the ledger, even when the automatic correction gate
            # intentionally refuses to act.
            if parsed.get("verdict") == "LIKELY_OK" and not correction.get("attempted"):
                return
            original = str(request.get("suspect_text") or "")
            status = str(correction.get("status") or "pending")
            entry = {
                "entry_id": f"{generation}:text:{job.get('route_id')}",
                "entry_type": "text_correction",
                **common,
                "status": status,
                "status_reason": correction.get("reason") or "NO_AUTOMATIC_CORRECTION",
                "original_text": original,
                "original_source_sha256": source_sha256(original),
                "proposed_text": correction.get("proposed_text"),
                "verification": parsed,
                "eligibility": correction.get("eligibility"),
                "fidelity": correction.get("fidelity"),
                "scope_guard": correction.get("scope_guard"),
                "source_reconstruction": {
                    key: value for key, value in (result.get("source_reconstruction") or {}).items()
                    if key in {"status", "usable", "provider", "finish_reason", "truncated", "error_type", "error_message"}
                } or None,
                "correction_attempt_count": correction.get("correction_attempt_count"),
                "correction_parse_errors": correction.get("correction_parse_errors"),
                "reverification": correction.get("reverification"),
                "oneplus_crosscheck": correction.get("oneplus_crosscheck"),
                "raw_docling_immutable": True,
            }
        else:
            if not bool(config.stage2c_vision_enrichment_enabled):
                return
            parsed = result.get("parsed") or {}
            category = str(parsed.get("diagram_category") or "unknown")
            # Independent Stage 2C exclusion boundary.  Even if a persisted or
            # legacy Stage 2B result says decorative, deterministic line/diagram
            # evidence prevents automatic exclusion and sends the item to audit.
            status, reason = vision_enrichment_status(parsed)
            entry = {
                "entry_id": f"{generation}:vision:{job.get('route_id')}",
                "entry_type": "vision_enrichment",
                **common,
                "status": status,
                "status_reason": reason,
                "artifact": request.get("artifact"),
                "original_source_sha256": request.get("image_sha256") or source_sha256(str(request.get("artifact") or "")),
                "diagram_category": category,
                "visible_text": parsed.get("visible_text") or [],
                "visible_objects": parsed.get("visible_objects") or [],
                "legacy_visible_labels": parsed.get("legacy_visible_labels") or [],
                "legacy_schema": bool(parsed.get("legacy_schema")),
                "generated_summary": (
                    parsed.get("summary")
                    or ((parsed.get("full_image") or {}).get("summary") if isinstance(parsed.get("full_image"), dict) else "")
                    or ""
                ),
                "unresolved": parsed.get("unresolved", True),
                "crop_coverage": parsed.get("crop_coverage"),
                "structural_image_evidence": parsed.get("structural_image_evidence"),
                "deterministic_override": parsed.get("deterministic_override"),
                "provenance_note": (
                    "Legacy visible_labels were ambiguous and are retained only in legacy_visible_labels; they are not promoted to visible_text. "
                    if parsed.get("legacy_schema") else ""
                ) + "visible_text is model-read source text; visible_objects and generated_summary are model-generated interpretation, not extracted facts.",
                "raw_docling_immutable": True,
            }
        async with self._stage2c_ledger_lock:
            await asyncio.to_thread(upsert_ledger_entry, result_dir, source_zip_sha, entry)
        self._events.notify("stage2c_ledger_updated")

    async def _document_for(self, zip_path: Path) -> dict[str, Any]:
        stat = await asyncio.to_thread(zip_path.stat)
        signature = (stat.st_size, stat.st_mtime_ns)
        cached = self._doc_cache.get(str(zip_path))
        if cached and cached[0] == signature:
            return cached[1]
        doc = await asyncio.to_thread(_load_docling_document_fast, zip_path)
        self._doc_cache[str(zip_path)] = (signature, doc)
        return doc

    async def _model_for(self, target: str, endpoint: str, client: Any) -> str | None:
        cached = self._model_cache.get(target)
        if cached and cached[0] == endpoint:
            return cached[1]
        health = await client.health()
        model = health.model
        self._model_cache[target] = (endpoint, model)
        return model

    def _checkpoint_path(self, job: dict[str, Any]) -> Path:
        return (Path(self._config_getter().processed_dir) / Path(str(job["result_dir"])).name
                / "verification" / f"checkpoint_{int(job['id'])}.json")

    def _selected_provider(self, role_target: str) -> str:
        config = self._config_getter()
        if role_target == "pi5":
            return str(getattr(config, "text_verifier_provider", "pi5") or "pi5").lower()
        if role_target == "oneplus":
            return str(getattr(config, "vision_verifier_provider", "oneplus") or "oneplus").lower()
        raise ValueError("Unknown Stage 2B role target")

    def _endpoint_for_provider(self, provider: str) -> str:
        config = self._config_getter()
        if provider == "pi5":
            return str(config.pi5_url).rstrip("/")
        if provider == "oneplus":
            return str(config.oneplus_url).rstrip("/")
        if provider == "groq":
            return str(config.text_cloud_base_url).rstrip("/")
        raise ValueError(f"Unknown verifier provider: {provider}")

    def _usage_context_for(self, job: dict[str, Any], purpose: str) -> dict[str, Any]:
        return {
            "job_id": int(job.get("id") or 0) or None,
            "postprocess_job_id": int(job.get("postprocess_job_id") or 0) or None,
            "route_id": str(job.get("route_id") or "") or None,
            "book": str(job.get("output_filename") or job.get("result_dir") or "") or None,
            "purpose": purpose,
        }

    def _client_for(self, target: str, job: dict[str, Any]) -> CheckpointVerifier:
        """Compatibility text/vision client used by older audit/backfill paths.

        The selected provider can now be Pi5, OnePlus, or Groq for either role.
        For the text role Groq keeps the GPT-OSS structured text client so old
        saved-result utilities continue to work. New live text routes use
        ``_vision_client_for_role`` below and reconstruct directly from source
        page images instead of semantic text judgement.
        """
        config = self._config_getter()
        provider = self._selected_provider(target)
        endpoint = self._endpoint_for_provider(provider)
        usage_context = self._usage_context_for(job, "stage2b_compat")
        if target == "pi5" and provider == "groq":
            api_key = os.environ.get(str(config.text_cloud_api_key_env), "").strip()
            raw_client = GroqStructuredVerifier(
                endpoint,
                api_key,
                str(config.text_cloud_model),
                timeout_seconds=int(config.text_cloud_timeout_seconds),
                reasoning_effort=str(config.text_cloud_reasoning_effort),
                quota_guard=self._groq_quota,
                usage_context=usage_context,
            )
            timeout = int(config.text_cloud_timeout_seconds) + 15
            identity = f"groq-text-compat:{config.text_cloud_model}:{job.get('generation', '')}:strict-json-v2"
        elif target == "oneplus" and provider == "groq":
            api_key = os.environ.get(str(config.text_cloud_api_key_env), "").strip()
            raw_client = GroqVisionVerifier(
                endpoint,
                api_key,
                str(config.vision_cloud_model),
                timeout_seconds=int(config.vision_cloud_timeout_seconds),
                quota_guard=self._groq_quota,
                usage_context=usage_context,
            )
            timeout = int(config.vision_cloud_timeout_seconds) + 15
            identity = f"groq-vision-compat:{config.vision_cloud_model}:{job.get('generation', '')}:strict-json-v2"
        else:
            raw_client = OpenAICompatibleVerifier(endpoint, timeout_seconds=config.stage2b_request_timeout_seconds)
            timeout = None if provider == "oneplus" else config.stage2b_pi5_job_timeout_seconds
            identity = f"{provider}:{target}:compat:{endpoint}:{job.get('generation', '')}:v3"
        wrapped = CheckpointVerifier(
            raw_client,
            self._device_locks[provider],
            self._checkpoint_path(job),
            identity,
            timeout=timeout,
        )
        wrapped.endpoint = endpoint
        wrapped.provider = provider
        return wrapped

    def _vision_client_for_role(self, role_target: str, job: dict[str, Any]) -> CheckpointVerifier:
        """Return the explicitly selected vision-capable processor for a role.

        Both local devices are vision capable. Groq uses Qwen Vision here even
        for TEXT routes because the new correction method reads the original
        page image and reconstructs the target between Docling anchors.
        """
        config = self._config_getter()
        provider = self._selected_provider(role_target)
        endpoint = self._endpoint_for_provider(provider)
        usage_context = self._usage_context_for(
            job, "text_source_reconstruction" if role_target == "pi5" else "image_route_analysis"
        )
        if provider == "groq":
            api_key = os.environ.get(str(config.text_cloud_api_key_env), "").strip()
            raw_client = GroqVisionVerifier(
                endpoint,
                api_key,
                str(config.vision_cloud_model),
                timeout_seconds=int(config.vision_cloud_timeout_seconds),
                quota_guard=self._groq_quota,
                usage_context=usage_context,
            )
            timeout = int(config.vision_cloud_timeout_seconds) + 15
            identity = f"groq-vision:{role_target}:{config.vision_cloud_model}:{job.get('generation', '')}:source-reconstruct-v2"
        else:
            raw_client = OpenAICompatibleVerifier(endpoint, timeout_seconds=config.stage2b_request_timeout_seconds)
            # Local llama.cpp vision can spend a long time in prompt/image eval;
            # liveness is enforced by first-token + stream-idle timers instead.
            timeout = None
            identity = f"{provider}-vision:{role_target}:{endpoint}:{job.get('generation', '')}:source-reconstruct-v2"
        wrapped = CheckpointVerifier(
            raw_client,
            self._device_locks[provider],
            self._checkpoint_path(job),
            identity,
            timeout=timeout,
        )
        wrapped.endpoint = endpoint
        wrapped.provider = provider
        return wrapped

    async def _run_pi5(self, job: dict[str, Any]):
        """Process a TEXT route by reading the original page image.

        The historical database target is ``pi5`` but the actual processor is
        explicitly selectable: Pi5, OnePlus, or Groq.  The processor is not
        asked whether Docling is right or wrong. It performs a fill-the-middle
        transcription using preceding/following Docling blocks only as location
        anchors. A readable reconstruction is authoritative for the overlay;
        unreadable text preserves immutable Docling output and does not block
        Stage 2C when human review is optional.
        """
        config = self._config_getter()
        zip_path = Path(config.output_dir) / str(job["output_filename"])
        doc = await self._document_for(zip_path)
        source = json.loads(job["source_json"] or "{}")
        source_type = str(source.get("type") or "text")
        page_value = source.get("page")
        page = int(page_value) if page_value is not None else None
        if source_type == "table_cell":
            table_index = int(source.get("table_index"))
            cell_index = int(source.get("cell_index"))
            suspect, before_anchors, after_anchors = _table_cell_context_parts(doc, table_index, cell_index)
            index = None
        else:
            index = int(source.get("index"))
            suspect, before_anchors, after_anchors = _text_context_parts(doc, index, page)

        conversion = await self._postprocess_store.get_conversion_job(int(job["conversion_job_id"]))
        source_filename = str((conversion or {}).get("filename") or (conversion or {}).get("source_filename") or "")
        if source_type == "table_cell":
            target_image = await asyncio.to_thread(
                _render_source_table_cell, config, source_filename, page, doc, table_index, cell_index
            )
        else:
            target_image = await asyncio.to_thread(
                _render_source_target, config, source_filename, page, doc, index
            )

        client = self._vision_client_for_role("pi5", job)
        provider = str(getattr(client, "provider", self._selected_provider("pi5")) or self._selected_provider("pi5"))
        endpoint = str(getattr(client, "endpoint", self._endpoint_for_provider(provider)))
        model = await self._model_for(f"text:{provider}", endpoint, client)

        if not target_image:
            reconstructed = {
                "verdict": "UNREADABLE",
                "status": "UNREADABLE",
                "corrected_text": "",
                "usable": False,
                "direct_transcription": True,
                "provider": provider,
                "error_type": "SourceTargetCropUnavailable",
                "error_message": "Original source target crop is unavailable; immutable Docling text preserved.",
            }
            crop_meta = None
        else:
            image_bytes, image_mime, crop_meta = target_image
            progress = self._prepare_oneplus_stream_region("text target reconstruction", target="pi5")
            reconstructed = await _oneplus_text_crosscheck(
                client,
                image_bytes,
                image_mime,
                suspect,
                "",
                model,
                first_token_timeout_seconds=config.stage2b_oneplus_first_token_timeout_seconds,
                idle_timeout_seconds=config.stage2b_oneplus_stream_idle_timeout_seconds,
                before_anchors=before_anchors,
                after_anchors=after_anchors,
            )

        visible = str(reconstructed.get("corrected_text") or "").strip()
        readable = (
            reconstructed.get("status") == "READABLE"
            and bool(visible)
            and not bool(reconstructed.get("truncated"))
            and str(reconstructed.get("finish_reason") or "").lower() != "length"
        )
        same = " ".join(visible.split()) == " ".join(suspect.split()) if readable else False
        if readable and same:
            verdict = "LIKELY_OK"
            parsed = {
                "verdict": verdict,
                "model_verdict": verdict,
                "confidence": 1.0,
                "reason_code": "SOURCE_IMAGE_MATCH",
                "evidence": visible[:PI5_EVIDENCE_MAX_CHARS],
                "evidence_valid": True,
                "segment_count": 1,
                "source_image_status": "READABLE",
                "source_image_reconstruction": visible,
                "provider": provider,
                "method": "FILL_THE_MIDDLE_SOURCE_IMAGE",
            }
            correction = {
                "attempted": False,
                "status": "verified_original",
                "reason": "SOURCE_IMAGE_MATCHES_DOCLING",
                "proposed_text": None,
                "direct_source_transcription": True,
                "scope_guard": reconstructed.get("scope_guard"),
            }
        elif readable:
            verdict = "LIKELY_CORRUPT"
            parsed = {
                "verdict": verdict,
                "model_verdict": verdict,
                "confidence": 1.0,
                "reason_code": "SOURCE_IMAGE_RECONSTRUCTION",
                "evidence": visible[:PI5_EVIDENCE_MAX_CHARS],
                "evidence_valid": True,
                "segment_count": 1,
                "source_image_status": "READABLE",
                "source_image_reconstruction": visible,
                "provider": provider,
                "method": "FILL_THE_MIDDLE_SOURCE_IMAGE",
            }
            correction = {
                "attempted": True,
                "status": "applied",
                "reason": "SOURCE_IMAGE_TARGET_RECONSTRUCTION",
                "proposed_text": visible,
                "direct_source_transcription": True,
                "fidelity": {
                    "accepted": True,
                    "reasons": ["SOURCE_IMAGE_DIRECT_TRANSCRIPTION_NO_SEMANTIC_JUDGEMENT_GATE"],
                    "informational_only": True,
                },
                "scope_guard": reconstructed.get("scope_guard"),
            }
        else:
            verdict = "UNCERTAIN"
            parsed = {
                "verdict": verdict,
                "model_verdict": verdict,
                "confidence": 0.0,
                "reason_code": "SOURCE_IMAGE_UNREADABLE",
                "evidence": "",
                "evidence_valid": False,
                "segment_count": 1,
                "source_image_status": "UNREADABLE",
                "source_image_reconstruction": "",
                "provider": provider,
                "method": "FILL_THE_MIDDLE_SOURCE_IMAGE",
                "error_type": reconstructed.get("error_type"),
                "error_message": reconstructed.get("error_message"),
            }
            correction = {
                "attempted": False,
                "status": "pending",
                "reason": "SOURCE_IMAGE_UNREADABLE_KEEP_ORIGINAL",
                "proposed_text": None,
                "direct_source_transcription": True,
                "scope_guard": reconstructed.get("scope_guard"),
            }

        request = {
            "task": "source_image_target_reconstruction",
            "route_id": job.get("route_id"),
            "page": page,
            "text_index": index,
            "table_index": source.get("table_index"),
            "cell_index": source.get("cell_index"),
            "source_type": source_type,
            "suspect_text": suspect,
            "target_ocr_hint": suspect,
            "before_anchors": before_anchors,
            "after_anchors": after_anchors,
            "nearby_context": "\n".join(before_anchors + after_anchors),
            "instruction": "Read only the target between BEFORE/AFTER anchors from the original page image; do not judge the anchors.",
            "selected_processor": provider,
            "source_image_available": bool(target_image),
            "source_image_scope": "target_crop_only",
            "target_crop": crop_meta,
            "raw_docling_immutable": True,
        }
        if target_image:
            request["source_image_sha256"] = source_sha256(target_image[0])
        result = {
            "parsed": parsed,
            "correction": correction,
            "source_reconstruction": {
                key: value for key, value in reconstructed.items() if key != "raw_response"
            },
            "raw_response": reconstructed.get("raw_response"),
            "text_provider": provider,
            "cloud_fallback": None,
        }
        return request, result, verdict, model, endpoint

    def _prepare_oneplus_stream_region(self, region: str, target: str = "oneplus"):
        state = self.worker_state[target]
        state.update({
            "active_started_epoch": time.time(),
            "active_stage": f"{region} · waiting for first output",
            "stream_phase": "waiting_first_output",
            "stream_chunk_count": 0,
            "stream_content_chunk_count": 0,
            "stream_completion_tokens": None,
            "stream_first_content_seconds": None,
            "stream_last_activity_epoch": None,
            "stream_finish_reason": None,
            "stream_done_received": False,
        })

        def on_progress(progress: dict[str, Any]) -> None:
            phase = str(progress.get("phase") or "streaming")
            state["stream_phase"] = phase
            state["stream_chunk_count"] = int(progress.get("chunk_count") or 0)
            state["stream_content_chunk_count"] = int(progress.get("content_chunk_count") or 0)
            state["stream_completion_tokens"] = progress.get("completion_tokens")
            state["stream_first_content_seconds"] = progress.get("first_content_seconds")
            state["stream_last_activity_epoch"] = time.time()
            state["stream_finish_reason"] = progress.get("finish_reason")
            state["stream_done_received"] = bool(progress.get("done_received"))
            if phase == "complete":
                state["active_stage"] = f"{region} · response complete"
            elif progress.get("first_content_seconds") is not None:
                state["active_stage"] = f"{region} · streaming output"
            else:
                state["active_stage"] = f"{region} · waiting for first output"

        return on_progress

    async def _run_oneplus(self, job: dict[str, Any]):
        config = self._config_getter()
        zip_path = Path(config.output_dir) / str(job["output_filename"])
        doc = await self._document_for(zip_path)
        source = json.loads(job["source_json"] or "{}")
        picture_index = int(source.get("index"))
        image_bytes, mime, member = await asyncio.to_thread(
            _read_picture, zip_path, doc, picture_index, source.get("artifact")
        )
        client = self._vision_client_for_role("oneplus", job)
        endpoint = str(getattr(client, "endpoint", self._endpoint_for_provider(self._selected_provider("oneplus"))))
        model = await self._model_for(f"vision:{getattr(client, 'provider', 'oneplus')}", endpoint, client)
        full_prompt = _vision_prompt(job, "full image")
        job["_active_stage"] = "full_image"
        full_progress = self._prepare_oneplus_stream_region("full image")
        full, full_raw, full_attempts = await _inspect_vision_region(
            client,
            image_bytes,
            full_prompt,
            mime,
            model,
            config.stage2b_oneplus_max_tokens,
            first_token_timeout_seconds=config.stage2b_oneplus_first_token_timeout_seconds,
            stream_idle_timeout_seconds=config.stage2b_oneplus_stream_idle_timeout_seconds,
            on_progress=full_progress,
        )
        crop_results: list[dict[str, Any]] = []
        crop_audit: list[dict[str, Any]] = []
        crop_early_stop = False
        should_crop = _should_crop_vision(full, config.stage2b_vision_crops_enabled)
        if should_crop:
            crops = await asyncio.to_thread(
                _vision_crops,
                image_bytes,
                config.stage2b_vision_crop_overlap,
                config.stage2b_vision_crop_upscale,
                config.stage2b_vision_max_crops,
            )
            for label, crop_bytes, crop_mime in crops:
                prompt = _vision_prompt(job, label)
                job["_active_stage"] = f"crop_{label}"
                crop_progress = self._prepare_oneplus_stream_region(f"crop {label}")
                parsed, raw, attempts = await _inspect_vision_region(
                    client,
                    crop_bytes,
                    prompt,
                    crop_mime,
                    model,
                    config.stage2b_oneplus_max_tokens,
                    first_token_timeout_seconds=config.stage2b_oneplus_first_token_timeout_seconds,
                    stream_idle_timeout_seconds=config.stage2b_oneplus_stream_idle_timeout_seconds,
                    on_progress=crop_progress,
                )
                if parsed.get("parse_failed"):
                    # Keep the raw attempts for audit, but do not treat an
                    # unparseable crop as positive/negative visual evidence.
                    crop_audit.append({
                        "region": label,
                        "status": "parse_uncertain",
                        "error_type": "VisionParseError",
                        "error_message": parsed.get("parse_error") or "Model response could not be parsed",
                        "prompt": prompt,
                        "parsed": parsed,
                        "raw_response": raw,
                        "attempts": attempts,
                    })
                    continue
                crop_results.append(parsed)
                crop_audit.append({
                    "region": label,
                    "status": "completed",
                    "prompt": prompt,
                    "parsed": parsed,
                    "raw_response": raw,
                    "attempts": attempts,
                })
                if (str(full.get("unresolved_reason") or "").upper() == "CLASSIFICATION"
                        and parsed.get("verdict") == "TECHNICAL_USEFUL"
                        and float(parsed.get("confidence") or 0) >= 0.85
                        and not parsed.get("unresolved", True)
                        and not any(item.get("status") == "parse_uncertain" for item in crop_audit)):
                    crop_early_stop = True
                    break
        merged = _merge_vision(full, crop_results)
        merged["crop_early_stop"] = crop_early_stop
        merged["crop_coverage"] = "partial_triage_resolved" if crop_early_stop else "configured_regions"
        if crop_early_stop:
            merged["unresolved"] = False

        structural_image = await asyncio.to_thread(image_structure_evidence, image_bytes)
        merged = _apply_vision_structural_gate(merged, structural_image)
        failed_crops = [item for item in crop_audit if item.get("status") == "parse_uncertain"]
        if failed_crops and merged["verdict"] == "DECORATIVE_OR_LOW_VALUE":
            # Missing crop evidence makes a low-value conclusion weaker; do not
            # silently overstate certainty when some requested regions failed.
            merged["verdict"] = "UNCERTAIN"
            merged["confidence"] = min(float(merged.get("confidence") or 0), 0.5)
        merged["incomplete_crop_count"] = len(failed_crops)
        request = {
            "task": "visual_route_triage",
            "route_id": job["route_id"],
            "page": source.get("page"),
            "picture_index": picture_index,
            "artifact": member,
            "image_sha256": source_sha256(image_bytes),
            "reason": job.get("reason") or "",
            "full_image_prompt": full_prompt,
            "crop_policy": "full image first; overlapping crops only if unresolved",
            "vision_provider": str(getattr(client, "provider", "oneplus")),
            "streaming": {
                "enabled": str(getattr(client, "provider", "oneplus")) == "oneplus",
                "first_token_timeout_seconds": config.stage2b_oneplus_first_token_timeout_seconds,
                "idle_timeout_seconds": config.stage2b_oneplus_stream_idle_timeout_seconds,
                "job_timeout_seconds": config.stage2b_oneplus_job_timeout_seconds,
                "completion_rule": "finish_reason or [DONE]",
            },
            "crop_settings": {
                "enabled": config.stage2b_vision_crops_enabled,
                "overlap": config.stage2b_vision_crop_overlap,
                "upscale": config.stage2b_vision_crop_upscale,
                "max_crops": config.stage2b_vision_max_crops,
            },
        }
        result = {
            "parsed": merged,
            "full_image_raw_response": full_raw,
            "full_image_attempts": full_attempts,
            "crop_audit": crop_audit,
        }
        job["_active_stage"] = "complete"
        self.worker_state["oneplus"]["active_stage"] = "merging result"
        result["vision_provider"] = str(getattr(client, "provider", "oneplus"))
        return request, result, merged["verdict"], model, endpoint

    async def vision_audit_image(self, job_id: int, region: str = "full") -> tuple[bytes, str, str]:
        """Reconstruct the exact source image/crop used by a completed vision job.

        The original converted ZIP remains immutable. Crop generation uses the
        settings persisted in the job request when available so the audit view
        matches what the verifier saw rather than today's configuration.
        """
        job = await self._store.get_job(int(job_id))
        if not job or str(job.get("target") or "") != "oneplus":
            raise ValueError("Vision verification job not found")
        config = self._config_getter()
        zip_path = Path(config.output_dir) / str(job.get("output_filename") or "")
        doc = await self._document_for(zip_path)
        try:
            source = json.loads(job.get("source_json") or "{}")
        except json.JSONDecodeError:
            source = {}
        picture_index = int(source.get("index"))
        image_bytes, mime, member = await asyncio.to_thread(
            _read_picture, zip_path, doc, picture_index, source.get("artifact")
        )
        if region in {"", "full", "full-image", "full_image"}:
            return image_bytes, mime, member

        try:
            request = json.loads(job.get("request_json") or "{}")
        except json.JSONDecodeError:
            request = {}
        settings = request.get("crop_settings") if isinstance(request, dict) else {}
        settings = settings if isinstance(settings, dict) else {}
        overlap = float(settings.get("overlap", config.stage2b_vision_crop_overlap))
        upscale = float(settings.get("upscale", config.stage2b_vision_crop_upscale))
        max_crops = int(settings.get("max_crops", config.stage2b_vision_max_crops))
        crops = await asyncio.to_thread(_vision_crops, image_bytes, overlap, upscale, max_crops)
        wanted = str(region).strip().lower().replace("_", "-")
        for label, crop_bytes, crop_mime in crops:
            if label.lower() == wanted:
                return crop_bytes, crop_mime, f"{member}#{label}"
        raise ValueError(f"Vision crop '{region}' was not generated for this job")

    def _write_failure_artifact(
        self,
        job: dict[str, Any],
        exc: Exception,
        seconds: float,
        status: str,
        retry_delay_seconds: int | None,
    ) -> str:
        config = self._config_getter()
        result_dir = Path(config.processed_dir) / Path(str(job["result_dir"])).name / "verification"
        result_dir.mkdir(parents=True, exist_ok=True)
        attempt = max(1, int(job.get("attempt_count") or 0) + 1)
        path = result_dir / f"stage2b_job_{int(job['id']):06d}_attempt_{attempt:02d}_error.json"
        payload = {
            "schema": "docling-stage2b-verification-error/v1",
            "job_id": int(job["id"]),
            "postprocess_job_id": int(job["postprocess_job_id"]),
            "conversion_job_id": int(job["conversion_job_id"]),
            "route_id": job["route_id"],
            "target": job["target"],
            "code": job.get("code"),
            "priority": job.get("priority"),
            "run_mode": job.get("run_mode"),
            "attempt": attempt,
            "stage": job.get("_active_stage") or "unknown",
            "status": status,
            "processing_seconds": round(seconds, 3),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "retry_delay_seconds": retry_delay_seconds,
            "retry_count_before_failure": int(job.get("retry_count") or 0),
            "max_retries": int(getattr(config, "stage2b_max_retries", 2)),
            "response_status": exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None else None,
            "response_excerpt": (exc.response.text[:4000] if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None else None),
            "raw_attempts": getattr(exc, "attempts", None),
            "raw_docling_immutable": True,
            "correction_applied": False,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return str(path.relative_to(Path(config.processed_dir)))

    def _write_result_artifact(
        self,
        job: dict[str, Any],
        request: dict[str, Any],
        result: dict[str, Any],
        verdict: str,
        seconds: float,
        model: str | None,
        endpoint: str,
    ) -> str:
        config = self._config_getter()
        result_dir = Path(config.processed_dir) / Path(str(job["result_dir"])).name / "verification"
        result_dir.mkdir(parents=True, exist_ok=True)
        path = result_dir / f"stage2b_job_{int(job['id']):06d}.json"
        payload = {
            "schema": "docling-stage2b-verification/v1",
            "job_id": int(job["id"]),
            "postprocess_job_id": int(job["postprocess_job_id"]),
            "conversion_job_id": int(job["conversion_job_id"]),
            "route_id": job["route_id"],
            "target": job["target"],
            "code": job.get("code"),
            "priority": job.get("priority"),
            "run_mode": job.get("run_mode"),
            "request": request,
            "result": result,
            "verdict": verdict,
            "processing_seconds": round(seconds, 3),
            "model": model,
            "endpoint": endpoint,
            "raw_docling_immutable": True,
            "correction_applied": False,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return str(path.relative_to(Path(config.processed_dir)))
