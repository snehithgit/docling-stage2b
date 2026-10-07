"""Compare immutable Docling source items with derived evidence, without inference."""
from collections import Counter
import hashlib
import json
import re
import time


def _literal(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def coverage_pipeline(document, result_dir):
    """Persist classified recovery work after indexing; never apply raw-source repairs."""
    from .evidence_contract import read_source_rows, atomic_json
    inputs = _input_stats(result_dir)
    rows = read_source_rows(result_dir)
    ledger = result_dir / "technical_evidence_ledger.json"
    candidates = json.loads(ledger.read_text(encoding="utf-8")).get("entries", []) if ledger.exists() else []
    report = measure_source_coverage(document, rows, candidates)
    page_text = {}
    for row in rows:
        for page in row.get("page_numbers") or []:
            page_text.setdefault(page, []).append(_literal(str(row.get("text") or "") + " " + " ".join(row.get("headings") or [])))
    for gap in report["items_without_search_reference"]:
        item = document[gap["kind"]][int(gap["ref"].rsplit("/", 1)[1])]
        text = _literal(item.get("text"))
        safety = bool(re.search(r"\bwarning\b|\bcaution\b|\bn\s*\.\s*b\s*\.|\bdo not\b|\bmust not\b", text))
        if text and len(text) >= 20 and gap["pages"] and all(any(text in body for body in page_text.get(page, [])) for page in gap["pages"]):
            state = "represented_literal"
        elif gap["label"] in {"page_header", "page_footer"} and not safety:
            state = "excluded_page_furniture"
        else:
            state = "needs_source_review"
        gap.update(disposition=state, priority="high" if safety or gap["kind"] in {"tables", "pictures"} else "normal",
                   recovery_route="visual_review" if gap["kind"] == "pictures" else "table_review" if gap["kind"] == "tables" else "source_text_review")
    report.update(schema="source-coverage/v1", generated_at_epoch=time.time(),
                  source_document_sha256=hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest(),
                  dispositions=dict(Counter(gap["disposition"] for gap in report["items_without_search_reference"])),
                  inputs=inputs, automatic_corrections=0)
    report["recovery_queue"] = sorted((gap for gap in report["items_without_search_reference"] if gap["disposition"] == "needs_source_review"),
                                      key=lambda gap: (gap["priority"] != "high", gap["pages"] or [0], gap["ref"]))
    atomic_json(result_dir / "source_coverage.json", report)
    return report


def _input_stats(result_dir):
    names = ("retrieval_index.jsonl", "visual_evidence.jsonl", "technical_evidence_ledger.json", "source_manifest.json")
    return {name: [p.stat().st_mtime_ns, p.stat().st_size] if (p := result_dir / name).exists() else None for name in names}


def coverage_status(result_dir):
    path = result_dir / "source_coverage.json"
    if not path.exists():
        return {"status": "not_measured", "semantic_coverage_verified": False}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        current = report.get("inputs") == _input_stats(result_dir)
        return {**report, "status": "current" if current else "stale", "current": current}
    except (OSError, ValueError, TypeError):
        return {"status": "invalid", "semantic_coverage_verified": False}


def measure_source_coverage(document: dict, search_rows: list, candidates: list) -> dict:
    items = {}
    for group in ("texts", "tables", "pictures"):
        for index, item in enumerate(document.get(group) or []):
            ref = f"#/{group}/{index}"
            pages = sorted({p["page_no"] for p in item.get("prov") or []
                            if isinstance(p.get("page_no"), int) and not isinstance(p["page_no"], bool) and p["page_no"] > 0})
            items[ref] = {"ref": ref, "kind": group, "pages": pages,
                          "label": item.get("label"), "text_preview": str(item.get("text") or "")[:240]}
    searchable, detected, validated = set(), set(), set()
    dangling = set()
    for rows, target in ((search_rows, searchable), (candidates, detected)):
        for row in rows:
            if row.get("superseded"):
                continue
            refs = set(row.get("doc_items") or [])
            target.update(refs & items.keys())
            dangling.update(refs - items.keys())
    # Candidate validation is deliberately not inferred from presence in an index.
    from .evidence_contract import is_validated_record
    for row in candidates:
        if not row.get("superseded") and is_validated_record(row):
            validated.update(set(row.get("doc_items") or []) & items.keys())
    gaps = [item for ref, item in items.items() if ref not in searchable]
    pages = sorted({page for item in items.values() for page in item["pages"]})
    covered_pages = {page for ref in searchable for page in items[ref]["pages"]}
    return {"measurement_scope": "source_item_reference_coverage", "semantic_coverage_verified": False,
            "source_items": len(items), "search_referenced_items": len(searchable),
            "candidate_referenced_items": len(detected), "validated_referenced_items": len(validated),
            "items_without_search_reference": gaps,
            "missing_by_kind": dict(Counter(item["kind"] for item in gaps)),
            "source_pages_with_items": len(pages), "pages_without_search_reference": sorted(set(pages) - covered_pages),
            "items_without_page_provenance": sorted(ref for ref, item in items.items() if not item["pages"]),
            "dangling_references": sorted(dangling), "model_calls": 0}
