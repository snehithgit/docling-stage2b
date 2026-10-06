"""Source-bound technical evidence detection. Derived records never edit corrections."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections import Counter
from pathlib import Path
from .technical_context import body_text, callout_kind, context_links, FIGURE
from .evidence_contract import SCHEMA, normalized_record, normalize_ledger, source_signature, atomic_json, backup_legacy_ledger

RULE_VERSION = "technical-evidence-v2"
CATEGORIES = {
    "troubleshooting": r"trouble[ -]?shoot|fault finding|probable cause|causes and remedies",
    "alarm": r"\balarm\b|fault code|pilot lamp|indicator light|high oil temp",
    "diagnostic_flowchart": r"flow[ -]?chart|decision tree",
    "operation": r"operating instructions|starting procedure|operation and stop|\bto cool\b|set the switch|stop unit operation",
    "technical_note": r"(?m)^\s*(?:n\s*\.\s*b\s*\.|note\s*:)" ,
    "technical_artifact": r"engineering drawing|technical image|component photo",
    "maintenance_repair": r"maintenance|repair procedure|disassembly|reassembl|replacement",
    "specification": r"technical data|specifications|tightening torque|rated pressure",
    "safety_interlock": r"\bwarning\b|\bcaution\b|interlock|safety functions|do not operate",
    "parts": r"spare[ -]?parts|article no|part number|parts list",
    "circuit": r"wiring|circuit diagram|hydraulic circuit|schematic",
    "inspection_test": r"inspection|test procedure|acceptance test|pressure test",
    "maintenance_schedule": r"maintenance schedule|service interval|every \d+|daily inspection",
    "component_description": r"construction|operating principle|function description|purpose",
    "applicability_reference": r"applicable models|refer to section|see instruction|model variants",
}
ALIASES = {
    "troubleshooting": ["fault diagnosis", "possible cause", "remedy", "why not working"],
    "diagnostic_flowchart": ["diagnostic decision tree", "fault cause remedy"],
    "alarm": ["alarm meaning", "trip condition", "fault code"],
    "parts": ["spare part", "part number", "replacement component"],
}
PATTERNS = {key: re.compile(value, re.I) for key, value in CATEGORIES.items()}


def question_categories(query: str) -> set[str]:
    categories = {key for key, pattern in PATTERNS.items() if pattern.search(query)}
    if re.search(r"\bwhy\b|reason|cause|not working|overheat|temperature high", query, re.I):
        categories.update(("troubleshooting", "diagnostic_flowchart"))
    if re.search(r"\balarm\b|fault code", query, re.I):
        categories.update(("alarm", "troubleshooting"))
    if re.search(r"replace|repair|service", query, re.I):
        categories.add("maintenance_repair")
    return categories


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_fault_table(text: str) -> list[dict]:
    """Bind explicit columns only; never infer column roles from numeric position."""
    columns = None
    column_count = 0
    records = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = _cells(line)
        roles = {}
        for i, cell in enumerate(cells):
            lowered = cell.casefold()
            if re.fullmatch(r"(?:fault|symptom|problem|trouble)", lowered): roles["symptom"] = i
            if re.fullmatch(r"(?:probable |possible )?cause(?:s)?", lowered): roles["cause"] = i
            if re.fullmatch(r"remed(?:y|ies)|corrective action|action", lowered): roles["remedy"] = i
        if len(roles) == 3:
            columns = roles
            column_count = len(cells)
            continue
        if not columns or len(cells) != column_count or all(re.fullmatch(r"[-: ]*", c) for c in cells):
            continue
        values = {key: cells[index] for key, index in columns.items()}
        # Blank merged cells/continuations need explicit recovery; do not borrow
        # the preceding fault and accidentally connect independent branches.
        if not all(values.values()):
            continue
        records.append({**values, "source_quote": line, "validation_status": "source_bound"})
    return records


def detect_record(row: dict) -> dict | None:
    text = str(row.get("text") or "")
    headings = list(row.get("headings") or [])
    body = body_text(row)
    deepest = str(headings[-1]) if headings else ""
    categories = [key for key, pattern in PATTERNS.items() if pattern.search(body)]
    basis = "literal_body"
    kind = callout_kind(row)
    if kind:
        category = "technical_note" if kind == "note" else "safety_interlock"
        if category not in categories:
            categories.append(category)
    if not categories:
        categories = [key for key, pattern in PATTERNS.items() if pattern.search(deepest)]
        basis = "deepest_source_heading"
    elif len(re.findall(r"\w+", deepest)) >= 3:
        for key, pattern in PATTERNS.items():
            if key not in categories and pattern.search(deepest):
                categories.append(key)
    if FIGURE.search(body + " " + deepest) and not categories:
        categories = ["component_description"]
        basis = "literal_caption"
    picture = any(str(ref).startswith("#/pictures/") for ref in row.get("doc_items") or [])
    if not categories and picture and not re.fullmatch(r"(?:icon|logo|decorative image)[.!]?", body, re.I):
        categories = ["technical_artifact"]
        basis = "unclassified_source_picture"
    if not categories:
        return None
    graph = bool(re.search(r"flow[ -]?chart", text, re.I))
    visual_required = graph or picture
    literal_refs = any(str(ref).startswith(("#/texts/", "#/tables/")) for ref in row.get("doc_items") or [])
    literal_available = bool(literal_refs and not graph and (len(re.findall(r"\w+", body)) >= 5 or ("|" in body and len(re.findall(r"\w+", body)) >= 3)))
    if graph and "diagnostic_flowchart" not in categories:
        categories.append("diagnostic_flowchart")
    provenance_valid = bool(row.get("source_filename") and isinstance(row.get("postprocess_job_id"), int) and not isinstance(row["postprocess_job_id"], bool) and row["postprocess_job_id"] > 0 and row.get("page_numbers") and all(isinstance(page, int) and not isinstance(page, bool) and page > 0 for page in row.get("page_numbers", [])) and row.get("doc_items"))
    status = "missing_provenance" if not provenance_valid else "needs_visual_parse" if visual_required else "source_bound"
    relations = [] if visual_required else parse_fault_table(text)
    fingerprint = hashlib.sha256(text.encode()).hexdigest()
    identity = json.dumps([row.get("postprocess_job_id"), row.get("chunk_id"), fingerprint, row.get("page_numbers"), row.get("doc_items"), RULE_VERSION], separators=(",", ":"))
    title = next((line.strip() for line in body.splitlines() if line.strip()), deepest or "Technical evidence")
    if title.casefold() in {"flow chart", "flowchart", "engineering drawing", "icon", "photograph"} and deepest:
        title = deepest
    if kind and body:
        title = ("Technical note: " if kind == "note" else "Safety warning: ") + body.splitlines()[0].strip()
    title = title[:180]
    terms = sorted({alias for category in categories for alias in ALIASES.get(category, [])})
    # Explicit topic aliases add discoverability, never new source facts.
    if re.search(r"overheat", body + deepest, re.I):
        terms += ["high oil temperature", "oil temperature high", "overheating"] if re.search(r"hydraulic|oil", body + deepest, re.I) else ["overheating"]
    if "operation" in categories and re.search(r"\bcool\b", body, re.I) and re.search(r"oil|hydraulic", body, re.I):
        terms += ["hydraulic oil cooling procedure", "cool overheated oil"]
    return normalized_record({
        "entry_id": "TE-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
        "schema": "technical-evidence-ledger/v1", "rule_version": RULE_VERSION,
        "evidence_types": categories, "source_format": "flowchart" if graph else "mixed" if visual_required and literal_available else "image" if visual_required else "table" if "|" in text else "paragraph",
        "detection_basis": basis, "context_kind": kind,
        "literal_content_available": literal_available,
        "visual_doc_items": [ref for ref in row.get("doc_items") or [] if str(ref).startswith("#/pictures/")],
        "component": None, "model": None, "equipment_scope": "resolved_from_book_assignment_at_query",
        "original_headings": headings, "search_heading": title, "heading_is_derived": True,
        "search_terms": sorted(set(terms)), "source_text": text,
        "source_sha256": fingerprint, "source_chunk_id": row.get("chunk_id"),
        "source_filename": row.get("source_filename"), "postprocess_job_id": row.get("postprocess_job_id"),
        "page_numbers": row.get("page_numbers") or [], "doc_items": row.get("doc_items") or [],
        "validation_status": status, "relationships": relations,
        "relationship_status": "source_bound" if relations else "not_parsed",
        "validation_reason": "diagram_relationships_require_pixel_verification" if visual_required else "literal_source_with_provenance" if provenance_valid else "source_page_or_item_missing",
        "answer_eligible": provenance_valid and not visual_required,
        "human_verified": False,
    })


def annotate_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    output, records = [], []
    for original in rows:
        row = dict(original)
        for field in ("evidence_types", "technical_evidence_id", "technical_validation_status", "search_terms", "search_heading", "technical_context_links", "technical_detection_rule"):
            row.pop(field, None)
        record = detect_record(row)
        if record:
            records.append(record)
            row.update(evidence_types=record["evidence_types"], technical_evidence_id=record["entry_id"],
                       technical_validation_status=record["validation_status"], search_terms=record["search_terms"],
                       search_heading=record["search_heading"])
        output.append(row)
    links = context_links(rows, records)
    by_chunk = {r["source_chunk_id"]: r for r in records}
    for row in output:
        record = by_chunk.get(row.get("chunk_id"))
        if record:
            record["context_links"] = links.get(row["chunk_id"], [])
            row["technical_context_links"] = record["context_links"]
            row["technical_detection_rule"] = RULE_VERSION
    return output, records


def write_evidence_ledger(result_dir: Path, rows: list[dict]) -> dict:
    """Called under the book lifecycle lock. Corrupt existing history is an error."""
    path = result_dir / "technical_evidence_ledger.json"
    existing = {"schema": SCHEMA, "entries": []}
    if path.exists():
        original = path.read_bytes()
        existing = json.loads(original)
        normalized = normalize_ledger(existing)
        if existing["schema"] != SCHEMA:
            backup_legacy_ledger(path, original)
        existing = normalized
        if existing.get("rule_version") != RULE_VERSION:
            digest = hashlib.sha256(original).hexdigest()[:16]
            backup = path.with_name(f"technical_evidence_ledger.pre-v5.0.2.{digest}.json")
            if backup.exists():
                if backup.read_bytes() != original:
                    raise ValueError("Detection upgrade backup mismatch")
            else:
                with backup.open("xb") as handle:
                    handle.write(original)
    _, records = annotate_rows(rows)
    previous = {record["entry_id"]: record for record in existing["entries"] if not record.get("superseded")}
    # Same source identity + parser version preserves every reviewed field.
    # A changed source/parser gets a new ID and supersedes its old history.
    previous_sources = {(r.get("source_chunk_id"), r.get("source_sha256"), tuple(r.get("page_numbers") or []), tuple(r.get("doc_items") or [])): r for r in previous.values()}
    preserved = []
    for record in records:
        old = previous.get(record["entry_id"])
        if old:
            record = normalized_record({**record, **old, "context_links": record.get("context_links", [])})
        else:
            key = (record.get("source_chunk_id"), record.get("source_sha256"), tuple(record.get("page_numbers") or []), tuple(record.get("doc_items") or []))
            old = previous_sources.get(key)
            if old:
                record = {**old, **record, "previous_evidence_id": old["entry_id"], "previous_validation": old.get("validation")}
                for field in ("human_verified", "human_decision", "reviewer_note", "review_metadata"):
                    if field in old:
                        record[field] = old[field]
                if old.get("validation", {}).get("state") == "rejected":
                    record["validation"] = old["validation"]
                record = normalized_record(record)
        preserved.append(record)
    records = preserved
    current_sources = {(r.get("chunk_id"), hashlib.sha256(str(r.get("text") or "").encode()).hexdigest(), tuple(r.get("page_numbers") or []), tuple(r.get("doc_items") or [])) for r in rows}
    detected_sources = {(r.get("source_chunk_id"), r.get("source_sha256"), tuple(r.get("page_numbers") or []), tuple(r.get("doc_items") or [])) for r in records}
    for old in previous.values():
        key = (old.get("source_chunk_id"), old.get("source_sha256"), tuple(old.get("page_numbers") or []), tuple(old.get("doc_items") or []))
        # Changing a classification must never resurrect a rejected source.
        if old.get("validation", {}).get("state") == "rejected" and key in current_sources and key not in detected_sources:
            records.append(old)
    active = {r["entry_id"] for r in records}
    history = [normalized_record({**r, "superseded": True}) for r in existing["entries"] if r.get("entry_id") not in active]
    payload = {**existing, "schema": SCHEMA, "rule_version": RULE_VERSION,
               "updated_at_epoch": time.time(), "entries": records + history,
               "summary": dict(Counter(r["validation"]["state"] for r in records)),
               "source_index_signature": source_signature(rows),
               "raw_docling_immutable": True}
    if existing.get("migration"):
        payload["migration"] = existing["migration"]
    atomic_json(path, payload)
    return payload["summary"]


def category_bonus(row: dict, query: str) -> float:
    # Candidate diagrams locate source pages; they cannot become answer facts.
    types = set(row.get("evidence_types") or [])
    return 3.0 if types & question_categories(query) else 0.0
