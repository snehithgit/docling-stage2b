"""Source-bound technical evidence detection. Derived records never edit corrections."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections import Counter
from pathlib import Path
from .evidence_contract import SCHEMA, normalized_record, normalize_ledger, source_signature, atomic_json, backup_legacy_ledger

RULE_VERSION = "technical-evidence-v1"
CATEGORIES = {
    "troubleshooting": r"trouble[ -]?shoot|fault finding|probable cause|causes and remedies",
    "alarm": r"\balarm\b|fault code|pilot lamp|indicator light|high oil temp",
    "diagnostic_flowchart": r"flow[ -]?chart|decision tree",
    "operation": r"operating instructions|starting procedure|operation and stop",
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
    context = "\n".join(headings)
    categories = [key for key, pattern in PATTERNS.items() if pattern.search(context + "\n" + text)]
    if not categories:
        return None
    picture = any(str(ref).startswith("#/pictures/") for ref in row.get("doc_items") or [])
    graph = bool(re.search(r"flow[ -]?chart", text, re.I))
    sparse = len(re.findall(r"\w+", text)) < 40
    visual_required = graph or (picture and (sparse or "circuit" in categories))
    if graph and "diagnostic_flowchart" not in categories:
        categories.append("diagnostic_flowchart")
    provenance_valid = bool(row.get("source_filename") and isinstance(row.get("postprocess_job_id"), int) and row["postprocess_job_id"] > 0 and row.get("page_numbers") and all(isinstance(page, int) and page > 0 for page in row.get("page_numbers", [])) and row.get("doc_items"))
    status = "needs_visual_parse" if visual_required else "source_bound" if provenance_valid else "missing_provenance"
    relations = [] if visual_required else parse_fault_table(text)
    fingerprint = hashlib.sha256(text.encode()).hexdigest()
    identity = json.dumps([row.get("postprocess_job_id"), row.get("chunk_id"), fingerprint, row.get("page_numbers"), row.get("doc_items"), RULE_VERSION], separators=(",", ":"))
    title = headings[-1] if headings else next((line for line in text.splitlines() if line.strip()), "Technical evidence")
    terms = sorted({alias for category in categories for alias in ALIASES.get(category, [])})
    # Explicit topic aliases add discoverability, never new source facts.
    if re.search(r"overheat", context + text, re.I):
        terms += ["high oil temperature", "oil temperature high", "overheating"] if re.search(r"hydraulic|oil", context + text, re.I) else ["overheating"]
    return normalized_record({
        "entry_id": "TE-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
        "schema": "technical-evidence-ledger/v1", "rule_version": RULE_VERSION,
        "evidence_types": categories, "source_format": "flowchart" if graph else "image" if visual_required else "table" if "|" in text else "paragraph",
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
        record = detect_record(row)
        if record:
            records.append(record)
            row.update(evidence_types=record["evidence_types"], technical_evidence_id=record["entry_id"],
                       technical_validation_status=record["validation_status"], search_terms=record["search_terms"],
                       search_heading=record["search_heading"])
        output.append(row)
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
    _, records = annotate_rows(rows)
    previous = {record["entry_id"]: record for record in existing["entries"] if not record.get("superseded")}
    # Same source identity + parser version preserves every reviewed field.
    # A changed source/parser gets a new ID and supersedes its old history.
    records = [normalized_record({**record, **previous[record["entry_id"]]}) if record["entry_id"] in previous else record for record in records]
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
