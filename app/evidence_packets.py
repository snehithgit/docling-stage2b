"""Bind generation candidates to current technical ledgers, without model calls."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .evidence_contract import normalize_ledger, is_validated_record, evidence_coverage


@lru_cache(maxsize=64)
def _records(path: str, mtime: int, size: int) -> tuple[dict, ...]:
    data = normalize_ledger(json.loads(Path(path).read_text(encoding="utf-8")))
    return tuple(record for record in data["entries"] if not record.get("superseded"))


def bind_evidence(results: list[dict], visuals: list[dict], books: list[dict], processed_dir: Path) -> tuple[list[dict], list[dict]]:
    """Only server-selected book paths can supply validation, never request fields."""
    ledgers = {}
    for book in books:
        if not book.get("result_dir"):
            continue
        path = processed_dir / Path(str(book["result_dir"])).name / "technical_evidence_ledger.json"
        try:
            stat = path.stat()
            records = _records(str(path), stat.st_mtime_ns, stat.st_size)
        except (OSError, ValueError, TypeError, KeyError):
            records = ()
        current = evidence_coverage(path.parent).get("source_current", False)
        ledgers[int(book["postprocess_job_id"])] = (book, records, current)

    def records_for(row):
        # Result-directory binding supports historic indexes with old job IDs.
        result = str(row.get("result_dir") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if result:
            matches = [(records, current) for book, records, current in ledgers.values() if Path(str(book["result_dir"])).name == result]
            if len(matches) == 1:
                return matches[0]
        try:
            entry = ledgers.get(int(row.get("postprocess_job_id") or 0), ({}, (), False))
            return entry[1], entry[2]
        except (TypeError, ValueError):
            return (), False

    def bind(row, visual=False):
        copy = dict(row)
        copy.pop("technical_evidence", None)
        copy.pop("technical_source_current", None)
        copy.pop("generation_blocked_reason", None)
        records, current = records_for(row)
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
            copy["context_neighbors"] = [bind({**n, "postprocess_job_id": n.get("postprocess_job_id", row.get("postprocess_job_id")), "result_dir": n.get("result_dir", row.get("result_dir"))}) for n in row.get("context_neighbors") or [] if isinstance(n, dict)]
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
    if record.get("validation_status") == "needs_visual_parse":
        return False, "visual_relationships_unvalidated", None
    # Extracted paragraphs may be quoted as literal source, but unverified
    # fault/cause/remedy mappings, aliases and interpretations never enter.
    return True, "literal_source_only_unvalidated_extraction", None
