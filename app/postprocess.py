from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
import zipfile
from urllib.parse import unquote, urlparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .config import AppConfig
from .events import EventBroker
from .postprocess_store import PostprocessStore
from .verifier_clients import OpenAICompatibleVerifier


TECH_VALUE_RE = re.compile(
    r"(?<![\w.])(?:[<>≤≥]\s*)?[+-]?\d+(?:[.,]\d+)?(?:\s*±\s*\d+(?:[.,]\d+)?)?\s*"
    r"(?:V(?:AC|DC)?|A|mA|kA|W|kW|MW|bar|mbar|Pa|kPa|MPa|Nm|kNm|°C|°F|Hz|kHz|rpm|%|mm|cm|m|µm|um)\b",
    re.I,
)
INEQUALITY_RE = re.compile(r"\b(?:less than|greater than|at least|at most|maximum|minimum)\b|[<>≤≥]", re.I)
REPLACEMENT_RE = re.compile("\ufffd")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
DIGIT_LOWER_L_RE = re.compile(r"(?<!\w)\d+[l]\d*(?!\w)|(?<!\w)\d*[l]\d+(?!\w)")
OCR_UNIT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:Nrn|rnA|rnV|rnW)\b", re.I)
LOWER_L_UNIT_RE = re.compile(r"\bl\s*(?:A|V|W)\b")

ALPHA_TOKEN_RE = re.compile(r"(?<![\w-])[^\W\d_]+(?:-[^\W\d_]+)*(?![\w-])", re.UNICODE)
COMMON_SHORT_WORDS = {
    # Common short function words across several Latin-script languages.
    # They are excluded only from the garble heuristic; source text is never changed.
    "a", "an", "and", "as", "at", "be", "by", "do", "for", "from",
    "if", "in", "is", "it", "no", "of", "on", "or", "so", "the", "to",
    "up", "we", "with", "i",
    "de", "du", "la", "le", "les", "en", "et", "un", "une", "au",
    "aux", "des", "ce", "se", "ne", "ou", "il",
    "im", "zu", "am", "an",
    "el", "y",
    "di", "da", "lo", "e",
}
CONTENTS_HEADING_RE = re.compile(
    r"^(?:table\s+of\s+contents|contents|inhaltsverzeichnis|sommaire|indice|índice)(?:\b|\s|$)",
    re.I,
)


def _generic_ocr_garble_reasons(value: str) -> list[str]:
    """Conservative language-light OCR fragmentation detector.

    Only token-fragment patterns are considered. Non-ASCII characters, accents,
    normal multi-space layout and ordinary short function words are ignored.
    """
    matches = list(ALPHA_TOKEN_RE.finditer(value or ""))
    if len(matches) < 8:
        return []

    tokens = [match.group(0).lower() for match in matches]

    def is_apostrophe_contraction(match) -> bool:
        before = (value or "")[match.start() - 1:match.start()] if match.start() else ""
        after = (value or "")[match.end():match.end() + 1]
        return before in {"'", "’"} or after in {"'", "’"}

    fragments: list[str] = []
    singles: list[str] = []
    for match, token in zip(matches, tokens):
        original = match.group(0)
        if token in COMMON_SHORT_WORDS or is_apostrophe_contraction(match):
            continue
        # Standalone uppercase letters are common engineering identifiers
        # (A/B lines, G/H/K lubrication points) and letter-spaced headings.
        if len(original) == 1 and original.isupper():
            continue
        if len(token) <= 2:
            fragments.append(token)
        if len(token) == 1:
            singles.append(token)

    reasons: list[str] = []
    if len(singles) >= 3 and len(singles) / len(tokens) >= 0.15:
        reasons.append("excessive_single_letter_fragments")
    elif singles and len(fragments) >= 3 and len(fragments) / len(tokens) >= 0.20:
        reasons.append("fragmented_alpha_tokens")

    counts = Counter(
        token for token in tokens
        if 2 <= len(token) <= 5 and token not in COMMON_SHORT_WORDS
    )
    repeated_three = any(count >= 3 for count in counts.values())
    repeated_pairs = sum(1 for count in counts.values() if count >= 2) >= 2
    if len(tokens) >= 12 and singles and (repeated_three or repeated_pairs):
        reasons.append("repeated_short_ocr_fragments")

    return reasons


TROUBLESHOOT_RE = re.compile(
    r"\b(?:"
    r"troubleshoot(?:ing|er|ers|ed|s)?"
    r"|fault(?:s)?(?:[\s-]+finding)?"
    r"|(?:possible|probable|root)[\s-]+cause(?:s)?"
    r"|corrective[\s-]+action(?:s)?"
    r"|remed(?:y|ies)"
    r"|symptom(?:s)?"
    r"|alarm(?:s)?"
    r")\b",
    re.I,
)
PROCEDURE_RE = re.compile(r"\b(?:procedure|instruction|start(?:ing)?|stop(?:ping)?|maintenance|inspection|adjustment)\b", re.I)
PARTS_RE = re.compile(r"\b(?:spare parts?|parts list|part no\.?|item no\.?|quantity|qty\.?|drawing no\.?)\b", re.I)
SPEC_RE = re.compile(r"\b(?:specification|technical data|rated|capacity|pressure|voltage|current|torque|temperature)\b", re.I)


CHECK_STATUS_DISPLAY = {
    "consistent": "Looks good",
    "anomaly": "Issue found",
    "limited": "Not enough checked to be sure",
    "not_evaluable": "Couldn't check this",
    "not_applicable": "Nothing to check",
}

COVERAGE_STATUS_DISPLAY = {
    "ok": "Good",
    "limited": "Needs more checking",
    "warning": "Needs attention",
}

INTEGRITY_STATUS_DISPLAY = {
    "ok": "All files present",
    "warning": "Some files missing",
    "not_checked_in_this_call": "Not checked",
}

AUX_STATUS_DISPLAY = {
    "scanned": "Checked",
    "inventoried": "Catalogued",
}

def display_label(status: str | None, *, kind: str = "check") -> str:
    value = status or "unknown"
    if kind == "coverage":
        return COVERAGE_STATUS_DISPLAY.get(value, value.replace("_", " ").title())
    if kind == "integrity":
        return INTEGRITY_STATUS_DISPLAY.get(value, value.replace("_", " ").title())
    if kind == "aux":
        return AUX_STATUS_DISPLAY.get(value, value.replace("_", " ").title())
    return CHECK_STATUS_DISPLAY.get(value, value.replace("_", " ").title())


HEADING_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+){1,5})(?=\s|\.|$)")
HEADING_ALPHA_NUMBER_RE = re.compile(r"^\s*([A-Z](?:\.\d+){1,5})(?=\s|\.|$)")
HEADING_ROMAN_RE = re.compile(r"^\s*([IVXLCDM]+)(?:\.|\s)(?=\s*[A-Z0-9])")
HEADING_CHAPTER_RE = re.compile(r"^\s*(?:chapter|section)\s+(?:\d+|[IVXLCDM]+)\b", re.I)
HEADING_APPENDIX_RE = re.compile(r"^\s*appendix\s+(?:[A-Z]|\d+|[IVXLCDM]+)\b", re.I)


def _semantic_heading_signature(text: str) -> tuple[str, int] | None:
    """Return a conservative (numbering-scheme, semantic-depth) signature.

    Different outline schemes are intentionally modeled separately so a book that
    uses, for example, Chapter headings plus decimal subsections does not force
    both onto one Docling-level mapping. Bare single-number headings are omitted
    because they are frequently numbered list items mislabelled as headings.
    """
    raw = (text or "").strip()
    value = re.sub(r"\s+", " ", raw)
    match = HEADING_NUMBER_RE.match(value)
    if match:
        return "decimal", len(match.group(1).split("."))
    match = HEADING_ALPHA_NUMBER_RE.match(value)
    if match:
        return "alpha_decimal", len(match.group(1).split("."))
    if HEADING_CHAPTER_RE.match(value):
        return "chapter_section", 1
    if HEADING_APPENDIX_RE.match(value):
        return "appendix", 1
    # Roman recognition is uppercase-only to avoid treating prose/list prefixes
    # such as "c." and "d." as Roman section numbers.
    if HEADING_ROMAN_RE.match(raw):
        return "roman", 1
    return None


def _semantic_heading_depth(text: str) -> int | None:
    signature = _semantic_heading_signature(text)
    return signature[1] if signature else None

def _heading_hierarchy_check(
    doc: dict[str, Any],
    min_group: int = 4,
    min_coverage: float = 0.10,
) -> dict[str, Any]:
    texts = doc.get("texts") or []
    observed: list[dict[str, Any]] = []
    by_depth: dict[tuple[str, int], list[int]] = defaultdict(list)
    high_level_count = 0

    for index, item in enumerate(texts):
        if item.get("label") != "section_header":
            continue
        level = item.get("level")
        if not isinstance(level, int):
            continue
        if level > 5:
            high_level_count += 1
        text = (item.get("text") or "").strip()
        signature = _semantic_heading_signature(text)
        if signature is None:
            continue
        scheme, depth = signature
        row = {
            "text_index": index,
            "page": _page_of(item),
            "text": text[:240],
            "semantic_scheme": scheme,
            "semantic_depth": depth,
            "docling_level": level,
        }
        observed.append(row)
        by_depth[(scheme, depth)].append(level)

    expected: dict[tuple[str, int], int] = {}
    support: dict[tuple[str, int], int] = {}
    for key, levels in by_depth.items():
        if len(levels) < min_group:
            continue
        mode_level, mode_count = Counter(levels).most_common(1)[0]
        expected[key] = mode_level
        support[key] = mode_count

    anomalies: list[dict[str, Any]] = []
    for row in observed:
        exp = expected.get((row["semantic_scheme"], row["semantic_depth"]))
        if exp is None:
            continue
        # A one-level deviation can be legitimate in front matter or a special
        # subsection. Require a >=2-level mismatch before flagging.
        if abs(row["docling_level"] - exp) >= 2:
            item = dict(row)
            item["expected_level_for_numbering_depth"] = exp
            item["reason"] = "numbered_heading_level_deviates_from_document_pattern"
            anomalies.append(item)

    mapping = [
        {
            "semantic_scheme": scheme,
            "semantic_depth": depth,
            "expected_docling_level": expected[(scheme, depth)],
            "observations": len(by_depth[(scheme, depth)]),
            "mode_support": support[(scheme, depth)],
        }
        for scheme, depth in sorted(expected)
    ]

    non_monotonic: list[dict[str, Any]] = []
    schemes = sorted({scheme for scheme, _ in expected})
    for scheme in schemes:
        ordered = sorted((depth, expected[(scheme, depth)]) for s, depth in expected if s == scheme)
        for (depth_a, level_a), (depth_b, level_b) in zip(ordered, ordered[1:]):
            if level_b < level_a:
                non_monotonic.append({
                    "semantic_scheme": scheme,
                    "shallower_depth": depth_a,
                    "shallower_level": level_a,
                    "deeper_depth": depth_b,
                    "deeper_level": level_b,
                    "reason": "deeper_numbering_maps_to_shallower_docling_level",
                })

    total_section_headings = sum(1 for item in texts if item.get("label") == "section_header")
    evaluable_headings = sum(1 for row in observed if (row["semantic_scheme"], row["semantic_depth"]) in expected)
    numbered_ratio = (len(observed) / total_section_headings) if total_section_headings else None
    evaluable_ratio = (evaluable_headings / total_section_headings) if total_section_headings else None
    anomaly_count = len(anomalies) + len(non_monotonic)
    if anomaly_count:
        status = "anomaly"
    elif total_section_headings == 0:
        status = "not_applicable"
    elif evaluable_headings == 0:
        status = "not_evaluable"
    elif (evaluable_ratio or 0.0) < min_coverage:
        status = "limited"
    else:
        status = "consistent"

    return {
        "status": status,
        "display_label": display_label(status),
        "total_section_headings": total_section_headings,
        "numbered_headings_checked": len(observed),
        "evaluable_headings": evaluable_headings,
        "numbered_heading_coverage_ratio": round(numbered_ratio, 4) if numbered_ratio is not None else None,
        "validation_coverage_ratio": round(evaluable_ratio, 4) if evaluable_ratio is not None else None,
        "minimum_coverage_for_consistent_status": min_coverage,
        "supported_numbering": ["1.2", "1.2.3", "A.1", "A.1.2", "uppercase Roman top-level", "Chapter/Section", "Appendix"],
        "headings_above_level_5": high_level_count,
        "level_mapping": mapping,
        "anomaly_count": anomaly_count,
        "anomalies": anomalies,
        "mapping_anomalies": non_monotonic,
        "note": "Levels >5 are recorded but are not errors by themselves. Zero anomalies with low coverage is inconclusive, not clean.",
    }


def _resolve_ref(doc: dict[str, Any], ref: str) -> tuple[str, int, dict[str, Any]] | None:
    match = re.fullmatch(r"#/([^/]+)/(\d+)", ref or "")
    if not match:
        return None
    collection, raw_index = match.groups()
    values = doc.get(collection) or []
    index = int(raw_index)
    if not isinstance(values, list) or not 0 <= index < len(values):
        return None
    value = values[index]
    return (collection, index, value) if isinstance(value, dict) else None


def _flatten_body_items(doc: dict[str, Any]) -> list[tuple[str, int, dict[str, Any]]]:
    """Flatten body/group references while preserving Docling reading order."""
    result: list[tuple[str, int, dict[str, Any]]] = []
    seen_groups: set[int] = set()

    def walk(ref: str) -> None:
        resolved = _resolve_ref(doc, ref)
        if not resolved:
            return
        collection, index, item = resolved
        if collection == "groups":
            if index in seen_groups:
                return
            seen_groups.add(index)
            for child in item.get("children") or []:
                if isinstance(child, dict):
                    walk(child.get("$ref", ""))
            return
        result.append((collection, index, item))

    for child in ((doc.get("body") or {}).get("children") or []):
        if isinstance(child, dict):
            walk(child.get("$ref", ""))
    return result


def _bbox_tuple(item: dict[str, Any]) -> tuple[int, float, float, float, float] | None:
    prov = item.get("prov") or []
    if not prov or not isinstance(prov[0], dict):
        return None
    raw = prov[0]
    bbox = raw.get("bbox") or {}
    try:
        return (
            int(raw["page_no"]), float(bbox["l"]), float(bbox["t"]),
            float(bbox["r"]), float(bbox["b"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _bbox_contains(outer: dict[str, Any], inner: dict[str, Any], margin: float = 3.0) -> bool:
    return (
        inner["l"] >= outer["l"] - margin
        and inner["r"] <= outer["r"] + margin
        and inner["b"] >= outer["b"] - margin
        and inner["t"] <= outer["t"] + margin
    )


def _inversion_ratio(sequence: list[tuple[str, int]], expected: list[tuple[str, int]]) -> tuple[float, int]:
    """Return normalized inversion count using O(n log n) merge counting."""
    rank = {value: i for i, value in enumerate(expected)}
    values = [rank[value] for value in sequence if value in rank]
    n = len(values)
    if n < 2:
        return 0.0, 0

    def count(values_: list[int]) -> tuple[list[int], int]:
        if len(values_) <= 1:
            return values_, 0
        mid = len(values_) // 2
        left, left_inv = count(values_[:mid])
        right, right_inv = count(values_[mid:])
        merged: list[int] = []
        i = j = 0
        inv = left_inv + right_inv
        while i < len(left) and j < len(right):
            if left[i] <= right[j]:
                merged.append(left[i]); i += 1
            else:
                merged.append(right[j]); j += 1
                inv += len(left) - i
        merged.extend(left[i:]); merged.extend(right[j:])
        return merged, inv

    _, inversions = count(values)
    pairs = n * (n - 1) // 2
    return inversions / pairs if pairs else 0.0, inversions


def _reading_order_check(
    doc: dict[str, Any],
    threshold: float = 0.18,
    min_items: int = 5,
    min_coverage: float = 0.10,
) -> dict[str, Any]:
    """Conservative layout-aware reading-order validation.

    It evaluates Docling body order against plausible geometric orders, while
    ignoring furniture, page numbers, tiny table/image overlay glyphs, and
    floating pictures. On pages that look two-column, both row-major and
    column-major interpretations are considered and the better fit wins.
    """
    pages_raw = doc.get("pages") or {}
    page_sizes: dict[int, tuple[float, float]] = {}
    fallback_page_sizes: set[int] = set()
    for key, page in pages_raw.items():
        try:
            number = int(page.get("page_no", key))
            size = page.get("size") or {}
            if size.get("width") is None or size.get("height") is None:
                fallback_page_sizes.add(number)
            page_sizes[number] = (float(size.get("width", 595.0)), float(size.get("height", 842.0)))
        except (TypeError, ValueError):
            continue

    # A lower repetition threshold than the semantic-furniture rule is useful
    # here because manufacturer title blocks/revision strings may repeat across
    # a section rather than 30% of the entire book. These strings are ignored
    # only for reading-order validation; they remain preserved in source data.
    running_counts = Counter()
    for text_item in doc.get("texts") or []:
        value = re.sub(r"\s+", " ", (text_item.get("text") or "").strip())
        if 2 <= len(value) <= 120:
            running_counts[value] += 1
    running_threshold = max(5, math.ceil(max(1, len(pages_raw)) * 0.05))
    running_text = {value for value, count in running_counts.items() if count >= running_threshold}

    technical_visual_classes = {
        "engineering_drawing", "flow_chart", "line_chart", "bar_chart",
        "box_plot", "geographical_map", "screenshot_from_manual",
    }
    technical_visuals_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for pic in doc.get("pictures") or []:
        cls, conf = _top_picture_prediction(pic)
        box = _bbox_tuple(pic)
        if cls in technical_visual_classes and box:
            page, left, top, right, bottom = box
            technical_visuals_by_page[page].append({
                "class": cls, "confidence": conf,
                "l": left, "t": top, "r": right, "b": bottom,
            })

    per_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for order, (collection, index, item) in enumerate(_flatten_body_items(doc)):
        if collection not in {"texts", "tables", "pictures"}:
            continue
        box = _bbox_tuple(item)
        if not box:
            continue
        page, left, top, right, bottom = box
        per_page[page].append({
            "id": (collection, index),
            "order": order,
            "collection": collection,
            "index": index,
            "label": item.get("label"),
            "text": (item.get("text") or "").strip(),
            "l": left, "t": top, "r": right, "b": bottom,
        })

    anomalies: list[dict[str, Any]] = []
    checked_pages = 0
    two_column_pages = 0
    eligible_pages = 0
    skip_reasons: Counter = Counter()

    for page, items in sorted(per_page.items()):
        width, height = page_sizes.get(page, (595.0, 842.0))
        containers = [x for x in items if x["collection"] in {"tables", "pictures"}]

        # Manufacturer title blocks often place Date/Page/Project fields across
        # the very top in an order unrelated to prose flow. Detect that pattern
        # geometrically rather than by manufacturer-specific words.
        top_metadata = [
            x for x in items
            if x["collection"] == "texts"
            and x["t"] >= 0.84 * height
            and len(x["text"]) <= 40
            and (x["r"] - x["l"]) <= 0.30 * width
        ]
        top_metadata_ids = {x["id"] for x in top_metadata} if len(top_metadata) >= 3 else set()

        comparable: list[dict[str, Any]] = []

        for item in items:
            if item["collection"] == "pictures":
                # Floating images are intentionally not forced into a text order.
                continue
            if item["id"] in top_metadata_ids:
                continue
            if item["collection"] == "texts":
                if item["label"] in {"page_header", "page_footer"}:
                    continue
                text = item["text"]
                normalized_text = re.sub(r"\s+", " ", text)
                if normalized_text in running_text:
                    continue
                item_width = max(0.0, item["r"] - item["l"])
                item_height = max(0.0, item["t"] - item["b"])
                if not text or item_width < 2.0 or item_height < 2.0:
                    continue
                if len(text) == 1:
                    # Common table-symbol/letter overlays.
                    continue
                if re.fullmatch(r"\d{1,4}", text) and item["b"] < 0.12 * height:
                    # Standalone printed page number not labelled as footer.
                    continue
                if re.fullmatch(r"\d+\s*[-–]\s*\d+", text) and item_width < 0.12 * width:
                    # Compact table/grid references such as 6-23.
                    continue
                if item["label"] not in {"section_header", "caption"}:
                    if any(_bbox_contains(container, item) for container in containers):
                        # Duplicate OCR text embedded inside a table/picture should
                        # not drive page-level reading-order diagnostics.
                        continue
            comparable.append(item)

        if len(comparable) < min_items:
            skip_reasons["insufficient_comparable_items"] += 1
            continue

        # Contents/index pages are navigation structures, not prose. Their
        # visual ordering may be table-like or form-like and should not be
        # interpreted as a paragraph-order defect.
        comparable_text_values = [
            re.sub(r"\s+", " ", x["text"]).strip()
            for x in comparable if x["collection"] == "texts" and x["text"]
        ]
        if any(CONTENTS_HEADING_RE.match(value) for value in comparable_text_values):
            skip_reasons["contents_or_index_page"] += 1
            continue

        # Generic form/title pages: many short field-like strings, several
        # colon-terminated labels, and essentially no prose. This is geometric
        # and structural rather than manufacturer-specific.
        form_candidates = [x for x in comparable if x["collection"] == "texts"]
        if len(form_candidates) >= 6:
            short_count = sum(1 for x in form_candidates if len(x["text"].strip()) <= 45)
            long_prose_count = sum(1 for x in form_candidates if len(x["text"].strip()) >= 120)
            field_label_count = sum(
                1 for x in form_candidates
                if x["text"].strip().endswith(":") and len(x["text"].strip()) <= 45
            )
            if (
                short_count / len(form_candidates) >= 0.80
                and long_prose_count == 0
                and field_label_count >= 2
            ):
                skip_reasons["form_or_title_page"] += 1
                continue

        # A substantial technical drawing with many nearby labels is not a
        # prose reading-order problem. Those labels are handled by the visual
        # inventory/router instead.
        visual_regions = technical_visuals_by_page.get(page, [])
        has_large_technical_visual = any(
            ((x["r"] - x["l"]) * (x["t"] - x["b"])) >= 0.18 * width * height
            for x in visual_regions
        )
        if has_large_technical_visual and len(form_candidates) >= 10:
            short_label_ratio = sum(
                1 for x in form_candidates if len(x["text"].strip()) <= 45
            ) / len(form_candidates)
            long_prose_count = sum(1 for x in form_candidates if len(x["text"].strip()) >= 120)
            if short_label_ratio >= 0.45 and long_prose_count <= 3:
                skip_reasons["technical_visual_label_page"] += 1
                continue

        eligible_pages += 1

        # Dense schematic/grid pages do not have a meaningful prose reading
        # order. Their labels are handled by visual routing instead. This avoids
        # treating electrical/hydraulic drawing coordinates as paragraph order.
        text_like = [x for x in comparable if x["collection"] == "texts"]
        if text_like:
            lengths = sorted(len(x["text"]) for x in text_like)
            widths = sorted((x["r"] - x["l"]) / max(width, 1.0) for x in text_like)
            median_length = lengths[len(lengths) // 2]
            median_width = widths[len(widths) // 2]
        else:
            median_length = 0
            median_width = 1.0
        landscape_grid = width > height * 1.10 and len(text_like) >= 20
        dense_label_grid = len(text_like) >= 40 and median_length <= 18 and median_width <= 0.18
        if landscape_grid or dense_label_grid:
            skip_reasons["schematic_or_dense_label_grid"] += 1
            eligible_pages -= 1
            continue

        # A single near-full-page table plus only a handful of title-block
        # fields is also not a prose ordering problem.
        large_table = any(
            x["collection"] == "tables"
            and ((x["r"] - x["l"]) * (x["t"] - x["b"])) >= 0.55 * width * height
            for x in comparable
        )
        if large_table and len(comparable) <= 6:
            skip_reasons["large_table_non_prose_page"] += 1
            eligible_pages -= 1
            continue

        checked_pages += 1

        body_order = [x["id"] for x in sorted(comparable, key=lambda x: x["order"])]
        row_major = [x["id"] for x in sorted(comparable, key=lambda x: (-x["t"], x["l"]))]
        row_ratio, row_inv = _inversion_ratio(body_order, row_major)

        narrow = [x for x in comparable if (x["r"] - x["l"]) < 0.62 * width]
        left = [x for x in narrow if (x["l"] + x["r"]) / 2 < 0.47 * width]
        right = [x for x in narrow if (x["l"] + x["r"]) / 2 > 0.53 * width]
        is_two_column = len(left) >= 2 and len(right) >= 2

        column_ratio = None
        column_inv = None
        score = row_ratio
        chosen = "row_major"

        if is_two_column:
            two_column_pages += 1
            narrow_body = [x["id"] for x in sorted(narrow, key=lambda x: x["order"])]
            column_major = [
                x["id"] for x in sorted(
                    narrow,
                    key=lambda x: (0 if (x["l"] + x["r"]) / 2 < 0.5 * width else 1, -x["t"], x["l"]),
                )
            ]
            column_ratio, column_inv = _inversion_ratio(narrow_body, column_major)
            if column_ratio < score:
                score = column_ratio
                chosen = "column_major"

        # Require several pairwise disagreements as well as the normalized
        # threshold, avoiding flags from a single harmless callout.
        chosen_inv = column_inv if chosen == "column_major" else row_inv
        if score > threshold and (chosen_inv or 0) >= 3:
            body_sample = []
            for item in sorted(comparable, key=lambda x: x["order"])[:10]:
                body_sample.append({
                    "type": item["collection"].rstrip("s"),
                    "index": item["index"],
                    "label": item["label"],
                    "text": item["text"][:100],
                    "bbox": [round(item["l"], 1), round(item["t"], 1), round(item["r"], 1), round(item["b"], 1)],
                })
            anomalies.append({
                "page": page,
                "comparable_items": len(comparable),
                "layout_model": chosen,
                "two_column_candidate": is_two_column,
                "row_major_inversion_ratio": round(row_ratio, 4),
                "column_major_inversion_ratio": round(column_ratio, 4) if column_ratio is not None else None,
                "score": round(score, 4),
                "inversions": int(chosen_inv or 0),
                "body_order_sample": body_sample,
                "reason": "docling_body_order_differs_materially_from_plausible_page_geometry",
            })

    total_pages = len(pages_raw)
    total_coverage = (checked_pages / total_pages) if total_pages else None
    eligible_coverage = (checked_pages / eligible_pages) if eligible_pages else None
    pages_without_body_geometry = max(0, total_pages - len(per_page))
    if pages_without_body_geometry:
        skip_reasons["no_body_geometry"] += pages_without_body_geometry
    if anomalies:
        status = "anomaly"
    elif total_pages == 0:
        status = "not_applicable"
    elif checked_pages == 0:
        status = "not_evaluable"
    elif (total_coverage or 0.0) < min_coverage:
        status = "limited"
    else:
        status = "consistent"

    return {
        "status": status,
        "display_label": display_label(status),
        "total_pages": total_pages,
        "pages_with_body_geometry": len(per_page),
        "eligible_pages": eligible_pages,
        "pages_checked": checked_pages,
        "coverage_ratio_total_pages": round(total_coverage, 4) if total_coverage is not None else None,
        "coverage_ratio_eligible_pages": round(eligible_coverage, 4) if eligible_coverage is not None else None,
        "minimum_coverage_for_consistent_status": min_coverage,
        "pages_using_a4_fallback_size": len(fallback_page_sizes),
        "a4_fallback_page_samples": sorted(fallback_page_sizes)[:30],
        "skipped_pages_by_reason": dict(skip_reasons),
        "two_column_pages_considered": two_column_pages,
        "threshold": threshold,
        "minimum_items": min_items,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "note": "Pictures/furniture, title-block metadata, schematic/grid labels and table/image overlay OCR are excluded; two-column pages accept the better of row-major or column-major order. Coverage is reported explicitly.",
    }


def _page_of(item: dict[str, Any]) -> int | None:
    prov = item.get("prov") or []
    if prov and isinstance(prov[0], dict):
        page = prov[0].get("page_no")
        return int(page) if isinstance(page, (int, float)) else None
    return None


def _top_picture_prediction(pic: dict[str, Any]) -> tuple[str | None, float | None]:
    preds = (((pic.get("meta") or {}).get("classification") or {}).get("predictions") or [])
    if not preds:
        return None, None
    top = preds[0]
    cls = top.get("class_name")
    conf = top.get("confidence")
    try:
        conf = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf = None
    return cls, conf


def _text_of_ref(doc: dict[str, Any], ref: str) -> str:
    match = re.fullmatch(r"#/texts/(\d+)", ref or "")
    if not match:
        return ""
    idx = int(match.group(1))
    texts = doc.get("texts") or []
    if 0 <= idx < len(texts):
        return (texts[idx].get("text") or "").strip()
    return ""


def _picture_child_text_count(doc: dict[str, Any], pic: dict[str, Any]) -> int:
    count = 0
    for child in pic.get("children") or []:
        if isinstance(child, dict) and _text_of_ref(doc, child.get("$ref", "")):
            count += 1
    return count


def _table_header_signature(table: dict[str, Any]) -> tuple[str, ...]:
    cells = ((table.get("data") or {}).get("table_cells") or [])
    headers: list[str] = []
    for cell in cells:
        if cell.get("column_header"):
            text = re.sub(r"\s+", " ", (cell.get("text") or "").strip())
            if text:
                headers.append(text)
    return tuple(headers)


def _json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.part")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _parse_requested_formats(raw: Any, legacy: str | None = None) -> list[str]:
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            values = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            values = []
    else:
        values = []
    if not values and legacy:
        values = [legacy]
    return list(dict.fromkeys(str(value) for value in values if value))


def _returned_formats_from_members(names: list[str]) -> list[str]:
    suffix_map = {
        ".json": "json", ".md": "md", ".markdown": "md",
        ".html": "html", ".htm": "html", ".txt": "text",
        ".doctags": "doctags",
    }
    found: list[str] = []
    for name in names:
        if name.endswith("/"):
            continue
        fmt = suffix_map.get(Path(name).suffix.lower())
        if fmt and fmt not in found:
            found.append(fmt)
    return found


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _collect_uri_references(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "uri" and isinstance(child, str) and child.strip():
                refs.append(child.strip())
            else:
                refs.extend(_collect_uri_references(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_collect_uri_references(child))
    return refs


def inspect_docling_zip(path: Path) -> tuple[dict[str, Any], str, list[str], dict[str, Any]]:
    if not zipfile.is_zipfile(path):
        raise ValueError("Output is not a valid ZIP archive")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        bad_crc_member = archive.testzip()
        if bad_crc_member is not None:
            raise ValueError(f"ZIP CRC validation failed for member: {bad_crc_member}")

        json_names = [name for name in names if name.lower().endswith(".json") and not name.endswith("/")]
        if not json_names:
            raise ValueError("ZIP contains no Docling JSON export")
        json_name = max(json_names, key=lambda name: archive.getinfo(name).file_size)
        try:
            document = json.loads(archive.read(json_name))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Docling JSON could not be decoded: {exc}") from exc
        if not isinstance(document, dict) or "texts" not in document:
            raise ValueError("Selected JSON does not look like a Docling document")

        member_set = {name.lstrip("./") for name in names}
        all_refs = _collect_uri_references(document)
        internal_refs: list[str] = []
        external_refs: list[str] = []
        for raw in all_refs:
            parsed = urlparse(raw)
            if parsed.scheme or raw.startswith("data:"):
                external_refs.append(raw)
                continue
            normalized = unquote(raw).replace("\\", "/").lstrip("./")
            if normalized:
                internal_refs.append(normalized)
        unique_internal = sorted(set(internal_refs))
        missing = [ref for ref in unique_internal if ref not in member_set]
        present = len(unique_internal) - len(missing)
        status = "ok" if not missing else "warning"
        integrity = {
            "schema": "docling-archive-integrity/v1",
            "status": status,
            "display_label": display_label(status, kind="integrity"),
            "zip_container_valid": True,
            "crc_checked": True,
            "crc_error_member": None,
            "json_member": json_name,
            "json_valid": True,
            "archive_member_count": len(names),
            "referenced_artifacts": {
                "total_uri_references": len(all_refs),
                "unique_internal_references": len(unique_internal),
                "present_internal_references": present,
                "missing_internal_references": len(missing),
                "missing_samples": missing[:100],
                "external_or_embedded_references": len(external_refs),
            },
            "note": "All ZIP members passed CRC validation. Relative URI references were checked against archive members; external/data URIs are reported but not fetched.",
        }
        return document, json_name, names, integrity


def load_docling_zip(path: Path) -> tuple[dict[str, Any], str, list[str]]:
    document, json_name, names, _ = inspect_docling_zip(path)
    return document, json_name, names


def build_profile(doc: dict[str, Any]) -> dict[str, Any]:
    pages = doc.get("pages") or {}
    texts = doc.get("texts") or []
    tables = doc.get("tables") or []
    pictures = doc.get("pictures") or []
    page_count = len(pages)

    picture_classes = Counter()
    picture_confidences: list[float] = []
    for pic in pictures:
        cls, conf = _top_picture_prediction(pic)
        if cls:
            picture_classes[cls] += 1
        if conf is not None:
            picture_confidences.append(conf)

    heading_levels = Counter()
    headings: list[str] = []
    for text in texts:
        if text.get("label") == "section_header":
            level = text.get("level")
            heading_levels[str(level) if level is not None else "unknown"] += 1
            value = (text.get("text") or "").strip()
            if value:
                headings.append(value)

    combined_heading_text = "\n".join(headings)
    detected_structures = {
        "troubleshooting": bool(TROUBLESHOOT_RE.search(combined_heading_text)),
        "procedures": bool(PROCEDURE_RE.search(combined_heading_text)),
        "parts_catalogue": bool(PARTS_RE.search(combined_heading_text)),
        "technical_specs": bool(SPEC_RE.search(combined_heading_text)),
    }

    page_stats: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "texts": 0, "tables": 0, "pictures": 0, "picture_classes": Counter(), "technical_values": 0
    })
    for text in texts:
        page = _page_of(text)
        if page is not None:
            page_stats[page]["texts"] += 1
            page_stats[page]["technical_values"] += len(TECH_VALUE_RE.findall(text.get("text") or ""))
    for table in tables:
        page = _page_of(table)
        if page is not None:
            page_stats[page]["tables"] += 1
    for pic in pictures:
        page = _page_of(pic)
        if page is not None:
            page_stats[page]["pictures"] += 1
            cls, _ = _top_picture_prediction(pic)
            if cls:
                page_stats[page]["picture_classes"][cls] += 1

    page_kinds = Counter()
    samples: dict[str, list[int]] = defaultdict(list)
    for page in range(1, page_count + 1):
        stat = page_stats[page]
        classes: Counter = stat["picture_classes"]
        if classes.get("engineering_drawing", 0) or classes.get("flow_chart", 0):
            kind = "engineering_visual"
        elif stat["tables"] >= 1 and stat["pictures"] == 0:
            kind = "table_or_list"
        elif stat["pictures"] > 0 and stat["texts"] > 0:
            kind = "mixed"
        elif stat["pictures"] > 0:
            kind = "visual"
        else:
            kind = "text"
        page_kinds[kind] += 1
        if len(samples[kind]) < 12:
            samples[kind].append(page)

    if page_kinds.get("engineering_visual", 0) >= max(3, page_count * 0.10):
        primary_kind = "mixed_technical_manual"
    elif page_kinds.get("table_or_list", 0) >= max(3, page_count * 0.25):
        primary_kind = "table_heavy_document"
    elif page_kinds.get("visual", 0) + page_kinds.get("mixed", 0) >= max(3, page_count * 0.35):
        primary_kind = "visual_document"
    else:
        primary_kind = "text_document"

    technical_value_count = sum(len(TECH_VALUE_RE.findall(t.get("text") or "")) for t in texts)

    return {
        "schema": "docling-quality-profile/v1",
        "document_name": doc.get("name"),
        "docling_schema": doc.get("schema_name"),
        "docling_version": doc.get("version"),
        "primary_kind": primary_kind,
        "counts": {
            "pages": page_count,
            "texts": len(texts),
            "tables": len(tables),
            "pictures": len(pictures),
            "technical_value_mentions": technical_value_count,
        },
        "page_kinds": dict(page_kinds),
        "page_kind_samples": dict(samples),
        "picture_classes": dict(picture_classes.most_common()),
        "picture_confidence": {
            "mean": round(sum(picture_confidences) / len(picture_confidences), 4) if picture_confidences else None,
            "minimum": round(min(picture_confidences), 4) if picture_confidences else None,
        },
        "heading_levels": dict(heading_levels),
        "detected_structures": detected_structures,
        "rule_scope": {
            "structure_keyword_language": "English/Latin-oriented",
            "technical_value_patterns": "common Latin/SI engineering units",
            "ocr_confusion_patterns": "selected Latin OCR confusions",
            "note": "A clean result means no configured pattern matched; it does not prove equivalent coverage for every language/OCR engine.",
        },
    }


def build_diagnostics(doc: dict[str, Any], config: AppConfig, integrity: dict[str, Any] | None = None) -> dict[str, Any]:
    texts = doc.get("texts") or []
    tables = doc.get("tables") or []
    pictures = doc.get("pictures") or []
    pages = doc.get("pages") or {}
    page_count = len(pages)
    signals: list[dict[str, Any]] = []

    heading_check = _heading_hierarchy_check(doc, config.heading_consistency_min_group, config.heading_validation_min_coverage)
    if heading_check["anomaly_count"]:
        heading_items = heading_check["anomalies"] + heading_check["mapping_anomalies"]
        signals.append({
            "code": "HEADING_HIERARCHY_INCONSISTENCY",
            "classification": "HUMAN_REVIEW",
            "severity": "medium",
            "action": "review_heading_hierarchy_before_chunking",
            "count": heading_check["anomaly_count"],
            "items": heading_items,
            "samples": heading_items[:30],
            "note": "Uses document-internal numbering consistency; level >5 alone is never treated as an error.",
        })

    reading_check = _reading_order_check(
        doc,
        threshold=config.reading_order_inversion_threshold,
        min_items=config.reading_order_min_items,
        min_coverage=config.reading_order_min_coverage,
    )
    if reading_check["anomaly_count"]:
        signals.append({
            "code": "READING_ORDER_ANOMALY",
            "classification": "HUMAN_REVIEW",
            "severity": "medium",
            "action": "review_layout_order_before_chunking",
            "count": reading_check["anomaly_count"],
            "items": reading_check["anomalies"],
            "samples": reading_check["anomalies"][:30],
            "note": "Layout-aware check ignores furniture, visual overlays and accepts row- or column-major order on two-column pages.",
        })

    coverage_warnings: list[dict[str, Any]] = []
    if heading_check["status"] in {"limited", "not_evaluable"}:
        coverage_warnings.append({
            "check": "heading_hierarchy",
            "status": heading_check["status"],
            "coverage_ratio": heading_check.get("validation_coverage_ratio"),
            "checked": heading_check.get("evaluable_headings"),
            "total": heading_check.get("total_section_headings"),
            "reason": "zero anomalies is inconclusive when few headings use a supported numbering scheme",
        })
    if reading_check["status"] in {"limited", "not_evaluable"}:
        coverage_warnings.append({
            "check": "reading_order",
            "status": reading_check["status"],
            "coverage_ratio": reading_check.get("coverage_ratio_total_pages"),
            "checked": reading_check.get("pages_checked"),
            "total": reading_check.get("total_pages"),
            "reason": "few pages were comparable under the current geometry model",
        })
    if reading_check.get("pages_using_a4_fallback_size"):
        coverage_warnings.append({
            "check": "page_geometry",
            "status": "fallback_used",
            "count": reading_check["pages_using_a4_fallback_size"],
            "reason": "missing page-size metadata used the 595x842pt fallback",
        })
    if coverage_warnings:
        signals.append({
            "code": "VALIDATION_COVERAGE_LIMITED",
            "classification": "INFO",
            "severity": "info",
            "count": len(coverage_warnings),
            "items": coverage_warnings,
            "samples": coverage_warnings[:20],
            "note": "Coverage warnings mean the checker was not broadly exercised; they are not extraction errors.",
        })

    if integrity and integrity.get("status") != "ok":
        missing = ((integrity.get("referenced_artifacts") or {}).get("missing_samples") or [])
        signals.append({
            "code": "ARCHIVE_ARTIFACT_MISSING",
            "classification": "HUMAN_REVIEW",
            "severity": "high",
            "action": "restore_or_reconvert_docling_archive_before_visual_verification",
            "count": (integrity.get("referenced_artifacts") or {}).get("missing_internal_references", len(missing)),
            "items": [{"artifact": item} for item in missing],
            "samples": [{"artifact": item} for item in missing[:30]],
            "note": "Docling JSON references artifacts that are absent from the ZIP; visual verification may be incomplete.",
        })

    # Furniture is source metadata, not an extraction error. It is safe to
    # exclude page headers/footers from semantic chunks without deleting them.
    furniture_indices = []
    for i, text in enumerate(texts):
        if text.get("label") in {"page_header", "page_footer"}:
            furniture_indices.append(i)
    if furniture_indices:
        signals.append({
            "code": "SEMANTIC_FURNITURE",
            "classification": "RULE_FIX",
            "severity": "info",
            "action": "exclude_from_semantic_chunks",
            "count": len(furniture_indices),
            "samples": furniture_indices[:20],
            "note": "Preserve in source JSON; exclude only from retrieval chunks.",
        })

    # Repeated short strings are likely running furniture. This is only a
    # candidate filter; no source text is deleted.
    short_counts = Counter()
    short_first: dict[str, int] = {}
    for i, text in enumerate(texts):
        value = re.sub(r"\s+", " ", (text.get("text") or "").strip())
        if 2 <= len(value) <= 100:
            short_counts[value] += 1
            short_first.setdefault(value, i)
    repeat_threshold = max(5, math.ceil(page_count * 0.30)) if page_count else 5
    repeated = [
        {"text": value, "count": count, "first_text_index": short_first[value]}
        for value, count in short_counts.items() if count >= repeat_threshold
    ]
    repeated.sort(key=lambda item: item["count"], reverse=True)
    if repeated:
        signals.append({
            "code": "REPEATED_RUNNING_TEXT",
            "classification": "RULE_FIX",
            "severity": "info",
            "action": "candidate_running_header_footer_filter",
            "count": len(repeated),
            "samples": repeated[:20],
        })

    # Tables with no marked header are not necessarily wrong; report them as
    # INFO rather than automatically changing table structure.
    no_header = []
    signatures = Counter()
    table_signatures: list[tuple[str, ...]] = []
    for i, table in enumerate(tables):
        sig = _table_header_signature(table)
        table_signatures.append(sig)
        if sig:
            signatures[sig] += 1
        else:
            no_header.append({"table_index": i, "page": _page_of(table)})
    if no_header:
        signals.append({
            "code": "TABLE_WITHOUT_MARKED_HEADER",
            "classification": "INFO",
            "severity": "info",
            "count": len(no_header),
            "samples": no_header[:20],
            "note": "Absence of column_header metadata is not by itself an extraction error.",
        })

    orphan_tables = []
    for i, table in enumerate(tables):
        sig = table_signatures[i]
        if sig and signatures[sig] >= 5 and not table.get("captions"):
            orphan_tables.append({
                "table_index": i,
                "page": _page_of(table),
                "header_signature": list(sig),
                "repeat_count": signatures[sig],
            })
    if orphan_tables:
        signals.append({
            "code": "REPEATED_TABLE_NEEDS_CONTEXT",
            "classification": "RULE_FIX",
            "severity": "medium",
            "action": "stitch_nearest_preceding_heading_before_chunking",
            "count": len(orphan_tables),
            "samples": orphan_tables[:30],
            "note": "Context stitching changes chunk metadata, not the original table cells.",
        })

    # Conservative OCR suspicion: only patterns with strong evidence of a
    # recognition problem. Engineering symbols/non-ASCII characters are NOT
    # treated as noise.
    suspicious_texts = []
    for i, text in enumerate(texts):
        value = text.get("text") or ""
        reasons = []
        if REPLACEMENT_RE.search(value):
            reasons.append("unicode_replacement_character")
        if CONTROL_RE.search(value):
            reasons.append("control_character")
        if DIGIT_LOWER_L_RE.search(value):
            reasons.append("lowercase_l_inside_numeric_token")
        if OCR_UNIT_RE.search(value) or LOWER_L_UNIT_RE.search(value):
            reasons.append("unit_like_ocr_confusion")
        reasons.extend(reason for reason in _generic_ocr_garble_reasons(value) if reason not in reasons)
        if reasons:
            suspicious_texts.append({
                "text_index": i,
                "page": _page_of(text),
                "label": text.get("label"),
                "reasons": reasons,
                "text": value[:240],
                "contains_technical_value": bool(TECH_VALUE_RE.search(value) or INEQUALITY_RE.search(value)),
            })
    if suspicious_texts:
        signals.append({
            "code": "SUSPICIOUS_OCR_TEXT",
            "classification": "TEXT_REVIEW",
            "severity": "high" if any(x["contains_technical_value"] for x in suspicious_texts) else "medium",
            "count": len(suspicious_texts),
            "items": suspicious_texts,
            "samples": suspicious_texts[:50],
            "note": "Route for evidence gathering; do not auto-correct technical values.",
        })

    # Vision routing is intentionally conservative. High-confidence engineering
    # drawings are recorded for on-demand use but are not automatically queued.
    vision_review = []
    visual_inventory = []
    technical_visual_classes = {
        "engineering_drawing", "flow_chart", "screenshot_from_manual", "table",
        "line_chart", "bar_chart", "box_plot", "full_page_image", "geographical_map"
    }
    for i, pic in enumerate(pictures):
        cls, conf = _top_picture_prediction(pic)
        child_texts = _picture_child_text_count(doc, pic)
        item = {
            "picture_index": i,
            "page": _page_of(pic),
            "class": cls,
            "confidence": round(conf, 4) if conf is not None else None,
            "child_text_count": child_texts,
            "artifact": ((pic.get("image") or {}).get("uri")),
        }
        if cls in technical_visual_classes:
            visual_inventory.append(item)
        if conf is None or (cls in technical_visual_classes and conf < config.picture_review_confidence):
            item = dict(item)
            item["reason"] = "missing_or_low_picture_classification_confidence"
            vision_review.append(item)
    if visual_inventory:
        signals.append({
            "code": "TECHNICAL_VISUAL_INVENTORY",
            "classification": "INFO",
            "severity": "info",
            "count": len(visual_inventory),
            "samples": visual_inventory[:30],
            "note": "High-confidence technical visuals stay on-demand; they are not all sent to the phone VLM.",
        })
    if vision_review:
        signals.append({
            "code": "LOW_CONFIDENCE_VISUAL",
            "classification": "VISION_REVIEW",
            "severity": "medium",
            "count": len(vision_review),
            "items": vision_review,
            "samples": vision_review[:50],
        })

    # Inventory high-risk facts so later correction gates can protect them.
    critical_fact_samples = []
    critical_count = 0
    for i, text in enumerate(texts):
        value = text.get("text") or ""
        hits = [m.group(0).strip() for m in TECH_VALUE_RE.finditer(value)]
        if hits:
            critical_count += len(hits)
            if len(critical_fact_samples) < 100:
                critical_fact_samples.append({
                    "text_index": i,
                    "page": _page_of(text),
                    "values": hits[:10],
                    "text": value[:280],
                })
    if critical_count:
        signals.append({
            "code": "TECHNICAL_FACT_INVENTORY",
            "classification": "INFO",
            "severity": "info",
            "count": critical_count,
            "samples": critical_fact_samples[:30],
            "note": "Values are protected evidence; inventory does not imply they are erroneous.",
        })

    coverage_status = "limited" if coverage_warnings else ("warning" if integrity and integrity.get("status") != "ok" else "ok")
    coverage = {
        "schema": "docling-validation-coverage/v1",
        "overall_status": coverage_status,
        "overall_display_label": display_label(coverage_status, kind="coverage"),
        "checks": {
            "heading_hierarchy": {
                "status": heading_check["status"],
                "display_label": heading_check["display_label"],
                "checked": heading_check.get("evaluable_headings", 0),
                "total": heading_check.get("total_section_headings", 0),
                "coverage_ratio": heading_check.get("validation_coverage_ratio"),
                "numbered_headings_recognized": heading_check.get("numbered_headings_checked", 0),
            },
            "reading_order": {
                "status": reading_check["status"],
                "display_label": reading_check["display_label"],
                "checked": reading_check.get("pages_checked", 0),
                "total": reading_check.get("total_pages", page_count),
                "coverage_ratio": reading_check.get("coverage_ratio_total_pages"),
                "eligible_pages": reading_check.get("eligible_pages", 0),
                "skipped_pages_by_reason": reading_check.get("skipped_pages_by_reason", {}),
            },
            "ocr_confusion_scan": {
                "status": "scanned",
                "display_label": display_label("scanned", kind="aux"),
                "checked": len(texts),
                "total": len(texts),
                "coverage_ratio": 1.0 if texts else None,
                "pattern_scope": "selected Latin OCR corruption patterns; not language-universal",
            },
            "technical_value_scan": {
                "status": "scanned",
                "display_label": display_label("scanned", kind="aux"),
                "checked": len(texts),
                "total": len(texts),
                "coverage_ratio": 1.0 if texts else None,
                "pattern_scope": "common Latin/SI engineering units; not language/unit-universal",
            },
            "vision_inventory": {
                "status": "inventoried",
                "display_label": display_label("inventoried", kind="aux"),
                "pictures_examined": len(pictures),
                "technical_visuals": len(visual_inventory),
                "actively_routed_for_review": len(vision_review),
                "active_review_ratio_of_technical_visuals": round(len(vision_review) / len(visual_inventory), 4) if visual_inventory else None,
                "policy": "high-confidence technical visuals remain on-demand",
            },
            "archive_integrity": {
                "status": integrity.get("status") if integrity else "not_checked_in_this_call",
                "display_label": display_label(integrity.get("status") if integrity else "not_checked_in_this_call", kind="integrity"),
                "missing_artifacts": ((integrity or {}).get("referenced_artifacts") or {}).get("missing_internal_references"),
            },
        },
        "warnings": coverage_warnings,
    }

    counts = Counter(signal["classification"] for signal in signals for _ in range(signal.get("count", 1) if signal["classification"] in {"TEXT_REVIEW", "VISION_REVIEW", "RULE_FIX", "HUMAN_REVIEW"} else 0))
    return {
        "schema": "docling-quality-diagnostics/v1",
        "summary": {
            "signal_groups": len(signals),
            "rule_fix_items": counts.get("RULE_FIX", 0),
            "text_review_items": counts.get("TEXT_REVIEW", 0),
            "vision_review_items": counts.get("VISION_REVIEW", 0),
            "human_review_items": counts.get("HUMAN_REVIEW", 0),
            "heading_hierarchy_anomalies": heading_check["anomaly_count"],
            "reading_order_anomaly_pages": reading_check["anomaly_count"],
            "validation_coverage_status": coverage["overall_status"],
            "validation_coverage_display_label": coverage["overall_display_label"],
            "validation_coverage_warnings": len(coverage_warnings),
            "archive_integrity_status": integrity.get("status") if integrity else "not_checked_in_this_call",
            "archive_integrity_display_label": display_label(integrity.get("status") if integrity else "not_checked_in_this_call", kind="integrity"),
        },
        "coverage": coverage,
        "checks": {
            "heading_hierarchy": heading_check,
            "reading_order": reading_check,
        },
        "signals": signals,
    }


def build_routes(doc: dict[str, Any], diagnostics: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []

    def add(target: str, code: str, priority: str, source: dict[str, Any], action: str, reason: str) -> None:
        if len(routes) >= config.max_routes_per_document:
            return
        routes.append({
            "route_id": f"R{len(routes)+1:05d}",
            "target": target,
            "code": code,
            "priority": priority,
            "status": "pending",
            "source": source,
            "action": action,
            "reason": reason,
        })

    for signal in diagnostics.get("signals", []):
        cls = signal.get("classification")
        code = signal.get("code")
        if cls == "TEXT_REVIEW":
            for sample in signal.get("items", signal.get("samples", [])):
                add(
                    "pi5",
                    code,
                    "high" if sample.get("contains_technical_value") else "medium",
                    {"type": "text", "index": sample.get("text_index"), "page": sample.get("page")},
                    "gather_text_evidence_then_verify_candidate",
                    ", ".join(sample.get("reasons") or []),
                )
        elif cls == "VISION_REVIEW":
            for sample in signal.get("items", signal.get("samples", [])):
                add(
                    "oneplus",
                    code,
                    "medium",
                    {
                        "type": "picture",
                        "index": sample.get("picture_index"),
                        "page": sample.get("page"),
                        "artifact": sample.get("artifact"),
                    },
                    "full_image_first_then_quality_gate_then_overlap_crops_if_needed",
                    sample.get("reason") or "visual ambiguity",
                )
        elif cls == "HUMAN_REVIEW":
            add(
                "human",
                code,
                signal.get("severity", "medium"),
                {"type": "diagnostic_group", "count": signal.get("count", 0)},
                signal.get("action") or "review_before_chunking",
                signal.get("note") or code,
            )
        elif cls == "RULE_FIX":
            # Rule fixes are represented as grouped operations rather than one
            # route per header/table to avoid huge queues.
            add(
                "rule",
                code,
                signal.get("severity", "info"),
                {"type": "diagnostic_group", "count": signal.get("count", 0)},
                signal.get("action") or "apply_non_destructive_rule",
                signal.get("note") or code,
            )

    truncated = len(routes) >= config.max_routes_per_document
    target_counts = Counter(route["target"] for route in routes)
    return {
        "schema": "docling-quality-routes/v1",
        "policy": {
            "raw_docling_is_immutable": True,
            "external_verifiers_enabled": config.external_verifiers_enabled,
            "verification_execution_enabled": config.stage2b_enabled,
            "picture_strategy": "full image -> quality gate -> overlapping crops only when unresolved",
            "text_strategy": "candidate vs trusted evidence; no autonomous engineering-value rewrite",
            "max_routes_per_document": config.max_routes_per_document,
        },
        "summary": {
            "routes": len(routes),
            "by_target": dict(target_counts),
            "truncated": truncated,
        },
        "routes": routes,
    }


class PostprocessWorker:
    def __init__(
        self,
        config_getter: Any,
        store: PostprocessStore,
        events: EventBroker,
    ) -> None:
        self._config_getter = config_getter
        self._store = store
        self._events = events
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._verifier_task: asyncio.Task[None] | None = None
        self.verifier_status: dict[str, Any] = {
            "pi5": {"reachable": None, "model": None, "detail": "not checked"},
            "oneplus": {"reachable": None, "model": None, "detail": "not checked"},
        }
        # Cache non-Docling ZIP signatures so unrelated archives in converted/
        # are not fully inspected on every poll. A changed file is retried.
        self._rejected_converted_zips: dict[str, tuple[int, int]] = {}

    async def start(self) -> None:
        config = self._config_getter()
        Path(config.processed_dir).mkdir(parents=True, exist_ok=True)
        await self._store.initialize()
        await self._store.recover_interrupted()
        if config.postprocess_enabled:
            self._task = asyncio.create_task(self._loop(), name="docling-postprocess-worker")
        self._verifier_task = asyncio.create_task(self._verifier_health_loop(), name="verifier-health-monitor")

    async def stop(self) -> None:
        self._stopping.set()
        tasks = [task for task in (self._task, self._verifier_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _discover_converted_folder(self) -> int:
        """Register valid Docling ZIPs manually dropped into converted/.

        Watcher-produced ZIPs are skipped because their conversion job already
        owns the output filename. Only new/changed unowned ZIPs are inspected,
        so normal polling does not repeatedly hash large manuals.
        """
        config = self._config_getter()
        output_dir = Path(config.output_dir)
        if not output_dir.is_dir():
            return 0
        registered = 0
        for path in sorted(output_dir.glob("*.zip"), key=lambda item: item.name.lower()):
            if path.name.startswith("."):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            signature = (stat.st_size, stat.st_mtime_ns)
            if self._rejected_converted_zips.get(path.name) == signature:
                continue
            if not await self._store.converted_output_needs_registration(
                path.name, stat.st_size, stat.st_mtime_ns
            ):
                continue
            try:
                # This validates CRC + Docling JSON shape before the file is
                # surfaced as a Stage-2 document. Non-Docling ZIPs are simply
                # ignored and never enter the quality queue.
                _doc, _json_name, names, _integrity = await asyncio.to_thread(
                    inspect_docling_zip, path
                )
                digest = await asyncio.to_thread(sha256_file, path)
                formats = _returned_formats_from_members(names)
                job_id = await self._store.register_converted_output(
                    path.name, stat.st_size, stat.st_mtime_ns, digest, formats
                )
                if job_id is not None:
                    self._rejected_converted_zips.pop(path.name, None)
                    registered += 1
            except (OSError, ValueError, zipfile.BadZipFile):
                # converted/ may contain unrelated archives. Only valid Docling
                # exports belong in Quality & Routing. Cache this exact file
                # signature; replacing/changing it automatically enables retry.
                self._rejected_converted_zips[path.name] = signature
                continue
        return registered

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                imported = await self._discover_converted_folder()
                if imported:
                    self._events.notify("postprocess_imported")
                created = await self._store.discover_completed_conversions()
                if created:
                    self._events.notify("postprocess_discovered")
                processed = await self._process_one()
                if processed:
                    continue
            except Exception:
                self._events.notify("postprocess_error")
            await asyncio.sleep(self._config_getter().postprocess_poll_interval_seconds)

    async def _process_one(self) -> bool:
        job = await self._store.next_pending()
        if not job:
            return False
        config = self._config_getter()
        output_path = Path(config.output_dir) / job["output_filename"]
        started = time.monotonic()
        await self._store.mark_processing(job["id"])
        self._events.notify("postprocess_started")
        try:
            if not output_path.is_file():
                raise FileNotFoundError(f"Converted ZIP not found: {output_path}")
            output_sha = await asyncio.to_thread(sha256_file, output_path)
            doc, json_name, names, integrity = await asyncio.to_thread(inspect_docling_zip, output_path)
            profile = await asyncio.to_thread(build_profile, doc)
            diagnostics = await asyncio.to_thread(build_diagnostics, doc, config, integrity)
            routes = await asyncio.to_thread(build_routes, doc, diagnostics, config)
            conversion_job = await self._store.get_conversion_job(job["conversion_job_id"])
            source_kind = (conversion_job or {}).get("source_kind") or "watcher"
            requested_formats = _parse_requested_formats(
                (conversion_job or {}).get("output_formats"),
                (conversion_job or {}).get("output_format"),
            )
            returned_formats = _returned_formats_from_members(names)
            missing_requested_formats = [fmt for fmt in requested_formats if fmt not in returned_formats]
            format_delivery = {
                "status": "warning" if missing_requested_formats else "ok",
                "display_label": "Some requested formats missing" if missing_requested_formats else "All requested formats present",
                "requested_formats": requested_formats,
                "returned_formats": returned_formats,
                "missing_requested_formats": missing_requested_formats,
            }

            stem = Path(job["output_filename"]).stem
            result_dir = Path(config.processed_dir) / f"{stem}__job{job['conversion_job_id']}"
            manifest = {
                "schema": "docling-quality-manifest/v1",
                "conversion_job_id": job["conversion_job_id"],
                "source_filename": job["source_filename"],
                "source_kind": source_kind,
                "converted_zip": job["output_filename"],
                "converted_zip_sha256": output_sha,
                "docling_json_member": json_name,
                "archive_member_count": len(names),
                "archive_integrity_status": integrity["status"],
                "missing_referenced_artifacts": integrity["referenced_artifacts"]["missing_internal_references"],
                "requested_formats": requested_formats,
                "returned_formats": returned_formats,
                "missing_requested_formats": missing_requested_formats,
                "format_delivery_status": format_delivery["status"],
                "format_delivery": format_delivery,
                "raw_docling_immutable": True,
            }
            ledger = {
                "schema": "docling-correction-ledger/v1",
                "source_zip_sha256": output_sha,
                "policy": "No corrections are applied in Stage 2A. Every future change must record before/after/evidence/verifier/confidence.",
                "entries": [],
            }
            await asyncio.to_thread(_json_atomic, result_dir / "source_manifest.json", manifest)
            await asyncio.to_thread(_json_atomic, result_dir / "integrity.json", integrity)
            await asyncio.to_thread(_json_atomic, result_dir / "coverage.json", diagnostics["coverage"])
            await asyncio.to_thread(_json_atomic, result_dir / "profile.json", profile)
            await asyncio.to_thread(_json_atomic, result_dir / "diagnostics.json", diagnostics)
            await asyncio.to_thread(_json_atomic, result_dir / "routes.json", routes)
            await asyncio.to_thread(_json_atomic, result_dir / "correction_ledger.json", ledger)
            summary = {
                "schema": "docling-quality-summary/v1",
                "primary_kind": profile["primary_kind"],
                "counts": profile["counts"],
                "integrity": {
                    "status": integrity["status"],
                    "display_label": integrity["display_label"],
                    "missing_referenced_artifacts": integrity["referenced_artifacts"]["missing_internal_references"],
                },
                "format_delivery": format_delivery,
                "coverage": {
                    "status": diagnostics["coverage"]["overall_status"],
                    "display_label": diagnostics["coverage"]["overall_display_label"],
                    "warnings": len(diagnostics["coverage"]["warnings"]),
                    "heading_ratio": diagnostics["coverage"]["checks"]["heading_hierarchy"]["coverage_ratio"],
                    "reading_order_ratio": diagnostics["coverage"]["checks"]["reading_order"]["coverage_ratio"],
                },
                "diagnostics": diagnostics["summary"],
                "routes": routes["summary"],
            }
            await asyncio.to_thread(_json_atomic, result_dir / "summary.json", summary)

            seconds = time.monotonic() - started
            await self._store.mark_completed(
                job["id"], seconds, result_dir.name, output_sha,
                profile["primary_kind"], routes["summary"]["routes"],
            )
            self._events.notify("postprocess_completed")
        except Exception as exc:
            seconds = time.monotonic() - started
            await self._store.mark_failed(job["id"], type(exc).__name__, str(exc), seconds)
            self._events.notify("postprocess_failed")
        return True

    async def _verifier_health_loop(self) -> None:
        while not self._stopping.is_set():
            config = self._config_getter()
            try:
                pi5 = OpenAICompatibleVerifier(config.pi5_url)
                oneplus = OpenAICompatibleVerifier(config.oneplus_url)
                p, o = await asyncio.gather(pi5.health(), oneplus.health())
                self.verifier_status = {
                    "pi5": {"reachable": p.reachable, "model": p.model, "detail": p.detail},
                    "oneplus": {"reachable": o.reachable, "model": o.model, "detail": o.detail},
                }
            except Exception as exc:
                self.verifier_status = {
                    "pi5": {"reachable": False, "model": None, "detail": str(exc)},
                    "oneplus": {"reachable": False, "model": None, "detail": str(exc)},
                }
            await asyncio.sleep(config.verifier_health_interval_seconds)
