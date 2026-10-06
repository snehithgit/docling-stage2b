"""Bind generation candidates to current technical ledgers, without model calls."""
from __future__ import annotations

import json
import hashlib
import re
from functools import lru_cache
from pathlib import Path

from .evidence_contract import normalize_ledger, is_validated_record, evidence_coverage, read_source_rows


@lru_cache(maxsize=64)
def _records(path: str, mtime: int, size: int) -> tuple[dict, ...]:
    data = normalize_ledger(json.loads(Path(path).read_text(encoding="utf-8")))
    return tuple(record for record in data["entries"] if not record.get("superseded"))


@lru_cache(maxsize=64)
def _source_rows(path: str, mtime: int, size: int) -> dict:
    return {row["chunk_id"]: row for row in read_source_rows(Path(path).parent)}


def bind_evidence(results: list[dict], visuals: list[dict], books: list[dict], processed_dir: Path, output_dir: Path | None = None) -> tuple[list[dict], list[dict]]:
    """Only server-selected book paths can supply validation, never request fields."""
    ledgers = {}
    for book in books:
        if not book.get("result_dir"):
            continue
        path = processed_dir / Path(str(book["result_dir"])).name / "technical_evidence_ledger.json"
        try:
            stat = path.stat()
            records = _records(str(path), stat.st_mtime_ns, stat.st_size)
            from .visual_graph import current_visual_image
            records = tuple({**record, "visual_image_current": current_visual_image(path.parent, output_dir, record["visual_extraction"])}
                            if record.get("visual_extraction") else record for record in records)
        except (OSError, ValueError, TypeError, KeyError):
            records = ()
        current = evidence_coverage(path.parent).get("source_current", False)
        index = path.parent / "retrieval_index.jsonl"
        try:
            stat = index.stat()
            sources = _source_rows(str(index), stat.st_mtime_ns, stat.st_size)
        except (OSError, ValueError, TypeError, KeyError):
            sources = {}
        ledgers[int(book["postprocess_job_id"])] = (book, records, current, sources)

    def records_for(row):
        # Result-directory binding supports historic indexes with old job IDs.
        result = str(row.get("result_dir") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if result:
            matches = [(records, current, sources) for book, records, current, sources in ledgers.values() if Path(str(book["result_dir"])).name == result]
            if len(matches) == 1:
                return matches[0]
        try:
            entry = ledgers.get(int(row.get("postprocess_job_id") or 0), ({}, (), False, {}))
            return entry[1], entry[2], entry[3]
        except (TypeError, ValueError):
            return (), False, {}

    def bind(row, visual=False, expand_context=True):
        copy = dict(row)
        copy.pop("technical_evidence", None)
        copy.pop("technical_source_current", None)
        copy.pop("generation_blocked_reason", None)
        records, current, sources = records_for(row)
        matches = []
        if visual:
            refs = set(row.get("doc_items") or [])
            matches = [r for r in records if refs.intersection(ref for ref in r.get("doc_items", []) if str(ref).startswith("#/pictures/"))]
            if any(r.get("validation", {}).get("state") in {"rejected", "error"} or (r.get("validation_status") == "needs_visual_parse" and (not current or not is_validated_record(r))) for r in matches):
                copy["generation_blocked_reason"] = "visual_relationships_unvalidated"
        else:
            matches = [r for r in records if r.get("source_chunk_id") == row.get("chunk_id")]
            record = matches[0] if len(matches) == 1 else None
            if record:
                same = all(record.get(k) == row.get(v) for k, v in (("source_text", "text"), ("page_numbers", "page_numbers"), ("doc_items", "doc_items")))
                if same:
                    copy["technical_evidence"] = record
                    copy["technical_source_current"] = current
                else:
                    copy["generation_blocked_reason"] = "technical_source_changed"
            elif row.get("technical_evidence_id"):
                copy["generation_blocked_reason"] = "technical_record_unavailable"
        if any(r.get("visual_extraction") and not r.get("visual_image_current") for r in matches):
            copy["generation_blocked_reason"] = "visual_source_image_changed_or_missing"
        if expand_context:
            neighbors = [dict(n) for n in row.get("context_neighbors") or [] if isinstance(n, dict)]
            seen = {n.get("chunk_id") for n in neighbors}
            if current:
                for record in matches:
                    for link in record.get("context_links") or []:
                        target = sources.get(link.get("target_chunk_id"))
                        target_record = next((r for r in records if r.get("source_chunk_id") == link.get("target_chunk_id")), None)
                        if not target or not target_record or target.get("chunk_id") in seen:
                            continue
                        if (link.get("target_source_sha256") != target_record.get("source_sha256")
                                or link.get("target_source_sha256") != hashlib.sha256(str(target.get("text") or "").encode()).hexdigest()
                                or target.get("text") != target_record.get("source_text")
                                or target.get("page_numbers") != link.get("target_page_numbers")
                                or target.get("doc_items") != link.get("target_doc_items")):
                            continue
                        neighbors.append({**target, "postprocess_job_id": row.get("postprocess_job_id"),
                                          "source_filename": row.get("source_filename"), "result_dir": row.get("result_dir"),
                                          "structural_relation": "candidate_" + str(link.get("link_type")), "context_link_status": "candidate"})
                        seen.add(target["chunk_id"])
            copy["context_neighbors"] = [bind({**n, "postprocess_job_id": n.get("postprocess_job_id", row.get("postprocess_job_id")), "result_dir": n.get("result_dir", row.get("result_dir"))}, expand_context=False) for n in neighbors]
        else:
            copy.pop("context_neighbors", None)
        return copy

    return [bind(row) for row in results], [bind(row, True) for row in visuals]


def generation_policy(row: dict) -> tuple[bool, str, dict | None]:
    """Literal text stays usable; derived relationships require validation proof."""
    if row.get("generation_blocked_reason"):
        return False, row["generation_blocked_reason"], None
    record = row.get("technical_evidence")
    if not isinstance(record, dict):
        return True, "literal_source", None
    if record.get("validation_status") == "missing_provenance":
        return False, "technical_provenance_missing", None
    if record.get("validation", {}).get("state") in {"rejected", "superseded", "error"}:
        return False, "technical_record_rejected_or_invalid", None
    if is_validated_record(record) and row.get("technical_source_current"):
        return True, "validated_technical_evidence", record
    mixed_literal = (record.get("source_format") != "flowchart"
                     and any(str(ref).startswith(("#/texts/", "#/tables/")) for ref in record.get("doc_items") or [])
                     and len(re.findall(r"\w+", record.get("source_text") or "")) >= 40)
    mixed_literal = mixed_literal or bool(record.get("literal_content_available") and record.get("source_format") != "flowchart")
    if record.get("validation_status") == "needs_visual_parse" and not mixed_literal:
        return False, "visual_relationships_unvalidated", None
    # Extracted paragraphs may be quoted as literal source, but unverified
    # fault/cause/remedy mappings, aliases and interpretations never enter.
    return True, "literal_source_only_unvalidated_extraction", None
