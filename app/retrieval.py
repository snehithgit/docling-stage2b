from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
import zipfile

import fitz

from .archive import select_docling_document


_SCHEMA_INDEX = "docling-retrieval-index/v1"
_SCHEMA_QUALITY = "docling-retrieval-quality/v1"
_SCHEMA_BENCHMARK = "docling-retrieval-benchmark/v2"

# Generic technical-reference and identifier handling. These rules deliberately
# know nothing about manufacturers or individual books.
_DEFINITION_INTENT_RE = re.compile(
    r"\b(?:what\s+is|what\s+does|what\b.{0,80}\bdo\b|function\s+of|refer(?:s|red)?\s+to|means?|identify|identification)\b",
    re.IGNORECASE,
)
_PROCEDURE_INTENT_RE = re.compile(
    r"\b(?:how\s+(?:do|to)|procedure|check|adjust|test|measure|inspect|replace|remove|install|change|calibrat(?:e|ion)|zero\s+set(?:ting)?)\b",
    re.IGNORECASE,
)
_TROUBLESHOOT_INTENT_RE = re.compile(
    r"\b(?:what\s+to\s+do|alarm|fault|trip|failure|failed|not\s+working|problem|error|trouble|cause|remedy|corrective|why)\b",
    re.IGNORECASE,
)
_FUNCTION_INTENT_RE = re.compile(r"\b(?:what\b.{0,80}\bdo\b|what\s+does|function\s+of|how\s+does|purpose\s+of)\b", re.IGNORECASE)
_ACTION_REQUEST_RE = re.compile(r"\b(?:what\s+to\s+do|what\s+to\s+check|how\s+to\s+(?:fix|troubleshoot|clear|reset))\b", re.IGNORECASE)

_ATTRIBUTE_UNITS = {
    "voltage": {"v", "vac", "vdc", "mv", "kv"},
    "pressure": {"bar", "mpa", "pa", "kpa", "psi", "kg/cm2"},
    "temperature": {"c", "f", "°c", "°f"},
    "torque": {"nm", "n-m"},
    "current": {"a", "ma", "ka"},
    "resistance": {"ohm", "ohms", "kohm", "mohm"},
    "frequency": {"hz", "khz"},
    "speed": {"rpm"},
    "flow": {"l/min", "lpm", "m3/h", "m³/h"},
    "clearance": {"mm", "cm"},
}
_ATTRIBUTE_ALIASES = {
    "volt": "voltage", "volts": "voltage", "voltage": "voltage",
    "press": "pressure", "pressure": "pressure",
    "temp": "temperature", "temperature": "temperature",
    "torque": "torque", "current": "current", "amp": "current", "amps": "current",
    "resistance": "resistance", "frequency": "frequency", "speed": "speed",
    "flow": "flow", "clearance": "clearance",
}
_QUERY_ACTION_TERMS = {
    "procedure", "check", "adjust", "test", "measure", "inspect", "replace", "remove",
    "install", "change", "calibrate", "calibration", "zero", "set", "setting", "do",
    "refer", "refers", "mean", "means", "identify", "alarm", "fault", "trip", "failure",
    "failed", "problem", "error", "trouble", "cause", "remedy", "corrective", "came",
}
_PROCEDURE_EVIDENCE = {
    "check", "adjust", "measure", "inspect", "remove", "install", "operate", "connect",
    "start", "stop", "loosen", "tighten", "drain", "replace", "set", "turn", "open",
    "close", "disconnect", "reconnect", "clean", "fill", "apply", "press", "verify",
}
_TROUBLESHOOT_EVIDENCE = {
    "check", "inspect", "reset", "replace", "fault", "faulty", "failure", "alarm", "error",
    "cause", "causes", "remedy", "corrective", "operated", "trip", "tripped", "stopped",
    "stop", "measure", "verify", "normal", "abnormal", "open", "closed", "broken",
}
_FUNCTION_EVIDENCE = {
    "operate", "operates", "operated", "control", "controls", "controlled", "release", "releases",
    "released", "block", "blocks", "blocked", "open", "opens", "close", "closes", "supply",
    "supplies", "drive", "drives", "start", "starts", "stop", "stops", "shift", "shifts",
    "connect", "connects", "disconnect", "energize", "energizes", "deenergize", "actuate", "actuates",
}
_CONTACT_CONTEXT_RE = re.compile(r"\b(?:tel|telephone|fax|phone|mobile|contact|office|address|www|email|e-mail)\b", re.IGNORECASE)

_ENGINEERING_UNITS = {
    "a", "bar", "c", "f", "hz", "ka", "kg", "kn", "kw", "l", "ma", "mm", "mpa", "nm",
    "pa", "psi", "rpm", "s", "v", "vac", "vdc", "w", "ohm", "ohms",
}
# Structured tokens must be matched before bare numbers so 2141-101 stays one
# identifier instead of being indexed as unrelated tokens 2141 and 101.

# Intentionally small: remove conversational filler while retaining technical terms.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is", "it",
    "of", "on", "or", "that", "the", "this", "to", "what", "when", "where", "which", "with", "why",
}
_TOKEN_RE = re.compile(
    r"[a-z0-9]+(?:[-_/.+:][a-z0-9]+)+|\d+(?:\.\d+)?(?:°[cf])?|[a-z]+|\d+",
    re.IGNORECASE,
)

_REFERENCE_PATTERNS = [
    # Title first: See instruction "High pressure pumps" in section 6.1
    re.compile(r"\b(?:see|refer\s+to)\s+(?:the\s+)?(?:instruction\s+)?[\"“”\'](?P<title>[^\"“”\']{2,140})[\"“”\'](?:\s*,?\s*(?:in|under)?\s*(?:section|chapter|clause)\s+(?P<section>[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*))?", re.IGNORECASE),
    # Section first: See instruction under section 6.1 "High pressure pumps"
    re.compile(r"\b(?:see|refer\s+to)\s+(?:the\s+)?(?:instruction\s+)?(?:under|in)?\s*(?:section|chapter|clause)\s+(?P<section>[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*)\s*[,;:]?\s*[\"“”\'](?P<title>[^\"“”\']{2,140})[\"“”\']", re.IGNORECASE),
    # Named unquoted reference followed by a section/chapter. Keep this conservative.
    re.compile(r"\b(?:see|refer\s+to)\s+(?:the\s+)?(?P<title>[A-Za-z][A-Za-z0-9 /&()_-]{2,100}?)(?:\s*,?\s*(?:in|under)?\s*(?:section|chapter|clause)\s+(?P<section>[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*))(?=[.;,]|$)", re.IGNORECASE),
    # Bare section reference is retained only when no title is available.
    re.compile(r"\b(?:see|refer\s+to)\s+(?:instruction\s+)?(?:in|under)?\s*(?:section|chapter|clause)\s+(?P<section>[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*)", re.IGNORECASE),
]


def _tokens(value: str) -> list[str]:
    values = [m.group(0).lower() for m in _TOKEN_RE.finditer(value or "")]
    return [token for token in values if token not in _STOPWORDS and len(token) > 1]


def _normalized(value: str) -> str:
    return " ".join(_tokens(value))


def _query_attribute(query: str) -> str | None:
    tokens = _tokens(query)
    for token in tokens:
        attr = _ATTRIBUTE_ALIASES.get(token)
        if attr:
            return attr
    return None


def _query_subject_terms(query: str, attribute: str | None = None) -> list[str]:
    terms: list[str] = []
    for token in _tokens(query):
        if token in _QUERY_ACTION_TERMS:
            continue
        if attribute and _ATTRIBUTE_ALIASES.get(token) == attribute:
            continue
        if token in _ENGINEERING_UNITS:
            continue
        terms.append(token)
    return list(dict.fromkeys(terms))


def _raw_has_unit(text: str, units: set[str]) -> bool:
    lower = re.sub(r"\s+", " ", (text or "").lower())
    for unit in units:
        compact = unit.lower().strip()
        if re.search(rf"[-+]?\d+(?:[.,]\d+)?\s*{re.escape(compact)}(?=$|[^a-z])", lower):
            return True
    return False

def _subject_overlap(row: dict[str, Any], subject_terms: list[str]) -> tuple[int, float, int]:
    if not subject_terms:
        return 0, 0.0, 0
    tokens = set(row.get("_tokens") or _tokens(str(row.get("text") or "")))
    headings = set(row.get("_heading_tokens") or _tokens(" ".join(row.get("headings") or [])))
    source_tokens = set(re.findall(r"[a-z0-9]+", str(row.get("source_filename") or "").lower()))
    matches = sum(term in tokens or term in headings or term in source_tokens for term in subject_terms)
    heading_matches = sum(term in headings for term in subject_terms)
    source_matches = sum(term in source_tokens for term in subject_terms)
    return matches, matches / max(1, len(subject_terms)), heading_matches + source_matches

def _subject_cohesion(row: dict[str, Any], subject_terms: list[str]) -> float:
    if len(subject_terms) < 2:
        return 0.0
    tokens = list(row.get("_tokens") or _tokens(str(row.get("text") or "")))
    positions = {term: [i for i, token in enumerate(tokens) if token == term] for term in subject_terms}
    if any(not values for values in positions.values()):
        return 0.0
    # Generic small-query case: use the tightest span containing every subject term.
    best = None
    for first in positions[subject_terms[0]]:
        chosen = [first]
        for term in subject_terms[1:]:
            chosen.append(min(positions[term], key=lambda pos: abs(pos - first)))
        span = max(chosen) - min(chosen)
        best = span if best is None else min(best, span)
    if best is None:
        return 0.0
    if best <= 6:
        return 3.4
    if best <= 15:
        return 2.0
    if best <= 30:
        return 0.8
    return 0.0


def _query_feature_score(row: dict[str, Any], query: str) -> float:
    """Generic reranking features independent of any manufacturer or book."""
    if not query:
        return 0.0
    counter = Counter(row.get("_tokens") or _tokens(str(row.get("text") or "")))
    raw_text = str(row.get("text") or "")
    attribute = _query_attribute(query)
    subject_terms = _query_subject_terms(query, attribute)
    matches, ratio, structural_subject_matches = _subject_overlap(row, subject_terms)
    procedure = bool(_PROCEDURE_INTENT_RE.search(query))
    troubleshoot = bool(_TROUBLESHOOT_INTENT_RE.search(query))
    definition = bool(_DEFINITION_INTENT_RE.search(query))
    function_intent = bool(_FUNCTION_INTENT_RE.search(query))
    action_request = bool(_ACTION_REQUEST_RE.search(query))
    score = 0.0

    # Subject first: a perfect procedure for the wrong equipment should not win.
    if subject_terms:
        score += 1.55 * matches + 2.6 * ratio
        score += min(2.4, structural_subject_matches * 0.8)
        score += _subject_cohesion(row, subject_terms)
        if ratio == 0 and (procedure or troubleshoot or attribute):
            score -= 4.0
        elif ratio < 0.5 and len(subject_terms) >= 2:
            score -= 1.0

    if procedure:
        evidence = len(_PROCEDURE_EVIDENCE & set(counter))
        score += min(4.2, evidence * 0.72)
        if evidence >= 3:
            score += 1.2
        if str(row.get("content_type") or "") == "prose" and evidence:
            score += 0.4

    if troubleshoot:
        evidence = len(_TROUBLESHOOT_EVIDENCE & set(counter))
        score += min(7.0, evidence * 1.05)
        if evidence >= 3:
            score += 2.0
        elif evidence == 0 and ratio > 0:
            score -= 2.8
        if re.search(r"\b(?:normal|at\s+limit|fault|faulty|alarm|not\s+working|stopp?ed|operated)\b", raw_text, re.I):
            score += 1.7
        if re.search(r"[-+]?\d+(?:[.,]\d+)?\s*(?:v|vac|vdc|bar|mpa|psi|°?c|°?f|a|ma|hz|rpm)\b", raw_text, re.I):
            score += 1.2
        if action_request:
            action_evidence = len(_PROCEDURE_EVIDENCE & set(counter))
            score += min(5.0, action_evidence * 1.0)
            if action_evidence == 0 and ratio > 0:
                score -= 1.6

    if function_intent:
        function_evidence = len(_FUNCTION_EVIDENCE & set(counter))
        score += min(6.0, function_evidence * 1.0)
        if function_evidence >= 2 and ratio > 0:
            score += 1.5
        if re.search(r"\b(?:affect(?:s|ed)?|causes?|controls?|releases?|blocks?|supplies?|drives?|results?\s+in)\b", raw_text, re.I):
            score += 3.2
        if re.search(r"\b(?:manually|during\s+checking|during\s+adjustment)\b", raw_text, re.I):
            score -= 1.8
        if str(row.get("content_type") or "") == "table" and function_evidence == 0:
            score -= 5.5

    if attribute:
        units = _ATTRIBUTE_UNITS.get(attribute) or set()
        if units and _raw_has_unit(raw_text, units):
            score += 5.2 if ratio > 0 else 2.2
        elif ratio > 0:
            # Subject-only descriptions should not outrank the requested value.
            score -= 2.4

    if definition and subject_terms and ratio >= 0.5:
        score += 0.6
    return score


def _incidental_identifier_penalty(text: str, identifiers: list[str]) -> float:
    if not identifiers:
        return 0.0
    penalty = 0.0
    for line in (text or "").splitlines() or [text or ""]:
        if not any(_identifier_boundary_pattern(identifier).search(line) for identifier in identifiers):
            continue
        if _CONTACT_CONTEXT_RE.search(line):
            penalty -= 15.0
        if re.search(r"\\b(?:date|page|rev(?:ision)?)\\b", line, re.I) and re.search(r"\\d", line):
            penalty -= 1.5
    return penalty




def _identifier_candidates(query: str) -> list[str]:
    """Return likely technical identifiers from a definition-style query.

    Pure numbers are considered identifiers only when the query asks what the
    token is/means/refers to.  Values followed by common engineering units are
    excluded.  This keeps the rule useful for completely unknown manuals.
    """
    if not _DEFINITION_INTENT_RE.search(query or ""):
        return []
    raw = list(_TOKEN_RE.finditer(query or ""))
    out: list[str] = []
    for idx, match in enumerate(raw):
        token = match.group(0).lower()
        if token in _STOPWORDS or len(token) < 2:
            continue
        next_token = raw[idx + 1].group(0).lower() if idx + 1 < len(raw) else ""
        if next_token in _ENGINEERING_UNITS:
            continue
        has_digit = any(ch.isdigit() for ch in token)
        has_letter = any(ch.isalpha() for ch in token)
        structured = any(ch in token for ch in "-_/.:+")
        if structured or (has_digit and has_letter) or (token.isdigit() and 2 <= len(token) <= 8):
            out.append(token)
    return list(dict.fromkeys(out))


def _identifier_boundary_pattern(identifier: str) -> re.Pattern[str]:
    # Separators commonly used inside technical IDs count as identifier
    # characters.  Therefore 2141 does not exactly match 2141-101.
    escaped = re.escape(identifier)
    return re.compile(rf"(?<![A-Za-z0-9_./:+-]){escaped}(?![A-Za-z0-9_./:+-])", re.IGNORECASE)


def _identifier_cell_definition(text: str, identifier: str) -> bool:
    """Detect generic table/key-value lines such as `Hydraulic motor | 2141`."""
    ident = identifier.lower()
    for line in (text or "").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")] if "|" in line else []
        if len(cells) < 2:
            continue
        normalized_cells = [_normalized(cell) for cell in cells]
        if ident not in normalized_cells:
            continue
        other = [cell for cell, norm in zip(cells, normalized_cells) if norm != ident and re.search(r"[A-Za-z]{2}", cell)]
        if other:
            return True
    return False


def extract_cross_references(text: str) -> list[dict[str, str]]:
    """Extract explicit human-readable cross-references from arbitrary manuals."""
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pattern in _REFERENCE_PATTERNS:
        for match in pattern.finditer(text or ""):
            title = re.sub(r"\s+", " ", str(match.groupdict().get("title") or "")).strip(" .,:;-")
            section = str(match.groupdict().get("section") or "").strip(" .,:;")
            if title.lower() in {"instruction", "instruction in", "the instruction"}:
                title = ""
            if not title and not section:
                continue
            key = (title.lower(), section.lower())
            if key in seen:
                continue
            seen.add(key)
            label = title or f"Section {section}"
            if section and title:
                label = f"{title} · section {section}"
            refs.append({"title": title, "section": section, "label": label})
    named_sections = {str(ref.get("section") or "") for ref in refs if ref.get("title") and ref.get("section")}
    if named_sections:
        refs = [ref for ref in refs if ref.get("title") or not any(
            str(named).startswith(str(ref.get("section") or "")) for named in named_sections
        )]
    return refs[:8]


def _reference_match_score(row: dict[str, Any], title: str, section: str = "") -> float:
    text = str(row.get("text") or "")
    headings = [str(value) for value in (row.get("headings") or [])]
    norm_title = _normalized(title)
    score = 0.0
    if norm_title:
        norm_text = _normalized(text)
        norm_headings = [_normalized(value) for value in headings]
        if norm_title in norm_headings:
            score += 12.0
        elif any(norm_title == value or norm_title in value for value in norm_headings):
            score += 9.0
        if norm_title in norm_text:
            score += 5.0
        title_terms = set(_tokens(title))
        if title_terms:
            row_terms = set(_tokens(text + " " + " ".join(headings)))
            score += 4.0 * (len(title_terms & row_terms) / len(title_terms))
    if section:
        section_pat = _identifier_boundary_pattern(section.lower())
        if any(section_pat.search(value) for value in headings):
            score += 2.5
        elif section_pat.search(text):
            score += 0.5
    return score


def follow_reference(
    index_paths: list[Path],
    *,
    title: str,
    section: str = "",
    parent_query: str = "",
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Resolve an explicit reference inside the selected book only.

    The referenced title/section establishes the anchor.  When a parent query
    is supplied, nearby child chunks are reranked with the original intent so
    `procedure`, `value`, and `troubleshooting` questions land on the useful
    subsection rather than only the section title.
    """
    docs: list[dict[str, Any]] = []
    for path in index_paths:
        if path.is_file():
            docs.extend(_load_index(path))
    if not docs:
        return []

    ref_scores = [_reference_match_score(row, title, section) for row in docs]
    max_ref = max(ref_scores, default=0.0)
    anchor_rows = [docs[i] for i, value in enumerate(ref_scores) if value >= max(5.0, max_ref - 2.5)]
    anchor_pages: list[int] = []
    anchor_chunks: list[int] = []
    for row in anchor_rows:
        anchor_chunks.append(int(row.get("chunk_index") or 0))
        for page in row.get("page_numbers") or []:
            try: anchor_pages.append(int(page))
            except (TypeError, ValueError): pass

    scored: list[tuple[float, int]] = []
    for idx, row in enumerate(docs):
        ref_score = ref_scores[idx]
        query_score = _query_feature_score(row, parent_query) if parent_query else 0.0
        proximity = 0.0
        row_pages = []
        for page in row.get("page_numbers") or []:
            try: row_pages.append(int(page))
            except (TypeError, ValueError): pass
        if anchor_pages and row_pages:
            distance = min(abs(a - b) for a in anchor_pages for b in row_pages)
            if distance <= 12:
                proximity = max(proximity, 3.2 * (1.0 - distance / 13.0))
        if anchor_chunks:
            chunk_distance = min(abs(int(row.get("chunk_index") or 0) - value) for value in anchor_chunks)
            if chunk_distance <= 24:
                proximity = max(proximity, 2.2 * (1.0 - chunk_distance / 25.0))

        # An exact reference may stand alone; a child chunk must be near the
        # anchor and relevant to the parent query to enter the result set.
        if ref_score <= 0 and not (parent_query and proximity > 0 and query_score > 0):
            continue
        if parent_query:
            score = (ref_score * 0.35) + (query_score * 1.80) + proximity
        else:
            score = ref_score + proximity
        if str(row.get("content_type") or "") == "prose":
            score += 0.25
        score *= 0.9 + (float(row.get("quality_score") or 100) / 100.0) * 0.1
        if score > 0:
            scored.append((score, idx))

    scored.sort(key=lambda pair: (-pair[0], int(docs[pair[1]].get("chunk_index") or 0)))
    results: list[dict[str, Any]] = []
    q_tokens = _tokens(parent_query or title or section)
    for rank, (score, idx) in enumerate(scored[:max(1, top_k)], start=1):
        row = dict(docs[idx])
        row.pop("_tokens", None); row.pop("_heading_tokens", None); row.pop("_normalized_text", None)
        results.append({
            **row,
            "rank": rank,
            "score": round(score, 4),
            "snippet": _snippet(str(row.get("text") or ""), q_tokens),
            "cross_references": extract_cross_references(str(row.get("text") or "")),
            "reference_scope": "same_book",
        })
    return results



def _docling_ref_item(document: dict[str, Any], ref: str) -> dict[str, Any] | None:
    match = re.fullmatch(r"#/([A-Za-z_]+)/([0-9]+)", str(ref or ""))
    if not match:
        return None
    collection, raw_index = match.groups()
    mapping = {
        "texts": "texts", "tables": "tables", "pictures": "pictures",
        "key_value_items": "key_value_items", "form_items": "form_items",
    }
    key = mapping.get(collection)
    if not key:
        return None
    values = document.get(key) or []
    try:
        value = values[int(raw_index)]
    except (ValueError, IndexError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def docling_highlight_rects(zip_path: Path, refs: list[str], page: int, page_height: float) -> list[fitz.Rect]:
    """Resolve Docling item provenance into PDF top-left rectangles.

    The coordinates are used only for an audit overlay on a rendered copy of
    the original source page. No source document is modified.
    """
    if not refs or not zipfile.is_zipfile(zip_path):
        return []
    with zipfile.ZipFile(zip_path) as archive:
        document, _ = select_docling_document(archive)
    rects: list[fitz.Rect] = []
    for ref in refs[:20]:
        item = _docling_ref_item(document, ref)
        if not item:
            continue
        for prov in item.get("prov") or []:
            if not isinstance(prov, dict) or not isinstance(prov.get("bbox"), dict):
                continue
            try:
                if int(prov.get("page_no")) != int(page):
                    continue
                bbox = prov["bbox"]
                left, right = float(bbox.get("l")), float(bbox.get("r"))
                top, bottom = float(bbox.get("t")), float(bbox.get("b"))
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
    return rects


def _compact_spaces(value: str) -> str:
    return re.sub(r"[ \t]+", " ", (value or "").strip())


def _is_markdown_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return len(cells) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def _table_shape(raw_text: str) -> tuple[bool, bool, bool]:
    """Return table_like, header_only, fragment_like without changing source text."""
    lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
    if not lines:
        return False, False, False
    pipe_lines = sum(1 for line in lines if "|" in line)
    table_like = pipe_lines / len(lines) >= 0.70
    canonical_header = len(lines) >= 2 and "|" in lines[0] and _is_markdown_separator(lines[1])
    data_lines = [line for line in lines[2:] if line.strip()] if canonical_header else []
    header_only = canonical_header and not data_lines
    fragment_like = canonical_header and bool(data_lines) and any("|" not in line for line in data_lines)
    return table_like or canonical_header, header_only, fragment_like


def _content_type(chunk: dict[str, Any]) -> tuple[str, bool]:
    raw = str(chunk.get("raw_text") or "")
    table_like, header_only, fragment_like = _table_shape(raw)
    refs = [str(item) for item in (chunk.get("doc_items") or [])]
    references_table = any(ref.startswith("#/tables/") for ref in refs)
    if header_only:
        return "table_header_only", False
    if fragment_like:
        return "table_fragment", True
    if table_like or references_table:
        return "table", True
    return "prose", False


def _repetitive_window_ratio(text: str, window: int = 8) -> float:
    values = _tokens(text)
    if len(values) < window * 3:
        return 0.0
    grams = Counter(tuple(values[i:i + window]) for i in range(0, len(values) - window + 1))
    if not grams:
        return 0.0
    repeats = max(grams.values())
    if repeats < 3:
        return 0.0
    return min(1.0, repeats * window / max(1, len(values)))


def retrieval_metadata(chunk: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    text = str(chunk.get("text") or chunk.get("raw_text") or "").strip()
    raw = str(chunk.get("raw_text") or text).strip()
    token_list = _tokens(text)
    content_type, table_related = _content_type(chunk)
    warnings: list[str] = []
    try:
        num_tokens = int(chunk.get("num_tokens") or 0)
    except (TypeError, ValueError):
        num_tokens = 0
    pages = []
    for value in chunk.get("page_numbers") or []:
        try:
            pages.append(int(value))
        except (TypeError, ValueError):
            pass
    if num_tokens > max_tokens:
        warnings.append("OVERSIZED_CHUNK")
    if chunk.get("num_tokens_estimated"):
        warnings.append("TOKEN_COUNT_ESTIMATED")
    if not pages:
        warnings.append("PAGE_UNKNOWN")
    if not chunk.get("doc_items"):
        warnings.append("NO_DOCLING_ITEM_REFERENCE")
    if content_type == "table_fragment":
        warnings.append("TABLE_FRAGMENT")
    if content_type == "table_header_only":
        warnings.append("TABLE_HEADER_ONLY")
    if len(token_list) < 2:
        warnings.append("TOO_LITTLE_SEARCHABLE_TEXT")
    repetition_ratio = _repetitive_window_ratio(text)
    if repetition_ratio >= 0.40:
        warnings.append("REPETITIVE_OCR_BLOCK")

    eligible = (
        bool(text)
        and len(token_list) >= 2
        and content_type != "table_header_only"
        and repetition_ratio < 0.40
    )
    quality = 100
    quality -= 20 if "OVERSIZED_CHUNK" in warnings else 0
    quality -= 12 if "TABLE_FRAGMENT" in warnings else 0
    quality -= 10 if "PAGE_UNKNOWN" in warnings else 0
    quality -= 8 if "NO_DOCLING_ITEM_REFERENCE" in warnings else 0
    quality -= 35 if "TOO_LITTLE_SEARCHABLE_TEXT" in warnings else 0
    quality -= 35 if "REPETITIVE_OCR_BLOCK" in warnings else 0
    if not eligible:
        quality = min(quality, 45)

    signature = hashlib.sha256(_normalized(text).encode("utf-8")).hexdigest() if text else ""
    return {
        "eligible": eligible,
        "content_type": content_type,
        "table_related": table_related,
        "quality_score": max(0, quality),
        "warnings": warnings,
        "searchable_tokens": len(token_list),
        "signature": signature,
        "repetition_ratio": round(repetition_ratio, 3),
    }


def annotate_retrieval_rows(
    chunks: list[dict[str, Any]],
    *,
    postprocess_job_id: int | None = None,
    source_filename: str | None = None,
    result_dir_name: str | None = None,
    max_tokens: int = 256,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Annotate chunks and build a compact searchable index.

    This never edits raw Docling content. It only annotates derived Stage 3 rows.
    """
    output: list[dict[str, Any]] = []
    index_rows: list[dict[str, Any]] = []
    content_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    quality_scores: list[int] = []
    excluded = 0
    oversized = 0

    for row in chunks:
        chunk = dict(row)
        meta = retrieval_metadata(chunk, max_tokens)
        content_counts[meta["content_type"]] += 1
        quality_scores.append(int(meta["quality_score"]))
        for warning in meta["warnings"]:
            warning_counts[warning] += 1
        if "OVERSIZED_CHUNK" in meta["warnings"]:
            oversized += 1
        if not meta["eligible"]:
            excluded += 1
        chunk["retrieval"] = meta
        if postprocess_job_id is not None:
            chunk["postprocess_job_id"] = int(postprocess_job_id)
        if source_filename:
            chunk["source_filename"] = source_filename
        output.append(chunk)

        if not meta["eligible"]:
            continue
        text = str(chunk.get("text") or chunk.get("raw_text") or "").strip()
        headings = [str(v).strip() for v in (chunk.get("headings") or []) if str(v).strip()]
        index_rows.append({
            "schema": _SCHEMA_INDEX,
            "postprocess_job_id": int(postprocess_job_id) if postprocess_job_id is not None else chunk.get("postprocess_job_id"),
            "result_dir": result_dir_name,
            "source_filename": source_filename or chunk.get("source_filename"),
            "chunk_id": chunk.get("chunk_id"),
            "chunk_index": chunk.get("chunk_index"),
            "text": text,
            "headings": headings,
            "page_numbers": chunk.get("page_numbers") or [],
            "doc_items": chunk.get("doc_items") or [],
            "num_tokens": chunk.get("num_tokens"),
            "num_tokens_estimated": bool(chunk.get("num_tokens_estimated")),
            "content_type": meta["content_type"],
            "quality_score": meta["quality_score"],
            "warnings": meta["warnings"],
            "stage2c_correction_count": len(((chunk.get("stage2c") or {}).get("text_corrections") or [])),
            "vision_enrichment_count": len(((chunk.get("stage2c") or {}).get("vision_enrichment") or [])),
        })

    summary = {
        "schema": _SCHEMA_QUALITY,
        "generated_at_epoch": time.time(),
        "postprocess_job_id": postprocess_job_id,
        "source_filename": source_filename,
        "result_dir": result_dir_name,
        "total_chunks": len(output),
        "searchable_chunks": len(index_rows),
        "excluded_chunks": excluded,
        "oversized_searchable_chunks": sum(1 for row in index_rows if "OVERSIZED_CHUNK" in (row.get("warnings") or [])),
        "oversized_all_chunks": oversized,
        "mean_quality_score": round(sum(quality_scores) / len(quality_scores), 2) if quality_scores else 0.0,
        "content_types": dict(sorted(content_counts.items())),
        "warnings": dict(sorted(warning_counts.items())),
        "raw_docling_immutable": True,
    }
    return output, index_rows, summary


def _write_jsonl_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def write_retrieval_artifacts(result_dir: Path, chunks: list[dict[str, Any]], *, max_tokens: int = 256) -> dict[str, Any]:
    result_dir = Path(result_dir)
    manifest: dict[str, Any] = {}
    try:
        manifest = json.loads((result_dir / "source_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    postprocess_job_id = None
    name = result_dir.name
    match = re.search(r"__job(\d+)", name)
    if match:
        postprocess_job_id = int(match.group(1))
    source_filename = str(manifest.get("source_filename") or name)
    annotated, index_rows, summary = annotate_retrieval_rows(
        chunks,
        postprocess_job_id=postprocess_job_id,
        source_filename=source_filename,
        result_dir_name=name,
        max_tokens=max_tokens,
    )
    _write_jsonl_atomic(result_dir / "chunks.jsonl", annotated)
    _write_jsonl_atomic(result_dir / "retrieval_index.jsonl", index_rows)
    tmp = result_dir / "retrieval_quality.json.tmp"
    tmp.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(result_dir / "retrieval_quality.json")
    _load_index_cached.cache_clear()
    return summary


def refresh_retrieval_artifacts(result_dir: Path, *, max_tokens: int = 256) -> dict[str, Any]:
    chunks_path = Path(result_dir) / "chunks.jsonl"
    if not chunks_path.is_file():
        raise FileNotFoundError("Stage 3 chunks.jsonl is missing")
    chunks: list[dict[str, Any]] = []
    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            chunks.append(value)
    return write_retrieval_artifacts(Path(result_dir), chunks, max_tokens=max_tokens)


@lru_cache(maxsize=128)
def _load_index_cached(path: str, mtime_ns: int, size: int) -> tuple[dict[str, Any], ...]:
    del mtime_ns, size
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                value = dict(value)
                value["_tokens"] = _tokens(str(value.get("text") or ""))
                value["_heading_tokens"] = set(_tokens(" ".join(value.get("headings") or [])))
                value["_normalized_text"] = _normalized(str(value.get("text") or ""))
                rows.append(value)
    return tuple(rows)


def _load_index(path: Path) -> list[dict[str, Any]]:
    stat = path.stat()
    return [dict(row) for row in _load_index_cached(str(path), stat.st_mtime_ns, stat.st_size)]


def _snippet(text: str, query_tokens: list[str], limit: int = 560) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) <= limit:
        return value
    lower = value.lower()
    positions = [lower.find(token) for token in query_tokens if lower.find(token) >= 0]
    start = max(0, (min(positions) if positions else 0) - 120)
    end = min(len(value), start + limit)
    prefix = "…" if start else ""
    suffix = "…" if end < len(value) else ""
    return prefix + value[start:end].strip() + suffix


def _adjacent_context(docs: list[dict[str, Any]], idx: int, *, limit: int = 2) -> list[dict[str, Any]]:
    row = docs[idx]
    try:
        center = int(row.get("chunk_index"))
    except (TypeError, ValueError):
        return []
    job_id = row.get("postprocess_job_id")
    source = row.get("source_filename")
    candidates: list[tuple[int, dict[str, Any]]] = []
    for other in docs:
        if other is row:
            continue
        if job_id is not None and other.get("postprocess_job_id") != job_id:
            continue
        if source and other.get("source_filename") != source:
            continue
        try:
            distance = abs(int(other.get("chunk_index")) - center)
        except (TypeError, ValueError):
            continue
        if distance not in {1, 2}:
            continue
        copy = dict(other)
        copy.pop("_tokens", None); copy.pop("_heading_tokens", None); copy.pop("_normalized_text", None)
        candidates.append((distance, copy))
    candidates.sort(key=lambda pair: (pair[0], int(pair[1].get("chunk_index") or 0)))
    return [
        {
            "chunk_id": value.get("chunk_id"),
            "chunk_index": value.get("chunk_index"),
            "page_numbers": value.get("page_numbers") or [],
            "doc_items": value.get("doc_items") or [],
            "text": value.get("text") or "",
            "content_type": value.get("content_type"),
        }
        for _, value in candidates[:limit]
    ]


def search_indices(index_paths: list[Path], query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
    q_tokens = _tokens(query)
    if not q_tokens:
        return []
    docs: list[dict[str, Any]] = []
    for path in index_paths:
        if path.is_file():
            docs.extend(_load_index(path))
    if not docs:
        return []

    q_counts = Counter(q_tokens)
    query_terms = list(q_counts)
    counters: list[Counter[str]] = [Counter(row.get("_tokens") or []) for row in docs]
    lengths = [max(1, sum(counter.values())) for counter in counters]
    avgdl = sum(lengths) / len(lengths)
    df = {term: sum(1 for counter in counters if term in counter) for term in query_terms}
    n_docs = len(docs)
    q_phrase = _normalized(query)
    technical = [term for term in query_terms if any(ch.isdigit() for ch in term) or any(ch in term for ch in "/._+-:")]
    identifiers = _identifier_candidates(query)
    definition_intent = bool(_DEFINITION_INTENT_RE.search(query or ""))
    procedure_intent = bool(_PROCEDURE_INTENT_RE.search(query or ""))

    scored: list[tuple[float, int]] = []
    k1, b = 1.4, 0.75
    for idx, (row, counter, dl) in enumerate(zip(docs, counters, lengths)):
        score = 0.0
        heading_tokens = row.get("_heading_tokens") or set()
        for term in query_terms:
            tf = counter.get(term, 0)
            if not tf:
                continue
            freq = max(1, df.get(term, 0))
            idf = math.log(1.0 + (n_docs - freq + 0.5) / (freq + 0.5))
            denom = tf + k1 * (1 - b + b * dl / max(1.0, avgdl))
            score += idf * (tf * (k1 + 1) / denom) * (1 + 0.15 * (q_counts[term] - 1))
            if term in heading_tokens:
                score += idf * 0.35
        normalized_text = str(row.get("_normalized_text") or "")
        if len(q_tokens) >= 2 and q_phrase and q_phrase in normalized_text:
            score += 3.0
        if technical:
            found = sum(term in counter for term in technical)
            score += found * 0.9
            if found == len(technical):
                score += 1.2
        raw_text = str(row.get("text") or "")
        if identifiers:
            for identifier in identifiers:
                exact = bool(_identifier_boundary_pattern(identifier).search(raw_text))
                cell_definition = _identifier_cell_definition(raw_text, identifier)
                structured_occurrences = [token for token in _tokens(raw_text) if identifier in token and token != identifier]
                if exact:
                    score += 4.2
                    if definition_intent:
                        score += 1.6
                if cell_definition:
                    score += 4.5
                if not exact and structured_occurrences:
                    score -= min(2.8, 0.9 + 0.45 * len(structured_occurrences))
        score += _query_feature_score(row, query)
        score += _incidental_identifier_penalty(raw_text, identifiers)
        score *= 0.85 + (float(row.get("quality_score") or 100) / 100.0) * 0.15
        if score > 0:
            scored.append((score, idx))

    scored.sort(key=lambda item: (-item[0], str(docs[item[1]].get("source_filename") or ""), int(docs[item[1]].get("chunk_index") or 0)))
    results: list[dict[str, Any]] = []
    for rank, (score, idx) in enumerate(scored[: max(1, top_k)], start=1):
        row = dict(docs[idx])
        row.pop("_tokens", None); row.pop("_heading_tokens", None); row.pop("_normalized_text", None)
        results.append({
            **row,
            "rank": rank,
            "score": round(score, 4),
            "snippet": _snippet(str(row.get("text") or ""), q_tokens),
            "cross_references": extract_cross_references(str(row.get("text") or "")),
            "context_neighbors": _adjacent_context(docs, idx) if (procedure_intent or bool(_TROUBLESHOOT_INTENT_RE.search(query or ""))) else [],
        })
    return results


def benchmark_path(processed_dir: Path) -> Path:
    return Path(processed_dir) / "retrieval_benchmark.json"


def benchmark_result_path(processed_dir: Path) -> Path:
    return Path(processed_dir) / "retrieval_benchmark_result.json"


def load_benchmark_result(processed_dir: Path) -> dict[str, Any] | None:
    path = benchmark_result_path(processed_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_benchmark(processed_dir: Path) -> dict[str, Any]:
    path = benchmark_path(processed_dir)
    if not path.is_file():
        return {"schema": _SCHEMA_BENCHMARK, "items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"schema": _SCHEMA_BENCHMARK, "items": []}
    if not isinstance(payload, dict):
        return {"schema": _SCHEMA_BENCHMARK, "items": []}
    payload.setdefault("schema", _SCHEMA_BENCHMARK)
    payload.setdefault("items", [])
    return payload


def save_benchmark(processed_dir: Path, payload: dict[str, Any]) -> None:
    path = benchmark_path(processed_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "schema": _SCHEMA_BENCHMARK, "updated_at_epoch": time.time()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _expected_source_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "postprocess_job_id": result.get("postprocess_job_id"),
        "source_filename": result.get("source_filename"),
        "chunk_id": result.get("chunk_id"),
        "doc_items": result.get("doc_items") or [],
        "pages": result.get("page_numbers") or [],
    }


def _benchmark_expected_sources(item: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [dict(value) for value in (item.get("acceptable_sources") or []) if isinstance(value, dict)]
    legacy = {
        "postprocess_job_id": item.get("expected_postprocess_job_id"),
        "source_filename": item.get("expected_source_filename"),
        "chunk_id": item.get("expected_chunk_id"),
        "doc_items": item.get("expected_doc_items") or [],
        "pages": item.get("expected_pages") or [],
    }
    if any(legacy.values()) and not any(
        src.get("postprocess_job_id") == legacy.get("postprocess_job_id")
        and set(src.get("doc_items") or []) == set(legacy.get("doc_items") or [])
        and set(src.get("pages") or []) == set(legacy.get("pages") or [])
        for src in sources
    ):
        sources.insert(0, legacy)
    return sources


def _same_expected_source(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("postprocess_job_id") is not None and b.get("postprocess_job_id") is not None:
        if int(a.get("postprocess_job_id")) != int(b.get("postprocess_job_id")):
            return False
    a_refs, b_refs = set(a.get("doc_items") or []), set(b.get("doc_items") or [])
    if a_refs and b_refs:
        return bool(a_refs & b_refs)
    a_pages, b_pages = set(a.get("pages") or []), set(b.get("pages") or [])
    if a_pages and b_pages:
        return bool(a_pages & b_pages)
    return bool(a.get("chunk_id") and a.get("chunk_id") == b.get("chunk_id"))


def add_benchmark_item(
    processed_dir: Path,
    *,
    query: str,
    result: dict[str, Any],
    note: str = "",
) -> dict[str, Any]:
    payload = load_benchmark(processed_dir)
    normalized_query = _normalized(query)
    source = _expected_source_from_result(result)
    for item in payload.get("items") or []:
        if _normalized(str(item.get("query") or "")) != normalized_query:
            continue
        sources = _benchmark_expected_sources(item)
        if not any(_same_expected_source(existing, source) for existing in sources):
            sources.append(source)
        item["acceptable_sources"] = sources
        item["note"] = note.strip() or str(item.get("note") or "")
        save_benchmark(processed_dir, payload)
        return item

    item = {
        "id": f"RB-{uuid.uuid4().hex[:10]}",
        "query": query.strip(),
        # Legacy fields remain for old UI/data compatibility.
        "expected_postprocess_job_id": result.get("postprocess_job_id"),
        "expected_source_filename": result.get("source_filename"),
        "expected_chunk_id": result.get("chunk_id"),
        "expected_doc_items": result.get("doc_items") or [],
        "expected_pages": result.get("page_numbers") or [],
        "acceptable_sources": [source],
        "note": note.strip(),
        "created_at_epoch": time.time(),
    }
    payload["items"].append(item)
    save_benchmark(processed_dir, payload)
    return item

def delete_benchmark_item(processed_dir: Path, item_id: str) -> bool:
    payload = load_benchmark(processed_dir)
    before = len(payload.get("items") or [])
    payload["items"] = [row for row in (payload.get("items") or []) if str(row.get("id")) != str(item_id)]
    if len(payload["items"]) == before:
        return False
    save_benchmark(processed_dir, payload)
    return True


def _result_matches_source(result: dict[str, Any], source: dict[str, Any]) -> bool:
    expected_job = source.get("postprocess_job_id")
    if expected_job is not None and result.get("postprocess_job_id") is not None:
        try:
            if int(result.get("postprocess_job_id")) != int(expected_job):
                return False
        except (TypeError, ValueError):
            return False
    expected_refs = {str(v) for v in (source.get("doc_items") or [])}
    result_refs = {str(v) for v in (result.get("doc_items") or [])}
    if expected_refs:
        return bool(expected_refs & result_refs)
    expected_pages = {int(v) for v in (source.get("pages") or []) if isinstance(v, (int, float)) or str(v).isdigit()}
    result_pages = {int(v) for v in (result.get("page_numbers") or []) if isinstance(v, (int, float)) or str(v).isdigit()}
    return bool(expected_pages & result_pages) if expected_pages else result.get("chunk_id") == source.get("chunk_id")


def _matches_expected(result: dict[str, Any], item: dict[str, Any]) -> bool:
    return any(_result_matches_source(result, source) for source in _benchmark_expected_sources(item))


def run_benchmark(processed_dir: Path, index_paths: list[Path], *, top_k: int = 5) -> dict[str, Any]:
    payload = load_benchmark(processed_dir)
    items = list(payload.get("items") or [])
    details: list[dict[str, Any]] = []
    ranks: list[int | None] = []
    for item in items:
        results = search_indices(index_paths, str(item.get("query") or ""), top_k=top_k)
        rank = next((int(row["rank"]) for row in results if _matches_expected(row, item)), None)
        ranks.append(rank)
        details.append({
            "id": item.get("id"),
            "query": item.get("query"),
            "rank": rank,
            "hit": rank is not None,
            "top_result": results[0] if results else None,
        })
    total = len(items)
    def rate(limit: int) -> float:
        if not total:
            return 0.0
        return round(100.0 * sum(rank is not None and rank <= limit for rank in ranks) / total, 1)
    mrr = round(sum((1.0 / rank) if rank else 0.0 for rank in ranks) / total, 4) if total else 0.0
    result = {
        "schema": "docling-retrieval-benchmark-result/v1",
        "cases": total,
        "top1_percent": rate(1),
        "top3_percent": rate(3),
        "top5_percent": rate(5),
        "mrr": mrr,
        "details": details,
        "generated_at_epoch": time.time(),
    }
    path = benchmark_result_path(processed_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return result
