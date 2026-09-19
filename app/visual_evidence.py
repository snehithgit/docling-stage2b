from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterable


VISUAL_EVIDENCE_SCHEMA = "docling-rag-visual-evidence/v1"
VISUAL_INDEX_SCHEMA = "docling-rag-visual-index/v1"
VISUAL_SUMMARY_SCHEMA = "docling-rag-visual-summary/v1"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _write_jsonl_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _clean_list(value: Any, *, limit: int = 20, item_limit: int = 240) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "")).strip()[:item_limit]
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
        if len(output) >= limit:
            break
    return output


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _visual_id(postprocess_job_id: int | None, picture_index: int | None, route_id: str) -> str:
    job = str(postprocess_job_id) if postprocess_job_id is not None else "unknown"
    if picture_index is not None:
        return f"V-{job}-{picture_index:06d}"
    safe_route = re.sub(r"[^A-Za-z0-9_-]+", "-", route_id or "unknown").strip("-") or "unknown"
    return f"V-{job}-{safe_route}"


def _eligibility(entry: dict[str, Any]) -> tuple[bool, str]:
    status = str(entry.get("status") or "").lower()
    verdict = str(entry.get("verification_verdict") or "").upper()
    unresolved = bool(entry.get("unresolved", True))
    if status != "applied":
        return False, f"stage2c_{status or 'unknown'}"
    if verdict and verdict != "TECHNICAL_USEFUL":
        return False, f"verdict_{verdict.lower()}"
    if unresolved:
        return False, "unresolved_visual_details"
    if not (_clean_list(entry.get("visible_text")) or _clean_list(entry.get("visible_objects")) or str(entry.get("generated_summary") or "").strip()):
        return False, "empty_visual_evidence"
    return True, "applied_resolved_technical_visual"


def _search_text(category: str, visible_text: list[str], visible_objects: list[str], summary: str) -> str:
    parts: list[str] = []
    if category and category != "unknown":
        parts.append(category.replace("_", " "))
    parts.extend(visible_text)
    parts.extend(visible_objects)
    if summary:
        parts.append(summary)
    return "\n".join(part for part in parts if part).strip()


def normalize_visual_entry(
    entry: dict[str, Any],
    *,
    postprocess_job_id: int | None,
    source_filename: str,
    result_dir_name: str,
    verification_lookup: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(entry, dict) or entry.get("entry_type") != "vision_enrichment":
        return None
    picture_index = _safe_int(entry.get("source_index"))
    page = _safe_int(entry.get("page"))
    route_id = str(entry.get("route_id") or "")
    visible_text = _clean_list(entry.get("visible_text"), limit=20, item_limit=160)
    visible_objects = _clean_list(entry.get("visible_objects"), limit=20, item_limit=220)
    summary = re.sub(r"\s+", " ", str(entry.get("generated_summary") or "")).strip()[:1200]
    category = str(entry.get("diagram_category") or "unknown").strip().lower().replace(" ", "_") or "unknown"
    eligible, eligibility_reason = _eligibility(entry)
    visual_id = _visual_id(postprocess_job_id, picture_index, route_id)
    verification_job_id = _safe_int(entry.get("verification_job_id"))
    lookup = (verification_lookup or {}).get(verification_job_id or -1, {})
    provider = str(entry.get("verification_provider") or lookup.get("provider") or entry.get("processor") or "").strip() or None
    worker = str(entry.get("verification_worker") or lookup.get("worker") or "").strip() or None
    evidence = {
        "schema": VISUAL_EVIDENCE_SCHEMA,
        "visual_evidence_id": visual_id,
        "postprocess_job_id": postprocess_job_id,
        "result_dir": result_dir_name,
        "book": source_filename,
        "source_filename": source_filename,
        "page": page,
        "source_page": page,
        "picture_index": picture_index,
        "artifact": entry.get("artifact"),
        "docling_ref": f"#/pictures/{picture_index}" if picture_index is not None else None,
        "category": category,
        "technical_subject": None,
        "visible_text": visible_text,
        "visible_objects": visible_objects,
        # .37 intentionally did not ask the vision model to invent these richer
        # structures. Keep explicit empty fields rather than manufacturing data.
        "technical_values": [],
        "controls": [],
        "relationships": [],
        "procedure_information": [],
        "warnings": [],
        "summary": summary,
        "unresolved": bool(entry.get("unresolved", True)),
        "verification_job_id": verification_job_id,
        "verification_route_id": route_id or None,
        "verification_provider": provider,
        "verification_worker": worker,
        "verification_model": entry.get("model"),
        "verification_verdict": entry.get("verification_verdict"),
        "verification_status": entry.get("status"),
        "stage2c_status": entry.get("status"),
        "stage2c_status_reason": entry.get("status_reason"),
        "source_sha256": entry.get("original_source_sha256"),
        "rule_version": entry.get("rule_version"),
        "rag_eligible": eligible,
        "rag_eligibility_reason": eligibility_reason,
        "provenance": {
            "raw_docling_immutable": bool(entry.get("raw_docling_immutable", True)),
            "visible_text": "vision_model_read_source_text",
            "visible_objects": "vision_model_interpretation",
            "summary": "vision_model_interpretation",
            "ledger_entry_id": entry.get("entry_id"),
        },
    }
    evidence["search_text"] = _search_text(category, visible_text, visible_objects, summary)
    return evidence


def build_visual_evidence(
    result_dir: Path,
    *,
    postprocess_job_id: int | None = None,
    source_filename: str | None = None,
    verification_lookup: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize persisted .37 Stage 2C vision enrichments for RAG.

    No model, Docling, or source-image call is performed. The immutable converted
    ZIP is never changed. Only derived JSONL/index files inside the processed
    result directory are written.
    """
    result_dir = Path(result_dir)
    manifest = _read_json(result_dir / "source_manifest.json")
    ledger = _read_json(result_dir / "correction_ledger.json")
    if postprocess_job_id is None:
        match = re.search(r"__job(\d+)", result_dir.name)
        postprocess_job_id = int(match.group(1)) if match else None
    book = str(source_filename or manifest.get("source_filename") or result_dir.name)

    rows: list[dict[str, Any]] = []
    for entry in ledger.get("entries") or []:
        normalized = normalize_visual_entry(
            entry,
            postprocess_job_id=postprocess_job_id,
            source_filename=book,
            result_dir_name=result_dir.name,
            verification_lookup=verification_lookup,
        )
        if normalized is not None:
            rows.append(normalized)
    rows.sort(key=lambda row: (
        int(row.get("page") or 0),
        int(row.get("picture_index") if row.get("picture_index") is not None else 10**9),
        str(row.get("visual_evidence_id") or ""),
    ))

    index_rows: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("rag_eligible"):
            continue
        page = row.get("page")
        search_text = str(row.get("search_text") or "").strip()
        if not search_text:
            continue
        index_rows.append({
            "schema": VISUAL_INDEX_SCHEMA,
            "evidence_kind": "visual",
            "postprocess_job_id": row.get("postprocess_job_id"),
            "result_dir": row.get("result_dir"),
            "source_filename": row.get("source_filename"),
            "chunk_id": row.get("visual_evidence_id"),
            "visual_evidence_id": row.get("visual_evidence_id"),
            "chunk_index": row.get("picture_index"),
            "picture_index": row.get("picture_index"),
            "page_numbers": [page] if page is not None else [],
            "doc_items": [row.get("docling_ref")] if row.get("docling_ref") else [],
            "headings": [],
            "text": search_text,
            "content_type": "visual_evidence",
            "quality_score": 90,
            "warnings": [],
            "category": row.get("category"),
            "visible_text": row.get("visible_text") or [],
            "visible_objects": row.get("visible_objects") or [],
            "summary": row.get("summary") or "",
            "artifact": row.get("artifact"),
            "docling_ref": row.get("docling_ref"),
            "unresolved": row.get("unresolved"),
            "verification_job_id": row.get("verification_job_id"),
            "verification_route_id": row.get("verification_route_id"),
            "verification_provider": row.get("verification_provider"),
            "verification_worker": row.get("verification_worker"),
            "verification_model": row.get("verification_model"),
            "verification_verdict": row.get("verification_verdict"),
            "stage2c_status": row.get("stage2c_status"),
            "rag_eligible": True,
            "rag_eligibility_reason": row.get("rag_eligibility_reason"),
            "provenance": row.get("provenance") or {},
        })

    _write_jsonl_atomic(result_dir / "visual_evidence.jsonl", rows)
    _write_jsonl_atomic(result_dir / "visual_evidence_index.jsonl", index_rows)
    reason_counts: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("rag_eligibility_reason") or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    summary = {
        "schema": VISUAL_SUMMARY_SCHEMA,
        "generated_at_epoch": time.time(),
        "postprocess_job_id": postprocess_job_id,
        "source_filename": book,
        "result_dir": result_dir.name,
        "total_visual_records": len(rows),
        "rag_eligible_visuals": len(index_rows),
        "rag_excluded_visuals": len(rows) - len(index_rows),
        "eligibility_reasons": dict(sorted(reason_counts.items())),
        "model_calls": 0,
        "docling_calls": 0,
        "raw_docling_immutable": True,
    }
    _atomic_json(result_dir / "visual_evidence_summary.json", summary)
    return summary


def ensure_visual_evidence_fresh(
    result_dir: Path,
    *,
    postprocess_job_id: int | None = None,
    source_filename: str | None = None,
    verification_lookup: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rebuild derived visual evidence only when the authoritative ledger changed."""
    result_dir = Path(result_dir)
    ledger = result_dir / "correction_ledger.json"
    index = result_dir / "visual_evidence_index.jsonl"
    summary_path = result_dir / "visual_evidence_summary.json"
    needs_refresh = not index.is_file() or not summary_path.is_file()
    if ledger.is_file() and index.is_file():
        try:
            needs_refresh = needs_refresh or ledger.stat().st_mtime_ns > index.stat().st_mtime_ns
        except OSError:
            needs_refresh = True
    if needs_refresh:
        return build_visual_evidence(
            result_dir,
            postprocess_job_id=postprocess_job_id,
            source_filename=source_filename,
            verification_lookup=verification_lookup,
        )
    return _read_json(summary_path)


def search_visual_indices(
    index_paths: list[Path],
    query: str,
    *,
    top_k: int = 3,
    preferred_pages: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Search normalized visual evidence using the existing deterministic BM25 path.

    Page affinity is only a weak re-rank signal after lexical matching; a visual
    is never admitted merely because it is adjacent to a text chunk.
    """
    from .retrieval import search_indices

    if not index_paths:
        return []
    candidates = search_indices(index_paths, query, top_k=max(20, int(top_k) * 5))
    preferred = {int(page) for page in (preferred_pages or set())}
    reranked: list[tuple[float, dict[str, Any]]] = []
    for row in candidates:
        score = float(row.get("score") or 0.0)
        pages = {_safe_int(value) for value in (row.get("page_numbers") or [])}
        pages.discard(None)
        exact = bool(preferred and pages.intersection(preferred))
        near = bool(preferred and pages and any(abs(int(page) - int(pref)) == 1 for page in pages for pref in preferred))
        if exact:
            score += 1.5
            row["page_affinity"] = "same_page"
        elif near:
            score += 0.35
            row["page_affinity"] = "adjacent_page"
        else:
            row["page_affinity"] = "query_match"
        reranked.append((score, row))
    reranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("source_filename") or ""), int(pair[1].get("picture_index") or 0)))
    output: list[dict[str, Any]] = []
    for rank, (score, row) in enumerate(reranked[: max(1, int(top_k))], start=1):
        item = dict(row)
        item["rank"] = rank
        item["score"] = round(score, 4)
        output.append(item)
    return output
