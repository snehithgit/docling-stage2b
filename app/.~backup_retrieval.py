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
_SCHEMA_BENCHMARK = "docling-retrieval-benchmark/v3"
RETRIEVAL_RULE_VERSION = "retrieval-source-integrity-v5"

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
_VALUE_INTENT_RE = re.compile(r"\b(?:value|rating|rated|capacity|setting|setpoint|limit|range|maximum|minimum|max|min|swl|wll|safe\s+working\s+load|working\s+load\s+limit|interval|period|clearance|diameter|dimension|wear\s+limit|thickness|length|width|service\s+life)\b", re.IGNORECASE)
_LOAD_QUERY_RE = re.compile(r"\b(?:swl|wll|safe\s+working\s+load|working\s+load\s+limit|hoisting\s+capacity|lifting\s+capacity)\b", re.IGNORECASE)
_LOAD_EVIDENCE_RE = re.compile(r"\b(?:swl|wll|safe\s+working\s+load|working\s+load\s+limit|hoisting\s+capacity|lifting\s+capacity)\b", re.IGNORECASE)
_LOAD_UNIT_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?\s*(?:metric\s+)?(?:t|ton|tons|tonne|tonnes|kg|kn)\b", re.IGNORECASE)
_DIRECT_LOAD_VALUE_RE = re.compile(
    r"\b(?:swl|wll|safe\s+working\s+load|working\s+load\s+limit|hoisting\s+capacity|lifting\s+capacity)\b\s*[:=]?\s*[-+]?\d+(?:[.,]\d+)?\s*(?:metric\s+)?(?:t|ton|tons|tonne|tonnes|kg|kn)\b",
    re.IGNORECASE,
)

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
_CROSS_REFERENCE_QUERY_RE = re.compile(r"\b(?:refer|reference|see|section|chapter|clause|where\s+(?:is|are|can)|which\s+(?:section|chapter|drawing|table))\b", re.IGNORECASE)
_PART_ITEM_QUERY_RE = re.compile(r"\b(?:item|pos(?:ition)?)\s*#?\s*(\d{2,4})\b", re.IGNORECASE)
_OPERATING_DURATION_QUERY_RE = re.compile(r"\b(?:operating|working|service|run[- ]?in|break[- ]?in|hours?|hrs?)\b", re.IGNORECASE)
_OPERATING_DURATION_EVIDENCE_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:hours?|hrs?)\b", re.IGNORECASE)
_SECTION_QUERY_RE = re.compile(r"\b(?:section|chapter|clause)\s+([A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*)", re.IGNORECASE)
_QUOTED_QUERY_RE = re.compile(r"[\"“”']([^\"“”']{3,100})[\"“”']")
_MAINTENANCE_INTERVAL_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?\s*(?:h|hr|hrs|hour|hours|day|days|week|weeks|month|months|year|years)\b", re.IGNORECASE)

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

    value_intent = bool(_VALUE_INTENT_RE.search(query))
    if value_intent:
        table_evidence = bool(row.get("table_related")) or str(row.get("content_type") or "").startswith("table")
        has_numeric = bool(re.search(r"[-+]?\d+(?:[.,]\d+)?", raw_text))
        if table_evidence and has_numeric and matches > 0:
            score += 2.0
        if row.get("stitched_table") and table_evidence and has_numeric:
            score += 2.5
        if _LOAD_QUERY_RE.search(query):
            direct_load_value = bool(_DIRECT_LOAD_VALUE_RE.search(raw_text))
            explicit_value_request = bool(re.search(r"\b(?:value|capacity|rating|rated)\b", query, re.I))
            if direct_load_value:
                # A direct SWL/WLL/capacity value with an engineering load unit
                # is stronger evidence for an explicit value request than a
                # definition or proof-load formula that merely mentions WLL.
                score += 20.0
            elif _LOAD_EVIDENCE_RE.search(raw_text) and _LOAD_UNIT_RE.search(raw_text):
                score += 3.5
                if explicit_value_request and not row.get("stitched_table"):
                    score -= 3.0
            if row.get("stitched_table") and re.search(r"\b(?:hoisting|lifting)\s+capacity\b", raw_text, re.I):
                score += 12.0 if explicit_value_request else 5.0

    if _CROSS_REFERENCE_QUERY_RE.search(query or ""):
        headings_text = " ".join(str(value) for value in (row.get("headings") or []))
        for section_match in _SECTION_QUERY_RE.finditer(query or ""):
            section = section_match.group(1)
            if _identifier_boundary_pattern(section.lower()).search(headings_text):
                # Section numbers are weak anchors by themselves because manuals
                # often reuse numbering in appendices/submanuals. Keep them a
                # tie-breaker, not a dominant ranking signal.
                score += 2.5
            elif _identifier_boundary_pattern(section.lower()).search(raw_text):
                score += 0.5
        for quoted in _QUOTED_QUERY_RE.findall(query or ""):
            norm = _normalized(quoted)
            if norm and norm in _normalized(headings_text):
                score += 8.0
            elif norm and norm in _normalized(raw_text):
                score += 3.0
        # A source row containing an explicit see/refer instruction is useful
        # evidence for a cross-reference question, but it must not outweigh a
        # direct heading/section match.
        if re.search(r"\b(?:see|refer\s+to)\b", raw_text, re.I) and ratio > 0:
            score += 1.4

    if value_intent and re.search(r"\b(?:interval|period|maintenance|service)\b", query, re.I):
        if _MAINTENANCE_INTERVAL_RE.search(raw_text) and matches > 0:
            score += 5.0

    # Parts-list item numbers are often plain numeric values (e.g. item 033),
    # so they are intentionally excluded from the global structured-ID guard.
    # When the query explicitly labels a number as an item/position, however,
    # it is a strong local identifier and should bind to that row.
    part_item = _PART_ITEM_QUERY_RE.search(query or "")
    if part_item:
        item_no = part_item.group(1)
        matching_lines = [
            line for line in (raw_text.splitlines() or [raw_text])
            if re.search(rf"(?<!\d){re.escape(item_no)}(?!\d)", line)
        ]
        if matching_lines:
            score += 12.0
            if re.search(r"\b(?:article\s*(?:no|number)|parts?\s+manual|description)\b", raw_text, re.I):
                score += 4.0
            # Bind the item number to its requested description, not to every
            # unrelated parts table that happens to reuse position 033. Split
            # hyphenated natural-language terms (emergency-stop -> emergency,
            # stop) only for this local description check; structured IDs keep
            # their normal token semantics elsewhere.
            generic = {"what","which","article","number","item","position","pos","parts","part","list","this","the","for","is"}
            q_terms = {t for t in _tokens((query or "").replace("-", " ")) if t not in generic and not t.isdigit()}
            best_overlap = 0
            for line in matching_lines:
                line_terms = set(_tokens(line.replace("-", " ")))
                best_overlap = max(best_overlap, len(q_terms & line_terms))
            if best_overlap >= 2:
                score += 14.0
            elif best_overlap == 1:
                score += 4.0

    # Commissioning/run-in questions commonly ask for a paired limit and
    # duration. Prefer evidence that actually contains the duration/value over
    # generic descriptions of the same motor/system.
    if value_intent and _OPERATING_DURATION_QUERY_RE.search(query or ""):
        if _OPERATING_DURATION_EVIDENCE_RE.search(raw_text):
            score += 5.0
        if re.search(r"\b(?:run[- ]?in|breaking[- ]?in|starting\s+up)\b", query, re.I) and re.search(r"\b(?:run[- ]?in|starting\s+up)\b", raw_text, re.I):
            score += 3.5
        if re.search(r"\bpower\b", query, re.I) and re.search(r"\bpower\b", raw_text, re.I) and re.search(r"\d+(?:[.,]\d+)?\s*%", raw_text):
            score += 5.0

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


_PARTS_LIST_HEADER_RE = re.compile(
    r"\bitem\b.{0,80}\b(?:qty|quantity)\b.{0,100}\b(?:article(?:\s+no)?|part(?:\s+no|\s+number)?)\b.{0,120}\bdescription\b",
    re.IGNORECASE | re.DOTALL,
)
_TABLE_REF_RE = re.compile(r"^#/tables/(\d+)$")
_NUMERIC_TABLE_TOKEN_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?(?:\s*[%°])?(?![A-Za-z])")
_ALPHA_LABEL_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z-]{1,}\b")


def _looks_like_space_delimited_table(raw_text: str) -> bool:
    """Conservatively identify flattened parts/specification lists without pipes.

    Docling occasionally serializes parts lists as one prose-looking line even
    though the source is tabular.  Requiring the generic item/quantity/part/
    description header sequence avoids promoting ordinary prose to a table.
    """
    raw = _compact_spaces(raw_text or "")
    if not raw or len(raw) < 24:
        return False
    return bool(_PARTS_LIST_HEADER_RE.search(raw[:500]))


def _table_label_density(raw_text: str) -> tuple[int, int, float]:
    """Return alphabetic labels, numeric values and label/(label+numeric).

    This intentionally inspects raw table body text only.  Stage 3 ``text`` may
    prepend alphabetic headings, which would hide a label-less numeric row.
    """
    raw = str(raw_text or "")
    alpha = len(_ALPHA_LABEL_TOKEN_RE.findall(raw))
    numeric = len(_NUMERIC_TABLE_TOKEN_RE.findall(raw))
    total = alpha + numeric
    return alpha, numeric, (alpha / total if total else 1.0)


def _table_data_without_header(raw_text: str) -> bool:
    alpha, numeric, density = _table_label_density(raw_text)
    return numeric >= 4 and alpha <= 2 and density < 0.20


def _table_refs(chunk: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for value in chunk.get("doc_items") or []:
        ref = str(value)
        if _TABLE_REF_RE.match(ref) and ref not in refs:
            refs.append(ref)
    return refs


def _table_header_cells(raw_text: str) -> list[str]:
    best: list[str] = []
    best_score = -1
    for line in str(raw_text or "").splitlines():
        stripped = line.strip()
        if "|" not in stripped or _is_markdown_separator(stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        meaningful = [cell for cell in cells if cell]
        if len(meaningful) < 2:
            continue
        alpha = sum(len(_ALPHA_LABEL_TOKEN_RE.findall(cell)) for cell in meaningful)
        numeric = sum(len(_NUMERIC_TABLE_TOKEN_RE.findall(cell)) for cell in meaningful)
        score = alpha * 10 + len(meaningful) - numeric
        if alpha >= 2 and score > best_score:
            best = meaningful
            best_score = score
    return best


def _clean_table_fragment(raw_text: str, header_cells: list[str]) -> str:
    header_norm = _compact_spaces(" | ".join(header_cells)).lower() if header_cells else ""
    kept: list[str] = []
    for line in str(raw_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _is_markdown_separator(stripped):
            continue
        compact = _compact_spaces(stripped.strip("|"))
        if not compact:
            continue
        # Drop separator shards such as ``-------|`` split by HybridChunker.
        if re.fullmatch(r"[-:| ]{3,}", stripped):
            continue
        if header_norm and _compact_spaces(stripped.strip("|")).lower() == header_norm:
            continue
        kept.append(stripped)
    return "\n".join(kept).strip()


def _stable_table_evidence_id(table_ref: str, page: int, window: int) -> str:
    match = _TABLE_REF_RE.match(table_ref)
    table_no = int(match.group(1)) if match else 0
    return f"TBL-{table_no:06d}-P{int(page):04d}-{int(window):03d}"


def _build_stitched_table_evidence(
    chunks: list[dict[str, Any]],
    *,
    postprocess_job_id: int | None,
    source_filename: str | None,
    result_dir_name: str | None,
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Build derived retrieval evidence for fragmented Docling table chunks.

    Canonical Stage 3 chunks are never modified or removed.  Only contiguous chunks from the same Docling table reference are considered.
    Continuations may cross one adjacent page boundary; reconstructed evidence
    repeats table labels and records every source chunk/page.
    """
    candidates: list[tuple[int, int, str, dict[str, Any]]] = []
    for chunk in chunks:
        try:
            idx = int(chunk.get("chunk_index") or 0)
        except (TypeError, ValueError):
            idx = 0
        pages: list[int] = []
        for value in chunk.get("page_numbers") or []:
            try:
                pages.append(int(value))
            except (TypeError, ValueError):
                continue
        if not pages:
            continue
        for ref in _table_refs(chunk):
            # Keep a table/page pair deterministic; most chunks have one page.
            for page in sorted(set(pages)):
                candidates.append((idx, page, ref, chunk))
    candidates.sort(key=lambda item: (item[2], item[1], item[0]))

    groups: list[list[tuple[int, int, str, dict[str, Any]]]] = []
    current: list[tuple[int, int, str, dict[str, Any]]] = []
    for item in candidates:
        idx, page, ref, _chunk = item
        if current:
            pidx, ppage, pref, _ = current[-1]
            # Docling may keep one table ref across a page break.  Permit only
            # same-page or immediately-next-page continuation, and only when
            # Stage-3 chunk order is still contiguous.  This avoids merging
            # distant/repeated tables that happen to share a structural label.
            page_step_ok = page == ppage or page == ppage + 1
            if ref != pref or not page_step_ok or idx != pidx + 1:
                if len(current) >= 2:
                    groups.append(current)
                current = []
        current.append(item)
    if len(current) >= 2:
        groups.append(current)

    output: list[dict[str, Any]] = []
    for group in groups:
        _first_idx, first_page, table_ref, _ = group[0]
        group_chunks = [item[3] for item in group]
        group_pages = sorted({int(item[1]) for item in group})
        metas = [chunk.get("retrieval") or retrieval_metadata(chunk, max_tokens) for chunk in group_chunks]
        fragmented = (
            any(meta.get("content_type") in {"table_header_only", "table_fragment"} for meta in metas)
            or any("TABLE_DATA_WITHOUT_HEADER" in (meta.get("warnings") or []) for meta in metas)
            or any(str(chunk.get("raw_text") or "").strip().startswith(("---", "|---")) for chunk in group_chunks)
        )
        # Reconstruct only when the current Stage 3 evidence actually shows
        # fragmentation.  Healthy multi-chunk tables stay canonical so derived
        # rows do not crowd or demote already-good benchmark sources.
        if not fragmented:
            continue

        header_cells: list[str] = []
        for chunk in group_chunks:
            cells = _table_header_cells(str(chunk.get("raw_text") or ""))
            if len(cells) > len(header_cells):
                header_cells = cells
        if not header_cells:
            continue

        headings: list[str] = []
        for chunk in group_chunks:
            for heading in chunk.get("headings") or []:
                value = str(heading).strip()
                if value and value not in headings:
                    headings.append(value)
        base_lines = []
        if headings:
            base_lines.append("Section: " + " > ".join(headings[-4:]))
        base_lines.append("Table columns: " + " | ".join(header_cells))
        base = "\n".join(base_lines).strip()

        pieces: list[tuple[str, str]] = []
        for chunk in group_chunks:
            cleaned = _clean_table_fragment(str(chunk.get("raw_text") or ""), header_cells)
            if cleaned:
                pieces.append((str(chunk.get("chunk_id") or ""), cleaned))
        if not pieces:
            continue

        windows: list[list[tuple[str, str]]] = []
        window: list[tuple[str, str]] = []
        for piece in pieces:
            trial = window + [piece]
            trial_text = base + "\n" + "\n".join(value for _cid, value in trial)
            if window and len(_tokens(trial_text)) > max(64, int(max_tokens)):
                windows.append(window)
                window = [piece]
            else:
                window = trial
        if window:
            windows.append(window)

        for window_no, window_pieces in enumerate(windows, start=1):
            source_ids = [cid for cid, _value in window_pieces if cid]
            evidence_text = base + "\n" + "\n".join(value for _cid, value in window_pieces)
            evidence_text = evidence_text.strip()
            if len(_tokens(evidence_text)) < 2:
                continue
            output.append({
                "schema": _SCHEMA_INDEX,
                "postprocess_job_id": int(postprocess_job_id) if postprocess_job_id is not None else group_chunks[0].get("postprocess_job_id"),
                "result_dir": result_dir_name,
                "source_filename": source_filename or group_chunks[0].get("source_filename"),
                "chunk_id": _stable_table_evidence_id(table_ref, first_page, window_no),
                "chunk_index": int(group_chunks[0].get("chunk_index") or 0),
                "text": evidence_text,
                "headings": headings,
                "page_numbers": group_pages,
                "doc_items": [table_ref],
                "num_tokens": len(_tokens(evidence_text)),
                "num_tokens_estimated": True,
                "content_type": "table_reconstructed",
                "table_related": True,
                "quality_score": 100,
                "warnings": ["TABLE_RECONSTRUCTED"],
                "retrieval_evidence_type": "stitched_table",
                "stitched_table": True,
                "table_ref": table_ref,
                "source_chunk_ids": source_ids,
                "table_group_chunk_ids": [str(chunk.get("chunk_id") or "") for chunk in group_chunks if str(chunk.get("chunk_id") or "")],
                "source_chunk_count": len(source_ids),
                "cross_page_table": len(group_pages) > 1,
                "raw_docling_immutable": True,
                "stage2c_correction_count": sum(len(((chunk.get("stage2c") or {}).get("text_corrections") or [])) for chunk in group_chunks),
                "vision_enrichment_count": sum(len(((chunk.get("stage2c") or {}).get("vision_enrichment") or [])) for chunk in group_chunks),
            })
    return output


def _table_shape(raw_text: str) -> tuple[bool, bool, bool]:
    """Return table_like, header_only, fragment_like without changing source text."""
    lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
    if not lines:
        return False, False, False
    pipe_lines = sum(1 for line in lines if "|" in line)
    table_like = pipe_lines / len(lines) >= 0.70
    canonical_header = len(lines) >= 2 and "|" in lines[0] and _is_markdown_separator(lines[1])
    data_lines = []
    if canonical_header:
        for line in lines[2:]:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if any(re.search(r"[A-Za-z0-9]", cell or "") for cell in cells):
                data_lines.append(line)
    header_only = canonical_header and not data_lines
    fragment_like = canonical_header and bool(data_lines) and any("|" not in line for line in data_lines)
    return table_like or canonical_header, header_only, fragment_like


def _content_type(chunk: dict[str, Any]) -> tuple[str, bool]:
    raw = str(chunk.get("raw_text") or "")
    table_like, header_only, fragment_like = _table_shape(raw)
    refs = [str(item) for item in (chunk.get("doc_items") or [])]
    references_table = any(ref.startswith("#/tables/") for ref in refs)
    flattened_table = _looks_like_space_delimited_table(raw)
    if header_only:
        return "table_header_only", True
    if fragment_like:
        return "table_fragment", True
    if table_like or references_table or flattened_table:
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
    alpha_labels = numeric_values = 0
    label_density = 1.0
    if table_related:
        alpha_labels, numeric_values, label_density = _table_label_density(raw)
        if _table_data_without_header(raw):
            warnings.append("TABLE_DATA_WITHOUT_HEADER")
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
    quality -= 35 if "TABLE_DATA_WITHOUT_HEADER" in warnings else 0
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
        "table_alpha_label_tokens": alpha_labels,
        "table_numeric_tokens": numeric_values,
        "table_label_density": round(label_density, 3),
    }



def _annotate_table_context(chunks: list[dict[str, Any]], max_tokens: int) -> list[dict[str, Any]]:
    """Attach deterministic table-continuity metadata to derived Stage-3 rows.

    Raw text is never changed.  The pointer lets later retrieval reconstruction
    recover labels for data fragments and makes page-break continuation visible
    to the Chunk Viewer/audit tooling.
    """
    rows = [dict(row) for row in chunks]
    last_header: dict[str, dict[str, Any]] = {}
    ordered = sorted(range(len(rows)), key=lambda i: int(rows[i].get("chunk_index") or i))
    for pos in ordered:
        row = rows[pos]
        refs = _table_refs(row)
        if not refs:
            continue
        raw = str(row.get("raw_text") or "")
        pages = []
        for value in row.get("page_numbers") or []:
            try:
                pages.append(int(value))
            except (TypeError, ValueError):
                pass
        pages = sorted(set(pages))
        meta = retrieval_metadata(row, max_tokens)
        cells = _table_header_cells(raw)
        idx = int(row.get("chunk_index") or 0)
        contexts: list[dict[str, Any]] = []
        for ref in refs:
            prior = last_header.get(ref)
            if prior and str(prior.get("chunk_id") or "") != str(row.get("chunk_id") or ""):
                prior_page = int(prior.get("page") or 0)
                current_page = int(pages[0]) if pages else prior_page
                distance = idx - int(prior.get("chunk_index") or idx)
                if 0 < distance <= 8 and current_page >= prior_page and current_page - prior_page <= 1:
                    contexts.append({
                        "table_ref": ref,
                        "table_continuation": bool(
                            current_page > prior_page
                            or meta.get("content_type") in {"table_fragment", "table_header_only"}
                            or "TABLE_DATA_WITHOUT_HEADER" in (meta.get("warnings") or [])
                        ),
                        "table_header_anchor": prior.get("chunk_id"),
                        "table_header_page": prior_page or None,
                        "table_header_distance": distance,
                        "table_header_cells": list(prior.get("header_cells") or []),
                    })
            if cells:
                last_header[ref] = {
                    "chunk_id": row.get("chunk_id"),
                    "chunk_index": idx,
                    "page": int(pages[0]) if pages else 0,
                    "header_cells": cells,
                }
        if contexts:
            row["table_context"] = contexts[0] if len(contexts) == 1 else {"anchors": contexts}
        rows[pos] = row
    return rows


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
    chunks = _annotate_table_context(chunks, max_tokens)
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
            "retrieval_rule_version": RETRIEVAL_RULE_VERSION,
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
            "table_related": bool(meta["table_related"]),
            "quality_score": meta["quality_score"],
            "warnings": meta["warnings"],
            "table_context": chunk.get("table_context"),
            "stage2c_correction_count": len(((chunk.get("stage2c") or {}).get("text_corrections") or [])),
            "vision_enrichment_count": len(((chunk.get("stage2c") or {}).get("vision_enrichment") or [])),
        })

    stitched_rows = _build_stitched_table_evidence(
        output,
        postprocess_job_id=postprocess_job_id,
        source_filename=source_filename,
        result_dir_name=result_dir_name,
        max_tokens=max_tokens,
    )
    index_rows.extend(stitched_rows)

    summary = {
        "schema": _SCHEMA_QUALITY,
        "retrieval_rule_version": RETRIEVAL_RULE_VERSION,
        "generated_at_epoch": time.time(),
        "postprocess_job_id": postprocess_job_id,
        "source_filename": source_filename,
        "result_dir": result_dir_name,
        "total_chunks": len(output),
        "searchable_chunks": len(index_rows),
        "canonical_searchable_chunks": len(index_rows) - len(stitched_rows),
        "stitched_table_evidence": len(stitched_rows),
        "stitched_table_source_chunks": sum(int(row.get("source_chunk_count") or 0) for row in stitched_rows),
        "cross_page_stitched_tables": sum(1 for row in stitched_rows if row.get("cross_page_table")),
        "table_header_anchors": sum(1 for row in output if row.get("table_context")),
        "table_data_without_header_chunks": int(warning_counts.get("TABLE_DATA_WITHOUT_HEADER", 0)),
        "flattened_table_chunks": sum(1 for row in output if row.get("retrieval", {}).get("content_type") == "table" and _looks_like_space_delimited_table(str(row.get("raw_text") or ""))),
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
    _write_jsonl_atomic(result_dir / "table_evidence.jsonl", [row for row in index_rows if row.get("stitched_table")])
    tmp = result_dir / "retrieval_quality.json.tmp"
    tmp.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(result_dir / "retrieval_quality.json")
    _load_index_cached.cache_clear()
    return summary


def refresh_retrieval_artifacts(result_dir: Path, *, max_tokens: int = 256) -> dict[str, Any]:
    """Rebuild derived retrieval artifacts from existing canonical Stage 3 chunks.

    This is intentionally retrieval-only: ``chunks.jsonl`` is read but never
    rewritten, so a retrieval-rule upgrade cannot silently alter canonical
    Stage 3 output or trigger Docling/model work.
    """
    result_dir = Path(result_dir)
    chunks_path = result_dir / "chunks.jsonl"
    if not chunks_path.is_file():
        raise FileNotFoundError("Stage 3 chunks.jsonl is missing")
    chunks: list[dict[str, Any]] = []
    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            chunks.append(value)
    manifest: dict[str, Any] = {}
    try:
        manifest = json.loads((result_dir / "source_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"__job(\d+)", result_dir.name)
    postprocess_job_id = int(match.group(1)) if match else None
    source_filename = str(manifest.get("source_filename") or result_dir.name)
    _annotated, index_rows, summary = annotate_retrieval_rows(
        chunks, postprocess_job_id=postprocess_job_id, source_filename=source_filename,
        result_dir_name=result_dir.name, max_tokens=max_tokens,
    )
    _write_jsonl_atomic(result_dir / "retrieval_index.jsonl", index_rows)
    _write_jsonl_atomic(result_dir / "table_evidence.jsonl", [row for row in index_rows if row.get("stitched_table")])
    tmp = result_dir / "retrieval_quality.json.tmp"
    tmp.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(result_dir / "retrieval_quality.json")
    _load_index_cached.cache_clear()
    return summary


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


def _structurally_related_context(anchor: dict[str, Any], other: dict[str, Any]) -> str | None:
    """Return a deterministic structural relation, never plain adjacency.

    Nearby chunks can contain unrelated settings on the same page. We therefore
    expand generation context only when the chunks share Docling items or the
    same deepest heading/section.
    """
    anchor_refs = {str(v) for v in (anchor.get("doc_items") or []) if str(v).strip()}
    other_refs = {str(v) for v in (other.get("doc_items") or []) if str(v).strip()}
    if anchor_refs and other_refs and anchor_refs.intersection(other_refs):
        return "shared_docling_item"
    anchor_headings = [re.sub(r"\s+", " ", str(v)).strip().casefold() for v in (anchor.get("headings") or []) if str(v).strip()]
    other_headings = [re.sub(r"\s+", " ", str(v)).strip().casefold() for v in (other.get("headings") or []) if str(v).strip()]
    if anchor_headings and other_headings and anchor_headings[-1] == other_headings[-1]:
        return "same_heading"
    return None


def _table_diversity_key(row: dict[str, Any]) -> str | None:
    """Return a conservative duplicate-cluster key for reconstructed table evidence.

    Only synthetic stitched rows are clustered. Canonical source chunks remain
    independently rankable so a useful source row is never hidden behind a
    synthetic representative.
    """
    if not row.get("stitched_table"):
        return None
    table_ref = str(row.get("table_ref") or "").strip()
    if not table_ref:
        return None
    scope = str(row.get("result_dir") or row.get("source_filename") or "")
    return f"{scope}::{table_ref}"


def diversify_results(rows: list[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
    """Keep near-duplicate stitched rows from consuming the entire early Top-K.

    The best synthetic representative for a table is kept in the first pass.
    Deferred variants are only used when there are not enough distinct results.
    This preserves recall while improving evidence diversity.
    """
    limit = max(1, int(top_k))
    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = _table_diversity_key(row)
        if key and key in seen:
            deferred.append(dict(row))
            continue
        if key:
            seen.add(key)
        selected.append(dict(row))
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for row in deferred:
            selected.append(dict(row))
            if len(selected) >= limit:
                break
    for rank, row in enumerate(selected, start=1):
        row["rank"] = rank
        if _table_diversity_key(row):
            row["diversity_clustered"] = True
    return selected


def _adjacent_context(docs: list[dict[str, Any]], idx: int, *, limit: int = 2) -> list[dict[str, Any]]:
    row = docs[idx]
    try:
        center = int(row.get("chunk_index"))
    except (TypeError, ValueError):
        return []
    job_id = row.get("postprocess_job_id")
    source = row.get("source_filename")
    candidates: list[tuple[int, dict[str, Any], str]] = []
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
        relation = _structurally_related_context(row, other)
        if not relation:
            continue
        copy = dict(other)
        copy.pop("_tokens", None); copy.pop("_heading_tokens", None); copy.pop("_normalized_text", None)
        candidates.append((distance, copy, relation))
    candidates.sort(key=lambda pair: (pair[0], int(pair[1].get("chunk_index") or 0)))
    return [
        {
            "chunk_id": value.get("chunk_id"),
            "chunk_index": value.get("chunk_index"),
            "page_numbers": value.get("page_numbers") or [],
            "doc_items": value.get("doc_items") or [],
            "headings": value.get("headings") or [],
            "text": value.get("text") or "",
            "content_type": value.get("content_type"),
            "structural_relation": relation,
        }
        for _, value, relation in candidates[:limit]
    ]


def search_indices(index_paths: list[Path], query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
    q_tokens = _tokens(query)
    if _LOAD_QUERY_RE.search(query or ""):
        # Generic lifting-load terminology expansion.  Retrieval may surface
        # both SWL/WLL evidence and operating-capacity tables; generation must
        # preserve their source context rather than equating them.
        q_tokens = list(dict.fromkeys(q_tokens + ["swl", "wll", "working", "load", "limit", "hoisting", "lifting", "capacity"]))
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
    candidates: list[dict[str, Any]] = []
    candidate_limit = min(len(scored), max(max(1, int(top_k)) * 6, max(1, int(top_k))))
    for rank, (score, idx) in enumerate(scored[:candidate_limit], start=1):
        row = dict(docs[idx])
        row.pop("_tokens", None); row.pop("_heading_tokens", None); row.pop("_normalized_text", None)
        candidates.append({
            **row,
            "rank": rank,
            "score": round(score, 4),
            "snippet": _snippet(str(row.get("text") or ""), q_tokens),
            "cross_references": extract_cross_references(str(row.get("text") or "")),
            "context_neighbors": _adjacent_context(docs, idx) if (procedure_intent or bool(_TROUBLESHOOT_INTENT_RE.search(query or ""))) else [],
        })
    return diversify_results(candidates, top_k=max(1, int(top_k)))


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
    expected_equipment_id: str | None = None,
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
        if expected_equipment_id:
            item["expected_equipment_id"] = str(expected_equipment_id)
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
        "expected_equipment_id": str(expected_equipment_id) if expected_equipment_id else None,
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


def benchmark_expected_rank(results: list[dict[str, Any]], item: dict[str, Any]) -> int | None:
    return next((int(row["rank"]) for row in results if _matches_expected(row, item)), None)


def run_benchmark(
    processed_dir: Path,
    index_paths: list[Path],
    *,
    top_k: int = 5,
    equipment_index_paths: dict[str, list[Path]] | None = None,
    case_equipment_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run lexical regression without ever silently crossing machine scopes.

    When equipment mappings are supplied, legacy cases without an explicit or
    uniquely inferred machine are reported as skipped rather than searched
    across all books. This keeps benchmark metrics aligned with production RAG.
    """
    payload = load_benchmark(processed_dir)
    items = list(payload.get("items") or [])
    details: list[dict[str, Any]] = []
    ranks: list[int | None] = []
    scoped_cases = 0
    skipped_cases = 0
    equipment_mode = equipment_index_paths is not None
    inferred = case_equipment_ids or {}
    for item in items:
        item_id = str(item.get("id") or "")
        expected_equipment_id = str(item.get("expected_equipment_id") or inferred.get(item_id) or "").strip()
        case_paths = index_paths
        scope_error = None
        if equipment_mode:
            if not expected_equipment_id:
                skipped_cases += 1
                details.append({
                    "id": item.get("id"), "query": item.get("query"),
                    "expected_equipment_id": None, "scope_error": "equipment_scope_required",
                    "rank": None, "hit": False, "skipped": True, "top_result": None,
                })
                continue
            scoped_cases += 1
            case_paths = list((equipment_index_paths or {}).get(expected_equipment_id) or [])
            if not case_paths:
                skipped_cases += 1
                details.append({
                    "id": item.get("id"), "query": item.get("query"),
                    "expected_equipment_id": expected_equipment_id, "scope_error": "equipment_scope_unavailable",
                    "rank": None, "hit": False, "skipped": True, "top_result": None,
                })
                continue
        elif expected_equipment_id:
            scoped_cases += 1
        results = search_indices(case_paths, str(item.get("query") or ""), top_k=top_k) if case_paths else []
        rank = benchmark_expected_rank(results, item)
        ranks.append(rank)
        details.append({
            "id": item.get("id"),
            "query": item.get("query"),
            "expected_equipment_id": expected_equipment_id or None,
            "scope_error": scope_error,
            "rank": rank,
            "hit": rank is not None,
            "skipped": False,
            "top_result": results[0] if results else None,
        })
    eligible = len(ranks)
    def rate(limit: int) -> float:
        if not eligible:
            return 0.0
        return round(100.0 * sum(rank is not None and rank <= limit for rank in ranks) / eligible, 1)
    mrr = round(sum((1.0 / rank) if rank else 0.0 for rank in ranks) / eligible, 4) if eligible else 0.0
    result = {
        "schema": "docling-retrieval-benchmark-result/v2",
        "cases": len(items),
        "eligible_cases": eligible,
        "skipped_cases": skipped_cases,
        "top1_percent": rate(1),
        "top3_percent": rate(3),
        "top5_percent": rate(5),
        "mrr": mrr,
        "equipment_scoped_cases": scoped_cases,
        "all_books_fallback_used": False if equipment_mode else None,
        "details": details,
        "generated_at_epoch": time.time(),
    }
    path = benchmark_result_path(processed_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return result
