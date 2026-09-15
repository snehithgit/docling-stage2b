from __future__ import annotations

import asyncio
import hashlib
import difflib
import json
import math
import os
import re
import time
import zipfile
import unicodedata
import fitz
from urllib.parse import unquote, urlparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .archive import select_docling_document
from .stage2c import rebuild_chunk_overlays
from .config import AppConfig
from .events import EventBroker
from .groq_quota import GroqQuotaGuard
from .postprocess_store import PostprocessStore
from .verifier_clients import GroqStructuredVerifier, OpenAICompatibleVerifier


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

OCR_RECALL_STRONG_EVIDENCE_KINDS = {
    "split_join_document_match",
    "spaced_apostrophe_document_match",
    "repeated_block_variant",
}


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


LEXICAL_TOKEN_RE = re.compile(r"(?<![\w-])[^\W\d_]+(?:[’'][^\W\d_]+)?(?![\w-])", re.UNICODE)


def _lexical_token(value: str) -> str:
    return (value or "").replace("’", "'").casefold()


def _bounded_edit_distance(a: str, b: str, limit: int) -> int | None:
    """Return edit distance when <= limit, otherwise None.

    This is intentionally tiny and dependency-free. It is used only after
    document-local trigram filtering, so Stage 2A remains light on CPU.
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return None
    if len(a) > len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        row_min = i
        lo = max(1, i - limit)
        hi = min(len(b), i + limit)
        for j in range(1, len(b) + 1):
            if j < lo or j > hi:
                current.append(limit + 1)
                continue
            cost = 0 if ca == b[j - 1] else 1
            value = min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + cost)
            current.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return None
        previous = current
    distance = previous[-1]
    return distance if distance <= limit else None


def _word_ngrams(token: str, n: int = 3) -> set[str]:
    compact = re.sub(r"[^\w]", "", token, flags=re.UNICODE)
    if len(compact) <= n:
        return {compact} if compact else set()
    return {compact[i:i+n] for i in range(len(compact) - n + 1)}


def _technical_or_identifier_token(surface: str) -> bool:
    """Protect identifiers/labels from document-local spell-like matching.

    The recall layer is evidence generation only, but avoiding obvious model IDs,
    terminal tags and all-cap engineering labels keeps Pi5 load bounded.
    """
    token = surface or ""
    if any(ch.isdigit() for ch in token) or "_" in token:
        return True
    letters = [ch for ch in token if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters) and len(letters) <= 12:
        return True
    return False


def _simple_inflection_pair(a: str, b: str) -> bool:
    """Suppress obvious grammatical variants without using a dictionary."""
    if a == b:
        return True
    suffixes = ("ingly", "ation", "ations", "tion", "tions", "ing", "ers", "ies", "ied", "ed", "er", "es", "s", "ly", "d", "y")

    def stems(token: str) -> set[str]:
        token = token.replace("'s", "")
        out = {token}
        for suffix in suffixes:
            if token.endswith(suffix) and len(token) - len(suffix) >= 5:
                out.add(token[:-len(suffix)])
        return out

    sa, sb = stems(a), stems(b)
    return bool(sa & sb)


def _document_ocr_recall_candidates(texts: list[dict[str, Any]], config: AppConfig, excluded_indices: set[int] | None = None) -> list[dict[str, Any]]:
    """Find plausible OCR outliers using only evidence from this document.

    This is deliberately language- and manufacturer-agnostic: a rare token is
    compared only with frequent tokens already present in the same document.
    It never changes text. Its sole output is extra Pi5 review candidates.
    """
    if not getattr(config, "stage2a_ocr_recall_enabled", True):
        return []

    excluded = excluded_indices or set()
    token_counts: Counter[str] = Counter()
    token_surfaces: dict[str, Counter[str]] = defaultdict(Counter)
    item_tokens: list[list[tuple[str, str]]] = []

    for item in texts:
        value = str(item.get("text") or "")
        row: list[tuple[str, str]] = []
        for match in LEXICAL_TOKEN_RE.finditer(value):
            surface = match.group(0)
            norm = _lexical_token(surface)
            if not norm:
                continue
            token_counts[norm] += 1
            token_surfaces[norm][surface] += 1
            row.append((surface, norm))
        item_tokens.append(row)

    common_min = int(getattr(config, "stage2a_ocr_recall_common_min_count", 5))
    rare_max = int(getattr(config, "stage2a_ocr_recall_rare_max_count", 2))
    common = {t for t, c in token_counts.items() if c >= common_min and len(t) >= 4}
    trigram_index: dict[str, set[str]] = defaultdict(set)
    for token in common:
        for gram in _word_ngrams(token):
            trigram_index[gram].add(token)

    candidate_word_map: dict[str, tuple[str, int, float]] = {}
    for token, count in token_counts.items():
        if count > rare_max or len(token) < 6 or token in common:
            continue
        surface = token_surfaces[token].most_common(1)[0][0]
        if _technical_or_identifier_token(surface):
            continue
        pool: set[str] = set()
        grams = _word_ngrams(token)
        for gram in grams:
            pool.update(trigram_index.get(gram, ()))
        if not pool:
            continue
        ranked: list[tuple[float, int, int, str]] = []
        for target in pool:
            if target == token or abs(len(target) - len(token)) > 2:
                continue
            if _simple_inflection_pair(token, target):
                continue
            limit = 1 if max(len(target), len(token)) <= 6 else 2
            dist = _bounded_edit_distance(token, target, limit)
            if dist is None or dist == 0:
                continue
            similarity = 1.0 - (dist / max(len(target), len(token)))
            if similarity < 0.84:
                continue
            target_count = token_counts[target]
            if target_count < max(common_min, count * 4):
                continue
            ranked.append((similarity, target_count, -dist, target))
        if ranked:
            ranked.sort(reverse=True)
            similarity, _target_count, neg_dist, target = ranked[0]
            candidate_word_map[token] = (target, -neg_dist, similarity)

    # Repeated-block consistency. If the document itself repeats the same long
    # block at least twice, variants sharing the same opening tokens can be
    # compared with that internal canonical form. This catches multi-word OCR
    # damage that no individual token comparison can reliably identify.
    repeated_block_evidence: dict[int, dict[str, Any]] = {}
    block_groups: dict[tuple[str, ...], list[tuple[int, str]]] = defaultdict(list)
    for idx, tokens in enumerate(item_tokens):
        norms = [norm for _surface, norm in tokens]
        if len(norms) < 10:
            continue
        key = tuple(norms[:5])
        normalized = " ".join(norms)
        block_groups[key].append((idx, normalized))
    for rows in block_groups.values():
        if len(rows) < 3:
            continue
        counts = Counter(text for _idx, text in rows)
        canonical, canonical_count = counts.most_common(1)[0]
        if canonical_count < 2:
            continue
        for idx, normalized in rows:
            if normalized == canonical:
                continue
            ratio = difflib.SequenceMatcher(None, normalized, canonical, autojunk=False).ratio()
            if 0.65 <= ratio < 0.985:
                repeated_block_evidence[idx] = {
                    "kind": "repeated_block_variant",
                    "canonical_repeat_count": canonical_count,
                    "similarity": round(ratio, 4),
                    "canonical_excerpt": canonical[:240],
                }

    max_candidates = int(getattr(config, "stage2a_ocr_recall_max_candidates", 150))
    candidates: list[dict[str, Any]] = []
    common_with_apostrophe = {t for t in common if "'" in t}

    for i, (item, tokens) in enumerate(zip(texts, item_tokens)):
        if i in excluded:
            continue
        value = str(item.get("text") or "")
        reasons: list[str] = []
        evidence: list[dict[str, Any]] = []
        best_score = 0.0

        seen_norms: set[str] = set()
        item_norms = [n for _s, n in tokens]
        for token_pos, (surface, norm) in enumerate(tokens):
            if norm in seen_norms:
                continue
            seen_norms.add(norm)
            match = candidate_word_map.get(norm)
            if not match:
                continue
            target, distance, similarity = match
            target_positions = [pos for pos, item_norm in enumerate(item_norms) if item_norm == target]
            if target_positions and min(abs(pos - token_pos) for pos in target_positions) <= 3:
                # Side-by-side glossary/translation forms often contain both a
                # source-language spelling and a similar target-language spelling.
                continue
            evidence.append({
                "kind": "rare_near_frequent_token",
                "observed": surface,
                "observed_count": token_counts[norm],
                "document_variant": token_surfaces[target].most_common(1)[0][0],
                "variant_count": token_counts[target],
                "edit_distance": distance,
                "similarity": round(similarity, 4),
            })
            reasons.append("rare_token_near_frequent_document_token")
            frequency_strength = min(1.0, token_counts[target] / 50.0)
            lexical_score = 0.75 * similarity + 0.25 * frequency_strength
            best_score = max(best_score, lexical_score)

        # Split/join evidence: only fire when one side is a single letter or the
        # source visibly separates an apostrophe. This avoids treating normal
        # compounds such as "data base" as OCR corruption.
        for pos in range(len(tokens) - 1):
            s1, n1 = tokens[pos]
            s2, n2 = tokens[pos + 1]
            joined = n1 + n2
            single_surface = s1 if len(n1) == 1 else (s2 if len(n2) == 1 else "")
            single_norm = n1 if len(n1) == 1 else (n2 if len(n2) == 1 else "")
            visibly_space_joined = bool(re.search(rf"(?<!\w){re.escape(s1)}\s+{re.escape(s2)}(?!\w)", value))
            if (
                single_surface
                and single_surface.islower()
                and single_norm not in COMMON_SHORT_WORDS
                and visibly_space_joined
                and joined in common
            ):
                evidence.append({
                    "kind": "split_join_document_match",
                    "observed": f"{s1} {s2}",
                    "document_variant": token_surfaces[joined].most_common(1)[0][0],
                    "variant_count": token_counts[joined],
                })
                reasons.append("split_token_matches_frequent_document_token")
                best_score = max(best_score, 0.92)

        if common_with_apostrophe and re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]\s+['’]|['’]\s+[A-Za-zÀ-ÖØ-öø-ÿ]", value):
            compact = re.sub(r"\s*(['’])\s*", r"\1", value).replace("’", "'").casefold()
            for target in common_with_apostrophe:
                if target in compact:
                    evidence.append({
                        "kind": "spaced_apostrophe_document_match",
                        "document_variant": token_surfaces[target].most_common(1)[0][0],
                        "variant_count": token_counts[target],
                    })
                    reasons.append("spaced_apostrophe_matches_frequent_document_token")
                    best_score = max(best_score, 0.95)
                    break

        repeated_ev = repeated_block_evidence.get(i)
        repeated_single_fragment = bool(re.search(r"(?i)(?<!\w)([a-z])(?:\s+\1){2,}(?!\w)", value))
        weak_structural_anomaly = bool(
            evidence
            or repeated_single_fragment
            or REPLACEMENT_RE.search(value)
            or CONTROL_RE.search(value)
            or _generic_ocr_garble_reasons(value)
        )
        if repeated_ev and weak_structural_anomaly:
            evidence.append(repeated_ev)
            reasons.append("repeated_block_differs_from_document_canonical")
            best_score = max(best_score, 0.88 + min(0.08, 0.01 * repeated_ev["canonical_repeat_count"]))

        if not evidence:
            continue
        # Prefer concise body text and stronger internal evidence, but preserve
        # the original Docling text exactly for Pi5/human verification.
        evidence_kinds = {str(ev.get("kind") or "") for ev in evidence}
        routing_eligible = bool(evidence_kinds & OCR_RECALL_STRONG_EVIDENCE_KINDS)
        candidates.append({
            "text_index": i,
            "page": _page_of(item),
            "label": item.get("label"),
            "reasons": sorted(set(reasons)),
            "text": value[:500],
            "evidence": evidence[:8],
            "recall_score": round(best_score, 4),
            "recall_confidence": "strong" if routing_eligible else "weak",
            "routing_eligible": routing_eligible,
            "contains_technical_value": bool(TECH_VALUE_RE.search(value) or INEQUALITY_RE.search(value)),
        })

    candidates.sort(
        key=lambda x: (
            bool(x.get("routing_eligible")),
            bool(x.get("contains_technical_value")),
            float(x.get("recall_score") or 0),
            len(x.get("evidence") or []),
        ),
        reverse=True,
    )
    return candidates


CONFUSABLE_SCRIPT_CHARS = {
    "α":"a", "Α":"A", "β":"b", "Β":"B", "γ":"y", "Γ":"G", "δ":"d", "Δ":"D",
    "ε":"e", "Ε":"E", "λ":"l", "Λ":"L", "μ":"u", "Μ":"M", "ν":"v", "Ν":"N",
    "ο":"o", "Ο":"O", "ρ":"p", "Ρ":"P", "τ":"t", "Τ":"T", "χ":"x", "Χ":"X",
    "ϕ":"f", "ϖ":"p", "і":"i", "І":"I", "с":"c", "С":"C", "х":"x", "Х":"X",
}


def _script_of_char(ch: str) -> str:
    name = unicodedata.name(ch, "")
    for script in ("LATIN", "CYRILLIC", "GREEK", "ARABIC", "HEBREW", "DEVANAGARI", "BENGALI", "TAMIL", "THAI", "HANGUL", "HIRAGANA", "KATAKANA", "CJK"):
        if script in name:
            return script
    return "OTHER"


def _bbox_problem(item: dict[str, Any], pages: dict[str, Any], page_hint: int | None = None) -> tuple[str | None, dict[str, Any]]:
    prov = item.get("prov") or []
    if not prov:
        return "missing_provenance", {}
    first = prov[0] if isinstance(prov[0], dict) else {}
    page = first.get("page_no", page_hint)
    bbox = first.get("bbox")
    if not isinstance(bbox, dict):
        return "missing_bbox", {"page": page}
    try:
        l,t,r,b = (float(bbox.get(k)) for k in ("l","t","r","b"))
    except (TypeError, ValueError):
        return "invalid_bbox", {"page": page, "bbox": bbox}
    if not all(math.isfinite(v) for v in (l,t,r,b)):
        return "invalid_bbox", {"page": page, "bbox": bbox}
    if abs(r-l) <= 1e-9 or abs(t-b) <= 1e-9:
        return "degenerate_bbox", {"page": page, "bbox": bbox}
    page_meta = pages.get(str(page)) or {}
    size = page_meta.get("size") or {}
    try:
        width,height=float(size.get("width")),float(size.get("height"))
    except (TypeError, ValueError):
        return None, {"page": page, "bbox": bbox}
    if width > 0 and height > 0:
        xs=(l,r); ys=(t,b)
        if max(xs) < -1 or min(xs) > width+1 or max(ys) < -1 or min(ys) > height+1:
            return "bbox_outside_page", {"page": page, "bbox": bbox, "page_size": size}
    return None, {"page": page, "bbox": bbox}


def _unicode_anomalies(value: str) -> list[dict[str, Any]]:
    out=[]
    for pos,ch in enumerate(value or ""):
        cat=unicodedata.category(ch)
        cp=ord(ch)
        kind=None
        if ch == "\ufffd": kind="replacement_character"
        elif cat == "Co": kind="private_use_character"
        elif cat in {"Cc","Cs"} and ch not in "\t\n\r": kind="control_or_surrogate_character"
        elif (0xFDD0 <= cp <= 0xFDEF) or (cp & 0xFFFF) in {0xFFFE,0xFFFF}: kind="unicode_noncharacter"
        elif ch == "□": kind="placeholder_square"
        if kind:
            left=max(0,pos-14); right=min(len(value),pos+15)
            out.append({"kind":kind,"codepoint":f"U+{cp:04X}","position":pos,"context":value[left:right]})
    return out


def _docling_integrity_findings(doc: dict[str, Any]) -> dict[str, Any]:
    texts=doc.get("texts") or []; tables=doc.get("tables") or []; pages=doc.get("pages") or {}
    findings={"empty_text":[],"geometry":[],"unicode":[],"mixed_script":[],"table_grid":[],"broken_reference":[],"unreachable_text":[],"reference_cycle":[]}
    for i,item in enumerate(texts):
        value=str((item or {}).get("text") or "")
        if not value.strip():
            findings["empty_text"].append({"text_index":i,"page":_page_of(item),"label":item.get("label")})
        problem,details=_bbox_problem(item,pages)
        if problem:
            findings["geometry"].append({"source_type":"text","text_index":i,"problem":problem,**details})
        ua=_unicode_anomalies(value)
        if ua:
            findings["unicode"].append({"source_type":"text","text_index":i,"page":_page_of(item),"text":value[:240],"anomalies":ua[:12]})
        for m in LEXICAL_TOKEN_RE.finditer(value):
            tok=m.group(0); scripts={_script_of_char(c) for c in tok if c.isalpha()}
            if len(tok)>=4 and "LATIN" in scripts and ({"CYRILLIC","GREEK"}&scripts):
                findings["mixed_script"].append({"source_type":"text","text_index":i,"page":_page_of(item),"token":tok,"scripts":sorted(scripts)})
                break
    for ti,table in enumerate(tables):
        page=_page_of(table); cells=((table.get("data") or {}).get("table_cells") or [])
        nr=(table.get("data") or {}).get("num_rows"); nc=(table.get("data") or {}).get("num_cols")
        positions={}; collisions=[]; invalid=[]; empty=0
        for ci,cell in enumerate(cells):
            value=str(cell.get("text") or "")
            if not value.strip(): empty += 1
            ua=_unicode_anomalies(value)
            if ua:
                findings["unicode"].append({"source_type":"table_cell","table_index":ti,"cell_index":ci,"page":page,"text":value[:240],"anomalies":ua[:12]})
            a,b,x,y=(cell.get(k) for k in ("start_row_offset_idx","end_row_offset_idx","start_col_offset_idx","end_col_offset_idx"))
            if not all(type(v) is int for v in (a,b,x,y)) or a<0 or x<0 or b<=a or y<=x or (isinstance(nr,int) and b>nr) or (isinstance(nc,int) and y>nc):
                invalid.append({"cell_index":ci,"span":[a,b,x,y]}); continue
            if (b-a)*(y-x) > 10000:
                invalid.append({"cell_index":ci,"span":[a,b,x,y],"reason":"unreasonable_span"}); continue
            for rr in range(a,b):
                for cc in range(x,y):
                    if (rr,cc) in positions: collisions.append({"first_cell":positions[(rr,cc)],"second_cell":ci,"row":rr,"col":cc})
                    positions[(rr,cc)] = ci
        if invalid or collisions or (len(cells)>=8 and empty/len(cells)>=0.80):
            findings["table_grid"].append({"table_index":ti,"page":page,"cells":len(cells),"empty_cells":empty,"invalid_spans":invalid[:20],"overlaps":collisions[:20],"overlap_count":len(collisions)})
    # Internal document graph integrity.
    nodes={}
    for key in ("texts","tables","pictures","groups","key_value_items","form_items"):
        for i,item in enumerate(doc.get(key) or []): nodes[f"#/{key}/{i}"]=item
    for root in ("body","furniture"):
        if isinstance(doc.get(root),dict): nodes[f"#/{root}"]=doc[root]
    visited=set(); active=set()
    def walk(ref:str, depth:int=0):
        if ref in active or depth>200:
            findings["reference_cycle"].append({"ref":ref,"depth":depth}); return
        node=nodes.get(ref)
        if node is None:
            findings["broken_reference"].append({"ref":ref,"field":"children"}); return
        if ref in visited: return
        visited.add(ref); active.add(ref)
        for child in node.get("children") or []:
            if isinstance(child,dict) and isinstance(child.get("$ref"),str): walk(child["$ref"],depth+1)
        active.discard(ref)
    for root in ("#/body","#/furniture"):
        if root in nodes: walk(root)
    for ref,node in nodes.items():
        for field in ("parent","captions","references","footnotes"):
            vals=node.get(field) or []
            if isinstance(vals,dict): vals=[vals]
            for val in vals:
                if isinstance(val,dict) and isinstance(val.get("$ref"),str) and val["$ref"] not in nodes:
                    findings["broken_reference"].append({"ref":ref,"field":field,"target":val["$ref"]})
    if "#/body" in nodes:
        for i in range(len(texts)):
            ref=f"#/texts/{i}"
            if ref not in visited: findings["unreachable_text"].append({"text_index":i,"ref":ref,"page":_page_of(texts[i])})
    return findings


def _source_pdf_crosscheck(doc: dict[str, Any], source_path: Path | None) -> dict[str, Any]:
    result = {"status": "unavailable", "findings": [], "pages_compared": 0}
    if not source_path or source_path.suffix.lower() != ".pdf" or not source_path.is_file():
        return result
    pages = doc.get("pages") or {}
    json_count = len(pages)
    try:
        with fitz.open(source_path) as pdf:
            pdf_count = len(pdf)
            result.update({"status": "checked", "pdf_pages": pdf_count, "json_pages": json_count})
            if pdf_count != json_count:
                result["findings"].append({"kind": "pdf_page_count_mismatch", "pdf_pages": pdf_count, "json_pages": json_count,
                    "note": "Page-aligned text/geometry comparison withheld because page alignment is not reliable."})
                return result
            text_by_page=defaultdict(list)
            for item in doc.get("texts") or []:
                page=_page_of(item)
                if page: text_by_page[page].append(str(item.get("text") or ""))
            for ti,table in enumerate(doc.get("tables") or []):
                page=_page_of(table)
                if page:
                    text_by_page[page].extend(str(c.get("text") or "") for c in ((table.get("data") or {}).get("table_cells") or []))
            for page_no in range(1,pdf_count+1):
                page=pdf[page_no-1]
                jmeta=pages.get(str(page_no)) or {}; size=jmeta.get("size") or {}
                try: jw,jh=float(size.get("width")),float(size.get("height"))
                except (TypeError,ValueError): jw=jh=0.0
                if jw>0 and jh>0 and (abs(jw-page.rect.width)/max(1,page.rect.width)>0.05 or abs(jh-page.rect.height)/max(1,page.rect.height)>0.05):
                    result["findings"].append({"kind":"pdf_geometry_mismatch","page":page_no,"pdf_size":[round(page.rect.width,2),round(page.rect.height,2)],"json_size":[jw,jh]})
                native_words=[str(w[4]).casefold() for w in page.get_text("words") if len(w)>=5 and str(w[4]).isalpha()]
                json_text=" ".join(text_by_page.get(page_no,[])).casefold()
                if len(native_words)>=20:
                    vocab=set(native_words)
                    covered=sum(1 for w in vocab if w in json_text)
                    ratio=covered/max(1,len(vocab))
                    if ratio<0.35:
                        result["findings"].append({"kind":"pdf_text_coverage_gap","page":page_no,"native_unique_words":len(vocab),"coverage_ratio":round(ratio,4)})
                json_chars=len(re.sub(r"\s+","",json_text))
                if len(page.get_images(full=True))>=1 and json_chars<40 and len(native_words)<10:
                    result["findings"].append({"kind":"image_page_low_text","page":page_no,"json_chars":json_chars,"pdf_images":len(page.get_images(full=True))})
                result["pages_compared"] += 1
    except Exception as exc:
        result={"status":"error","findings":[],"error":f"{type(exc).__name__}: {exc}"}
    return result


def _layout_audit_findings(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    pages=doc.get("pages") or {}; repeated=defaultdict(list); cross=[]
    for i,item in enumerate(doc.get("texts") or []):
        page=_page_of(item); value=re.sub(r"\s+"," ",str(item.get("text") or "").strip())
        if not page or not value: continue
        prov=item.get("prov") or []
        if prov and isinstance(prov[0],dict) and isinstance(prov[0].get("bbox"),dict):
            b=prov[0]["bbox"]; size=(pages.get(str(page)) or {}).get("size") or {}
            try:
                h=float(size.get("height")); t=float(b.get("t")); bottom=float(b.get("b")); origin=str(b.get("coord_origin") or "BOTTOMLEFT").upper()
                if origin=="BOTTOMLEFT": y0,y1=h-t,h-bottom
                else: y0,y1=t,bottom
                lo,hi=min(y0,y1),max(y0,y1)
                band="top" if hi<h*.12 else ("bottom" if lo>h*.88 else None)
                if band and len(value)<=180:
                    key=re.sub(r"\d+","#",value.casefold())
                    repeated[(band,key)].append({"text_index":i,"page":page,"text":value[:180]})
                if hi>h*.75 and re.search(r"\w{2,}[-\u00ad]\s*$",value) and str(page+1) in pages:
                    cross.append({"text_index":i,"page":page,"next_page":page+1,"text":value[-120:]})
            except (TypeError,ValueError): pass
    running=[]
    for (band,key),rows in repeated.items():
        unique=sorted({r["page"] for r in rows})
        if len(unique)>=max(5, math.ceil(len(pages)*0.20)):
            running.append({"band":band,"normalized_text":key,"pages":unique[:100],"count":len(unique),"sample":rows[0]})
    return {"running_furniture":running,"cross_page_word_break":cross}


def _table_cell_ocr_candidates(doc: dict[str, Any]) -> list[dict[str, Any]]:
    out=[]
    for ti,table in enumerate(doc.get("tables") or []):
        page=_page_of(table)
        for ci,cell in enumerate(((table.get("data") or {}).get("table_cells") or [])):
            value=str(cell.get("text") or "")
            reasons=[]
            if REPLACEMENT_RE.search(value): reasons.append("unicode_replacement_character")
            if CONTROL_RE.search(value): reasons.append("control_character")
            if DIGIT_LOWER_L_RE.search(value): reasons.append("lowercase_l_inside_numeric_token")
            if OCR_UNIT_RE.search(value) or LOWER_L_UNIT_RE.search(value): reasons.append("unit_like_ocr_confusion")
            for anomaly in _unicode_anomalies(value):
                if anomaly["kind"] in {"private_use_character","unicode_noncharacter","replacement_character","control_or_surrogate_character"}:
                    reasons.append(anomaly["kind"])
            reasons.extend(x for x in _generic_ocr_garble_reasons(value) if x not in reasons)
            if reasons:
                out.append({"table_index":ti,"cell_index":ci,"page":page,"reasons":sorted(set(reasons)),"text":value[:300],"contains_technical_value":bool(TECH_VALUE_RE.search(value) or INEQUALITY_RE.search(value))})
    return out


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

        document, json_name = select_docling_document(archive)

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
            "ocr_confusion_patterns": "structural OCR confusions plus generic same-document consistency",
            "note": "A clean result means no configured pattern matched. Same-document OCR recall uses no external dictionary and does not auto-correct text.",
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
    total_headings = int(heading_check.get("total_section_headings") or 0)
    evaluable_headings = int(heading_check.get("evaluable_headings") or 0)
    if total_headings >= 50 and evaluable_headings / max(1, total_headings) < 0.10:
        coverage_warnings.append({
            "check": "unnumbered_heading_hierarchy", "status": "limited",
            "checked": evaluable_headings, "total": total_headings,
            "reason": "most section headings do not use a supported numbering scheme; hierarchy correctness is not established",
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

    # Generic Docling-JSON integrity checks. These do not rewrite source data.
    json_quality = _docling_integrity_findings(doc)
    structural_json_count = 0
    if json_quality["empty_text"]:
        structural_json_count += len(json_quality["empty_text"])
        signals.append({
            "code": "EMPTY_TEXT_ITEM", "classification": "INFO", "severity": "medium",
            "count": len(json_quality["empty_text"]), "items": json_quality["empty_text"],
            "samples": json_quality["empty_text"][:50],
            "note": "Empty Docling text nodes are structural extraction defects/candidates; source remains immutable.",
        })
    if json_quality["geometry"]:
        structural_json_count += len(json_quality["geometry"])
        signals.append({
            "code": "DOCLING_GEOMETRY_ANOMALY", "classification": "HUMAN_REVIEW", "severity": "medium",
            "action": "preserve_source_and_avoid_unreliable_target_crop",
            "count": len(json_quality["geometry"]), "items": json_quality["geometry"],
            "samples": json_quality["geometry"][:50],
            "note": "Missing/degenerate/out-of-page geometry can make source-image target verification unsafe.",
        })
    if json_quality["unicode"]:
        structural_json_count += len(json_quality["unicode"])
        signals.append({
            "code": "UNICODE_ENCODING_ANOMALY", "classification": "TEXT_REVIEW", "severity": "medium",
            "count": len(json_quality["unicode"]), "items": json_quality["unicode"],
            "samples": json_quality["unicode"][:50],
            "note": "Replacement/private-use/control/noncharacter/placeholder glyphs are evidence only; never normalize automatically.",
        })
    if json_quality["mixed_script"]:
        signals.append({
            "code": "MIXED_SCRIPT_TOKEN", "classification": "TEXT_REVIEW", "severity": "medium",
            "count": len(json_quality["mixed_script"]), "items": json_quality["mixed_script"],
            "samples": json_quality["mixed_script"][:50],
            "note": "Latin mixed with Greek/Cyrillic may be OCR confusion or intentional notation; verify from source crop.",
        })
    if json_quality["table_grid"]:
        structural_json_count += len(json_quality["table_grid"])
        signals.append({
            "code": "TABLE_GRID_ANOMALY", "classification": "HUMAN_REVIEW", "severity": "medium",
            "action": "review_table_grid_before_chunking",
            "count": len(json_quality["table_grid"]), "items": json_quality["table_grid"],
            "samples": json_quality["table_grid"][:40],
            "note": "Invalid spans, overlapping logical cells, or extremely sparse tables can corrupt troubleshooting chunks.",
        })
    graph_items = json_quality["broken_reference"] + json_quality["reference_cycle"] + json_quality["unreachable_text"]
    if graph_items:
        structural_json_count += len(graph_items)
        signals.append({
            "code": "DOCLING_GRAPH_INTEGRITY", "classification": "HUMAN_REVIEW", "severity": "high",
            "action": "review_broken_or_unreachable_docling_structure",
            "count": len(graph_items), "items": graph_items, "samples": graph_items[:50],
            "note": "Broken references/cycles/unreachable text can make document structure and chunking incomplete.",
        })
    if structural_json_count:
        coverage_warnings.append({
            "check": "docling_json_integrity", "status": "warning", "count": structural_json_count,
            "reason": "structural JSON defects were found; zero OCR anomalies would not constitute a clean result",
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

    layout_audit = _layout_audit_findings(doc)
    if layout_audit["running_furniture"]:
        signals.append({
            "code": "GEOMETRY_RUNNING_FURNITURE", "classification": "RULE_FIX", "severity": "info",
            "action": "exclude_repeated_margin_furniture_from_semantic_chunks",
            "count": len(layout_audit["running_furniture"]), "items": layout_audit["running_furniture"],
            "samples": layout_audit["running_furniture"][:30],
            "note": "Requires repeated text in a stable top/bottom page band; source is preserved.",
        })
    if layout_audit["cross_page_word_break"]:
        signals.append({
            "code": "CROSS_PAGE_WORD_BREAK", "classification": "INFO", "severity": "info",
            "count": len(layout_audit["cross_page_word_break"]), "items": layout_audit["cross_page_word_break"],
            "samples": layout_audit["cross_page_word_break"][:30],
            "note": "Page-boundary hyphenation is retrieval/chunking evidence, not an automatic OCR correction.",
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
        if re.search(r"(?<!\w)\d+[OoIl]\d+(?!\w)", value):
            reasons.append("numeric_letter_ambiguity")
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

    # Generic OCR-recall pass. Unlike the structural detector above, this pass
    # learns only from repeated/frequent text inside the current document. A
    # rare near-match or split token becomes a Pi5 candidate, never a rewrite.
    structural_indices = {int(item["text_index"]) for item in suspicious_texts if item.get("text_index") is not None}
    recall_all = _document_ocr_recall_candidates(texts, config, structural_indices)
    recall_eligible = len(recall_all)
    strong_recall = [x for x in recall_all if x.get("routing_eligible")]
    weak_recall = [x for x in recall_all if not x.get("routing_eligible")]
    recall_candidates = (strong_recall + weak_recall)[: int(config.stage2a_ocr_recall_max_candidates)]
    recall_stats = {
        "eligible_candidates": recall_eligible,
        "emitted": len(recall_candidates),
        "omitted_due_to_cap": max(0, recall_eligible - len(recall_candidates)),
        "strong_candidates": len(strong_recall),
        "weak_candidates": len(weak_recall),
        "cap_reached": recall_eligible > len(recall_candidates),
    }
    if recall_candidates:
        signals.append({
            "code": "DOCUMENT_INTERNAL_OCR_RECALL",
            "classification": "TEXT_REVIEW",
            "severity": "high" if any(x["contains_technical_value"] for x in recall_candidates) else "medium",
            "count": len(recall_candidates),
            "items": recall_candidates,
            "samples": recall_candidates[:50],
            "note": "Generic same-document consistency candidates. Strong candidates route normally; isolated lexical near-matches remain low-priority source-image verification candidates, never corrections by similarity alone.",
            "policy": {
                "manufacturer_specific_rules": False,
                "external_dictionary": False,
                "common_min_count": config.stage2a_ocr_recall_common_min_count,
                "rare_max_count": config.stage2a_ocr_recall_rare_max_count,
                "candidate_cap": config.stage2a_ocr_recall_max_candidates,
                **recall_stats,
            },
        })
    if recall_stats["cap_reached"]:
        coverage_warnings.append({
            "check": "ocr_document_consistency_scan", "status": "truncated",
            "count": recall_stats["omitted_due_to_cap"],
            "reason": "document-internal OCR recall produced more candidates than the configured cap",
        })

    table_cell_ocr = _table_cell_ocr_candidates(doc)
    if table_cell_ocr:
        signals.append({
            "code": "SUSPICIOUS_TABLE_CELL_OCR", "classification": "TEXT_REVIEW",
            "severity": "high" if any(x["contains_technical_value"] for x in table_cell_ocr) else "medium",
            "count": len(table_cell_ocr), "items": table_cell_ocr, "samples": table_cell_ocr[:50],
            "note": "Table cells are part of the OCR quality universe; verification must crop the exact cell from its parent page.",
        })

    # Document-internal lexical recall across table cells as well as ordinary text.
    flat_cells=[]; cell_map=[]
    for ti,table in enumerate(tables):
        page=_page_of(table)
        for ci,cell in enumerate(((table.get("data") or {}).get("table_cells") or [])):
            flat_cells.append({"text": cell.get("text") or "", "label": "table_cell", "prov": [{"page_no": page}] if page else []})
            cell_map.append((ti,ci,page))
    table_recall_all = _document_ocr_recall_candidates(flat_cells, config, set()) if flat_cells else []
    table_recall=[]
    for item in table_recall_all:
        fi=int(item.get("text_index")); ti,ci,page=cell_map[fi]
        mapped=dict(item); mapped.pop("text_index",None); mapped.update({"table_index":ti,"cell_index":ci,"page":page})
        table_recall.append(mapped)
    table_recall.sort(key=lambda x:(bool(x.get("routing_eligible")), bool(x.get("contains_technical_value")), float(x.get("recall_score") or 0)), reverse=True)
    table_recall=table_recall[: int(config.stage2a_ocr_recall_max_candidates)]
    if table_recall:
        signals.append({
            "code": "DOCUMENT_INTERNAL_TABLE_OCR_RECALL", "classification": "TEXT_REVIEW",
            "severity": "high" if any(x["contains_technical_value"] for x in table_recall) else "medium",
            "count": len(table_recall), "items": table_recall, "samples": table_recall[:50],
            "note": "Same-document lexical evidence inside table cells; source-image verification only, never lexical auto-correction.",
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

    coverage_status = "warning" if any(w.get("status") in {"warning", "truncated"} for w in coverage_warnings) or (integrity and integrity.get("status") != "ok") else ("limited" if coverage_warnings else "ok")
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
                "pattern_scope": "structural OCR corruption patterns; a clean result does not prove lexical correctness",
                "structural_candidates": len(suspicious_texts),
            },
            "ocr_document_consistency_scan": {
                "status": "scanned" if config.stage2a_ocr_recall_enabled else "disabled",
                "display_label": "Document consistency checked" if config.stage2a_ocr_recall_enabled else "Disabled",
                "checked": len(texts) if config.stage2a_ocr_recall_enabled else 0,
                "total": len(texts),
                "coverage_ratio": 1.0 if (texts and config.stage2a_ocr_recall_enabled) else None,
                "candidates": len(recall_candidates),
                "candidate_cap": config.stage2a_ocr_recall_max_candidates,
                "eligible_candidates": recall_stats.get("eligible_candidates", 0),
                "omitted_due_to_cap": recall_stats.get("omitted_due_to_cap", 0),
                "strong_candidates": recall_stats.get("strong_candidates", 0),
                "weak_candidates": recall_stats.get("weak_candidates", 0),
                "pattern_scope": "same-document token/repeated-block consistency; no external dictionary or manufacturer rules",
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
            "docling_json_integrity": {
                "status": "warning" if structural_json_count else "ok",
                "display_label": "Needs attention" if structural_json_count else "Good",
                "defects": structural_json_count,
                "empty_text_items": len(json_quality.get("empty_text") or []),
                "geometry_anomalies": len(json_quality.get("geometry") or []),
                "unicode_anomalies": len(json_quality.get("unicode") or []),
                "table_grid_anomalies": len(json_quality.get("table_grid") or []),
                "graph_integrity_anomalies": len(graph_items),
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


def merge_source_crosscheck(diagnostics: dict[str, Any], crosscheck: dict[str, Any]) -> dict[str, Any]:
    if not crosscheck or crosscheck.get("status") == "unavailable":
        return diagnostics
    checks = diagnostics.setdefault("coverage", {}).setdefault("checks", {})
    findings = list(crosscheck.get("findings") or [])
    checks["source_pdf_crosscheck"] = {
        "status": crosscheck.get("status"),
        "pdf_pages": crosscheck.get("pdf_pages"),
        "json_pages": crosscheck.get("json_pages"),
        "pages_compared": crosscheck.get("pages_compared", 0),
        "findings": len(findings),
    }
    if findings:
        diagnostics.setdefault("signals", []).append({
            "code": "SOURCE_PDF_CROSSCHECK", "classification": "INFO", "severity": "medium",
            "count": len(findings), "items": findings, "samples": findings[:50],
            "note": "Original-PDF comparison is deterministic corroboration only; PDF text layers are not treated as ground truth.",
        })
        warnings = diagnostics["coverage"].setdefault("warnings", [])
        warnings.append({"check":"source_pdf_crosscheck","status":"warning","count":len(findings),"reason":"source PDF and Docling JSON differ under deterministic coverage/geometry checks"})
        diagnostics["coverage"]["overall_status"] = "warning"
        diagnostics["coverage"]["overall_display_label"] = display_label("warning", kind="coverage")
        summary=diagnostics.setdefault("summary",{})
        summary["validation_coverage_status"]="warning"
        summary["validation_coverage_display_label"]=diagnostics["coverage"]["overall_display_label"]
        summary["validation_coverage_warnings"]=len(warnings)
    return diagnostics


def build_routes(doc: dict[str, Any], diagnostics: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    route_keys: set[tuple[Any, ...]] = set()

    def add(target: str, code: str, priority: str, source: dict[str, Any], action: str, reason: str) -> None:
        key = (target, source.get("type"), source.get("index"), source.get("table_index"), source.get("cell_index"), source.get("page"))
        if key in route_keys:
            for existing in routes:
                es = existing.get("source") or {}
                if (existing.get("target"), es.get("type"), es.get("index"), es.get("table_index"), es.get("cell_index"), es.get("page")) == key:
                    related = existing.setdefault("related_codes", [])
                    if code != existing.get("code") and code not in related:
                        related.append(code)
                    if reason and reason not in str(existing.get("reason") or ""):
                        existing["reason"] = (str(existing.get("reason") or "") + "; " + reason).strip("; ")
                    # Upgrade duplicate priority but never downgrade it.
                    rank={"high":3,"medium":2,"low":1,"info":0}
                    if rank.get(priority,0) > rank.get(str(existing.get("priority")),0): existing["priority"] = priority
                    return
        if len(routes) >= config.max_routes_per_document:
            return
        route_keys.add(key)
        routes.append({
            "route_id": "",
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
                routing_eligible = True
                if code in {"DOCUMENT_INTERNAL_OCR_RECALL", "DOCUMENT_INTERNAL_TABLE_OCR_RECALL"}:
                    routing_eligible = bool(sample.get("routing_eligible"))
                if sample.get("table_index") is not None and sample.get("cell_index") is not None:
                    source = {"type": "table_cell", "table_index": sample.get("table_index"), "cell_index": sample.get("cell_index"), "page": sample.get("page")}
                else:
                    source = {"type": "text", "index": sample.get("text_index"), "page": sample.get("page")}
                if sample.get("evidence"):
                    source["ocr_recall_evidence"] = sample.get("evidence")[:8]
                    source["ocr_recall_score"] = sample.get("recall_score")
                add(
                    "pi5",
                    code,
                    ("high" if sample.get("contains_technical_value") else ("medium" if routing_eligible else "low")),
                    source,
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

    priority_rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    routes.sort(key=lambda r: (priority_rank.get(str(r.get("priority")), 9), 0 if r.get("target") == "pi5" else 1))
    for i, route in enumerate(routes, 1):
        route["route_id"] = f"R{i:05d}"
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
        groq_quota: GroqQuotaGuard | None = None,
    ) -> None:
        self._config_getter = config_getter
        self._store = store
        self._events = events
        self._groq_quota = groq_quota
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
        self._cloud_health_last_check_monotonic = 0.0
        self._cloud_health_cached = None

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
            conversion_job = await self._store.get_conversion_job(job["conversion_job_id"])
            source_filename = str((conversion_job or {}).get("filename") or (conversion_job or {}).get("source_filename") or job.get("source_filename") or "")
            source_path = Path(config.input_dir) / Path(source_filename).name if source_filename else None
            crosscheck = await asyncio.to_thread(_source_pdf_crosscheck, doc, source_path)
            diagnostics = merge_source_crosscheck(diagnostics, crosscheck)
            routes = await asyncio.to_thread(build_routes, doc, diagnostics, config)
            routes["analysis_run"] = int(job.get("rerun_count") or 0)
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
            result_dir = Path(config.processed_dir) / f"{stem}__job{job['conversion_job_id']}__run{job.get('rerun_count', 0)}"
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
            previous_entries: list[dict[str, Any]] = []
            previous_dir = Path(config.processed_dir) / Path(str(job.get("result_dir") or result_dir.name)).name
            previous_ledger_path = previous_dir / "correction_ledger.json"
            if previous_ledger_path.is_file():
                try:
                    previous_payload = json.loads(previous_ledger_path.read_text(encoding="utf-8"))
                    for old_entry in previous_payload.get("entries") or []:
                        item = dict(old_entry)
                        if item.get("status") != "superseded":
                            item["previous_status"] = item.get("status")
                            item["status"] = "superseded"
                            item["status_reason"] = "STAGE2A_GENERATION_RERUN"
                        previous_entries.append(item)
                except (OSError, json.JSONDecodeError, TypeError):
                    previous_entries = []
            ledger = {
                "schema": "docling-correction-ledger/v2",
                "source_zip_sha256": output_sha,
                "policy": "Raw Docling output is immutable. Stage 2C may append auditable corrections/enrichment; only applied entries feed chunk overlays.",
                "rule_version": "stage2c-structural-v1",
                "entries": previous_entries,
            }
            await asyncio.to_thread(_json_atomic, result_dir / "source_manifest.json", manifest)
            await asyncio.to_thread(_json_atomic, result_dir / "integrity.json", integrity)
            await asyncio.to_thread(_json_atomic, result_dir / "coverage.json", diagnostics["coverage"])
            await asyncio.to_thread(_json_atomic, result_dir / "profile.json", profile)
            await asyncio.to_thread(_json_atomic, result_dir / "diagnostics.json", diagnostics)
            await asyncio.to_thread(_json_atomic, result_dir / "routes.json", routes)
            await asyncio.to_thread(_json_atomic, result_dir / "correction_ledger.json", ledger)
            await asyncio.to_thread(rebuild_chunk_overlays, result_dir, previous_entries)
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
        """Monitor the processors selected for the Text and Vision roles.

        Pi5 and OnePlus are symmetric local vision endpoints. Groq is reported
        as configured without spending health-check tokens. The role keys stay
        ``pi5`` (Text) and ``oneplus`` (Vision) for database/UI compatibility.
        """
        from .verifier_clients import EndpointHealth

        async def selected_health(config, provider: str) -> EndpointHealth:
            provider = str(provider or "").lower()
            if provider == "groq":
                key = os.environ.get(str(config.text_cloud_api_key_env), "").strip()
                return EndpointHealth(
                    bool(key), model=str(config.vision_cloud_model),
                    detail=("Groq Vision configured · explicit selection" if key else
                            f"{config.text_cloud_api_key_env} not configured"),
                )
            if provider == "pi5":
                return await OpenAICompatibleVerifier(config.pi5_url).health()
            if provider == "oneplus":
                return await OpenAICompatibleVerifier(config.oneplus_url).health()
            return EndpointHealth(False, detail=f"Unknown provider: {provider}")

        while not self._stopping.is_set():
            config = self._config_getter()
            try:
                text_provider = str(getattr(config, "text_verifier_provider", "pi5"))
                vision_provider = str(getattr(config, "vision_verifier_provider", "oneplus"))
                text_health, vision_health = await asyncio.gather(
                    selected_health(config, text_provider),
                    selected_health(config, vision_provider),
                )
                self.verifier_status = {
                    "pi5": {
                        "reachable": text_health.reachable,
                        "model": text_health.model,
                        "detail": text_health.detail,
                        "provider": text_provider,
                    },
                    "oneplus": {
                        "reachable": vision_health.reachable,
                        "model": vision_health.model,
                        "detail": vision_health.detail,
                        "provider": vision_provider,
                    },
                }
            except Exception as exc:
                self.verifier_status = {
                    "pi5": {"reachable": False, "model": None, "detail": str(exc)},
                    "oneplus": {"reachable": False, "model": None, "detail": str(exc)},
                }
            await asyncio.sleep(config.verifier_health_interval_seconds)
