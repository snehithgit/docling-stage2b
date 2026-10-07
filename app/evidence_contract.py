"""V5 evidence states, non-destructive migration and honest candidate coverage."""
from __future__ import annotations
from .structured_tables import structured_hash

import hashlib
import json
import math
import re
import time
import uuid
from collections import Counter
from functools import lru_cache
from pathlib import Path

SCHEMA = "technical-evidence-ledger/v2"
LEGACY_SCHEMA = "technical-evidence-ledger/v1"
STATES = frozenset({"detected", "queued", "parsing", "extracted", "needs_review", "validated", "rejected", "superseded", "error"})


def source_signature(rows: list[dict]) -> str:
    fields = ("postprocess_job_id", "source_filename", "chunk_id", "text", "page_numbers", "doc_items", "headings")
    source = [{key: row.get(key) for key in fields} for row in rows]
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalized_record(record: dict) -> dict:
    result = dict(record)
    if not isinstance(result.get("entry_id"), str) or not result["entry_id"]:
        raise ValueError("Evidence entry identity is missing")
    links = result.get("context_links") or []
    if not isinstance(links, list) or any(not isinstance(link, dict) for link in links):
        raise ValueError("Malformed technical context links")
    for link in links:
        if (link.get("status") != "candidate"
                or link.get("link_type") not in {"note_for_diagram", "warning_for_diagram", "caption_for_diagram"}
                or link.get("source_chunk_id") != result.get("source_chunk_id")
                or not isinstance(link.get("target_chunk_id"), str) or not link["target_chunk_id"]
                or not isinstance(link.get("target_source_sha256"), str) or len(link["target_source_sha256"]) != 64
                or not isinstance(link.get("target_page_numbers"), list)
                or not isinstance(link.get("target_doc_items"), list)
                or link.get("branch_relationship_verified") is not False):
            raise ValueError("Invalid technical context provenance")
    validation = dict(result.get("validation") or {})
    if validation:
        if validation.get("state") not in STATES:
            raise ValueError("Unknown evidence validation state")
    else:
        legacy = result.get("validation_status")
        state = "extracted" if legacy == "source_bound" else "detected" if legacy == "needs_visual_parse" else "needs_review"
        validation = {"state": state, "provenance_checked": legacy == "source_bound",
                      "relationships_checked": False, "method": None, "actor": None,
                      "validated_at": None, "source_sha256": result.get("source_sha256")}
        if legacy == "needs_visual_parse":
            result["pending_requirements"] = ["visual_parse", "source_relationship_verification"]
        elif legacy == "missing_provenance":
            result["pending_requirements"] = ["source_provenance_recovery"]
    if result.get("superseded"):
        if validation.get("state") != "superseded":
            validation.setdefault("superseded_from_state", validation.get("state"))
        validation["state"] = "superseded"
    result.update(schema=SCHEMA, validation=validation)
    # Preserve original human flags/decisions; they are not proof that this new
    # structured extraction was reviewed. V1 source_bound was provenance only.
    result["answer_eligible"] = is_validated_record(result)
    if validation.get("state") == "validated" and not result["answer_eligible"]:
        validation.update(state="needs_review", reported_state="validated", reason="validation_proof_incomplete")
    return result


def is_validated_record(record: dict) -> bool:
    validation = record.get("validation") or {}
    timestamp = validation.get("validated_at")
    timestamp_valid = isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and math.isfinite(timestamp) and timestamp > 0
    quote_hash = hashlib.sha256(str(record.get("source_text") or "").encode()).hexdigest()
    extraction = record.get("visual_extraction") or {}
    graph = extraction.get("graph") or {}
    visual_valid = (not extraction or (validation.get("visual_graph_checked") is True
        and validation.get("visual_graph_sha256") == extraction.get("graph_sha256") == structured_hash(graph)
        and validation.get("visual_image_sha256") == extraction.get("image_sha256")
        and extraction.get("source_sha256") == record.get("source_sha256")
        and f"#/pictures/{extraction.get('picture_index')}" in record.get("doc_items", [])
        and len([ref for ref in record.get("doc_items", []) if str(ref).startswith("#/pictures/")]) == 1
        and bool(graph.get("nodes")) and not graph.get("unresolved")
        and all(edge.get("direction") != "unknown" for edge in graph.get("edges", []))))
    return bool(visual_valid and not record.get("superseded") and validation.get("state") == "validated"
                and validation.get("method") in {"human", "independent_source_check"}
                and isinstance(validation.get("actor"), str) and validation["actor"].strip() and timestamp_valid
                and validation.get("provenance_checked")
                and record.get("source_sha256") == quote_hash
                and validation.get("source_sha256") == record.get("source_sha256")
                and (not record.get("relationships") or validation.get("relationships_checked"))
                and (not record.get("structured_records") or (validation.get("structured_fields_checked")
                     and validation.get("structured_sha256") == record.get("structured_sha256")
                     and record.get("structured_sha256") == structured_hash(record["structured_records"]))))


def normalize_ledger(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("schema") not in {SCHEMA, LEGACY_SCHEMA} or not isinstance(data.get("entries"), list):
        raise ValueError("Unsupported or malformed technical evidence ledger")
    if any(not isinstance(record, dict) for record in data["entries"]):
        raise ValueError("Malformed evidence entry")
    entries = [normalized_record(record) for record in data["entries"]]
    active_ids = [record["entry_id"] for record in entries if not record.get("superseded")]
    if len(set(active_ids)) != len(active_ids):
        raise ValueError("Duplicate active evidence identity")
    return {**data, "schema": SCHEMA, "entries": entries}


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Malformed source index: {path.name}")
    return rows


def _technical_visual_source_rows(result_dir: Path) -> list[dict]:
    """Expose current Stage 2C technical visuals as source-bound graph candidates.

    visual_evidence.jsonl is derived from the authoritative Stage 2C correction
    ledger and carries exact Docling picture/page provenance. Decorative images
    stay excluded; unresolved technical visuals remain candidates because graph
    extraction + human pixel review is precisely how they are resolved.
    """
    output: list[dict] = []
    for row in _read_jsonl(result_dir / "visual_evidence.jsonl"):
        ref = str(row.get("docling_ref") or "")
        if not re.fullmatch(r"#/pictures/\d+", ref):
            continue
        human = str(row.get("human_visual_decision") or "").strip().lower()
        verdict = str(row.get("verification_verdict") or "").strip().upper()
        if human in {"decorative", "not_useful"}:
            continue
        if human not in {"technical", "useful"} and verdict != "TECHNICAL_USEFUL":
            continue
        page = row.get("source_page", row.get("page"))
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = None
        text = str(row.get("search_text") or "").strip()
        if not text:
            parts = [
                str(row.get("category") or "").replace("_", " "),
                *(str(value) for value in (row.get("visible_text") or [])),
                *(str(value) for value in (row.get("visible_objects") or [])),
                str(row.get("summary") or ""),
            ]
            text = "\n".join(part.strip() for part in parts if part and part.strip()).strip()
        if not text:
            text = "Technical image"
        chunk_id = str(row.get("visual_evidence_id") or "").strip()
        if not chunk_id:
            continue
        output.append({
            "postprocess_job_id": row.get("postprocess_job_id"),
            "source_filename": row.get("source_filename") or row.get("book"),
            "chunk_id": chunk_id,
            "text": text,
            "page_numbers": [page] if page and page > 0 else [],
            "doc_items": [ref],
            "headings": [],
            "content_type": "visual_evidence",
            "visual_evidence_id": chunk_id,
            "picture_index": row.get("picture_index"),
            "verification_verdict": row.get("verification_verdict"),
            "human_visual_decision": row.get("human_visual_decision"),
            "visual_source": True,
        })
    return output


def read_source_rows(result_dir: Path) -> list[dict]:
    """Return the complete technical-evidence source set.

    Text/table chunks come from retrieval_index.jsonl. Technical pictures come
    from visual_evidence.jsonl so Artifact Audit decisions and graph review share
    the same Docling picture provenance instead of becoming disconnected silos.
    """
    rows = _read_jsonl(result_dir / "retrieval_index.jsonl")
    rows.extend(_technical_visual_source_rows(result_dir))
    if any(not isinstance(row.get("chunk_id"), str) or not row["chunk_id"] for row in rows):
        raise ValueError("Malformed technical evidence source identity")
    if len({row["chunk_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate technical evidence source identity")
    return rows


def backup_legacy_ledger(path: Path, original: bytes) -> Path:
    digest = hashlib.sha256(original).hexdigest()
    backup = path.with_name(f"technical_evidence_ledger.v1.{digest[:16]}.json")
    if backup.exists():
        if backup.read_bytes() != original:
            raise ValueError("Evidence migration backup mismatch")
    else:
        with backup.open("xb") as handle:
            handle.write(original)
    return backup


def migrate_ledger(result_dir: Path) -> dict:
    """Caller holds the book lifecycle lock. Only the derived ledger is changed."""
    path = result_dir / "technical_evidence_ledger.json"
    original = path.read_bytes()
    data = json.loads(original)
    converted = normalize_ledger(data)  # fail before any mutation on corruption
    if data["schema"] == SCHEMA:
        return {"changed": False, "schema": SCHEMA}
    rows = read_source_rows(result_dir)
    by_id = {row["chunk_id"]: row for row in rows}
    for entry in converted["entries"]:
        if entry.get("superseded"):
            continue
        source = by_id.get(entry.get("source_chunk_id"))
        if not source or source.get("text") != entry.get("source_text") or source.get("page_numbers") != entry.get("page_numbers") or source.get("doc_items") != entry.get("doc_items"):
            raise ValueError("Evidence source changed; detect again before migration")
    digest = hashlib.sha256(original).hexdigest()
    backup = backup_legacy_ledger(path, original)
    converted.update(source_index_signature=source_signature(rows),
                     migration={"from_schema": LEGACY_SCHEMA, "original_sha256": digest,
                                "backup_file": backup.name, "migrated_at_epoch": time.time(),
                                "human_decisions_preserved": True})
    atomic_json(path, converted)
    return {"changed": True, "schema": SCHEMA, "backup_file": backup.name, "entries": len(converted["entries"])}


@lru_cache(maxsize=128)
def _coverage_snapshot(ledger_path: str, ledger_mtime: int, ledger_size: int,
                       index_mtime: int, index_size: int,
                       visual_mtime: int, visual_size: int) -> dict:
    path = Path(ledger_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    normalized = normalize_ledger(data)
    active = [entry for entry in normalized["entries"] if not entry.get("superseded")]
    counts = Counter(entry["validation"]["state"] for entry in active)
    signature = data.get("source_index_signature")
    current = bool(signature and index_mtime and signature == source_signature(read_source_rows(path.parent)))
    valid = sum(is_validated_record(entry) for entry in active)
    rejected = counts.get("rejected", 0)
    return {"status": "legacy" if data["schema"] == LEGACY_SCHEMA else "stale" if not current else "pending" if valid + rejected < len(active) else "tracked_candidates_resolved" if active else "no_candidates",
            "source_current": current, "detected": len(active), "validated": valid,
            "unvalidated": len(active) - valid, "rejected": rejected, "pending": len(active) - valid - rejected, "states": dict(counts),
            "visual_parse_pending": sum(entry.get("validation_status") == "needs_visual_parse" and entry["validation"]["state"] not in {"validated", "rejected"} for entry in active),
            "visual_graph_extracted": sum(bool(entry.get("visual_extraction")) for entry in active),
            "visual_graph_needs_review": sum(bool(entry.get("visual_extraction")) and not is_validated_record(entry) for entry in active),
            "technical_notes": sum(entry.get("context_kind") == "note" for entry in active),
            "context_link_candidates": len({(tuple(sorted((link.get("source_chunk_id", ""), link.get("target_chunk_id", "")))), link.get("link_type")) for entry in active for link in entry.get("context_links") or [] if link.get("status") == "candidate"}),
            "schema": data["schema"], "migration_required": data["schema"] == LEGACY_SCHEMA,
            "measurement_scope": "detected_candidates_only", "whole_manual_coverage_measured": False}


def evidence_coverage(result_dir: Path) -> dict:
    base = {"measurement_scope": "detected_candidates_only", "whole_manual_coverage_measured": False,
            "detected": None, "validated": None, "unvalidated": None, "states": {}}
    path = result_dir / "technical_evidence_ledger.json"
    if not path.exists():
        return {**base, "status": "not_scanned", "source_current": False}
    try:
        stat = path.stat()
        index = result_dir / "retrieval_index.jsonl"
        visual = result_dir / "visual_evidence.jsonl"
        source = index.stat() if index.exists() else None
        visual_source = visual.stat() if visual.exists() else None
        result = _coverage_snapshot(
            str(path), stat.st_mtime_ns, stat.st_size,
            source.st_mtime_ns if source else 0, source.st_size if source else 0,
            visual_source.st_mtime_ns if visual_source else 0, visual_source.st_size if visual_source else 0,
        )
        from .source_coverage import coverage_status
        source = coverage_status(result_dir)
        reference_coverage = {key: source.get(key) for key in ("status", "source_items", "search_referenced_items", "dispositions", "semantic_coverage_verified")}
        reference_coverage["pending_source_review"] = len(source.get("recovery_queue") or [])
        return {**result, "states": dict(result["states"]), "source_reference_coverage": reference_coverage}
    except (OSError, ValueError, TypeError, KeyError):
        return {**base, "status": "invalid", "source_current": False, "reason": "evidence_ledger_unreadable"}


def book_readiness(*, correction_current: bool, verification_ready: bool,
                   blocking_reviews: int, index_current: bool, coverage: dict,
                   hybrid_ready: bool = False, audit_bypassed: bool = False) -> dict:
    correction = bool(correction_current and verification_ready and blocking_reviews == 0 and not audit_bypassed)
    # Search availability is a separate axis. A testing bypass may leave an
    # inspectable index without certifying correction readiness.
    lexical = bool(index_current and blocking_reviews == 0)
    return {"schema": "book-readiness/v1",
            "correction": {"ready": correction, "blocking_reviews": blocking_reviews,
                           "reason": "testing_bypass" if audit_bypassed else "review_pending" if blocking_reviews else "verification_pending" if not verification_ready else "corrections_not_current" if not correction_current else None},
            "search": {"lexical_ready": lexical, "hybrid_ready": bool(lexical and hybrid_ready),
                       "reason": "review_pending" if blocking_reviews else "index_not_current" if not index_current else None},
            "evidence": coverage}


def machine_readiness(books: list[dict | None], *, lexical_ready: bool, hybrid_ready: bool) -> dict:
    evidence = [book.get("readiness", {}).get("evidence", {}) if book else {"status": "not_scanned"} for book in books]
    unknown = sum(item.get("status") in {"not_scanned", "invalid", "legacy", "stale"} for item in evidence)
    return {"schema": "machine-readiness/v1",
            "correction": {"ready": bool(books) and all(book and book.get("readiness", {}).get("correction", {}).get("ready") for book in books)},
            "search": {"lexical_ready": lexical_ready, "hybrid_ready": bool(lexical_ready and hybrid_ready)},
            "evidence": {"status": "incomplete" if unknown or not books else "pending" if any(item.get("pending", 0) for item in evidence) else "tracked_candidates_resolved",
                         "detected": sum(item.get("detected") or 0 for item in evidence),
                         "validated": sum(item.get("validated") or 0 for item in evidence),
                         "visual_parse_pending": sum(item.get("visual_parse_pending") or 0 for item in evidence),
                         "visual_graph_extracted": sum(item.get("visual_graph_extracted") or 0 for item in evidence),
            "visual_graph_needs_review": sum(item.get("visual_graph_needs_review") or 0 for item in evidence),
            "technical_notes": sum(item.get("technical_notes") or 0 for item in evidence),
                         "context_link_candidates": sum(item.get("context_link_candidates") or 0 for item in evidence),
                         "manuals": len(books), "unknown_manuals": unknown,
                         "measurement_scope": "detected_candidates_only", "whole_manual_coverage_measured": False}}
