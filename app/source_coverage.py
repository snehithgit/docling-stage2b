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
    from .stage2c import _authoritative_visual_subjects
    correction_path = result_dir / "correction_ledger.json"
    corrections = json.loads(correction_path.read_text(encoding="utf-8")).get("entries", []) if correction_path.exists() else []
    normalized_visuals = []
    for entry in corrections:
        if entry.get("entry_type") != "vision_enrichment" or entry.get("status") == "superseded":
            continue
        value = entry.get("source_index")
        if value is None:
            value = entry.get("picture_index")
        if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
            continue
        index = int(value)
        if 0 <= index < len(document.get("pictures") or []):
            normalized_visuals.append({**entry, "source_index": index})
    visuals = {f"#/pictures/{entry['source_index']}": entry for entry in _authoritative_visual_subjects(normalized_visuals)}
    groups = {str(item.get("self_ref") or f"#/groups/{i}"): item for i, item in enumerate(document.get("groups") or [])}
    def visual_parent(item):
        parent = (item.get("parent") or {}).get("$ref")
        seen = set()
        while parent and parent not in seen:
            if re.fullmatch(r"#/pictures/\d+", parent):
                return parent
            seen.add(parent)
            parent = (groups.get(parent, {}).get("parent") or {}).get("$ref")
        return None
    report = measure_source_coverage(document, rows, candidates)
    table_routes = {}
    if any(gap["kind"] == "tables" for gap in report["items_without_search_reference"]):
        from .structural_anomaly import structural_entries
        for entry in structural_entries(result_dir):
            index = ((entry.get("_structural_evidence") or {}).get("route") or {}).get("source", {}).get("table_index")
            if index is not None:
                table_routes.setdefault(f"#/tables/{index}", entry)
    page_text = {}
    for row in rows:
        for page in row.get("page_numbers") or []:
            page_text.setdefault(page, []).append(_literal(str(row.get("text") or "") + " " + " ".join(row.get("headings") or [])))
    for gap in report["items_without_search_reference"]:
        item = document[gap["kind"]][int(gap["ref"].rsplit("/", 1)[1])]
        text = _literal(item.get("text"))
        safety = bool(re.search(r"\bwarning\b|\bcaution\b|\bn\s*\.\s*b\s*\.|\bdo not\b|\bmust not\b", text))
        picture_ref = gap["ref"] if gap["kind"] == "pictures" else visual_parent(item)
        visual = visuals.get(picture_ref) or {}
        if text and len(text) >= 20 and gap["pages"] and all(any(text in body for body in page_text.get(page, [])) for page in gap["pages"]):
            state = "represented_literal"
        elif gap["label"] in {"page_header", "page_footer"} and not safety:
            state = "excluded_page_furniture"
        else:
            state = "needs_source_review"
        if state == "needs_source_review" and visual.get("human_verified") is True and visual.get("human_visual_decision") in {"decorative", "not_useful"}:
            state = "excluded_human_visual_decision"
        gap.update(disposition=state, priority="high" if safety or picture_ref or gap["kind"] == "tables" else "normal",
                   recovery_route="visual_review" if picture_ref else "table_review" if gap["kind"] == "tables" else "source_text_review")
        if picture_ref:
            gap.update(review_group=picture_ref, visual_entry_id=visual.get("entry_id"),
                       visual_route_status="existing_review" if visual else "missing_review_route",
                       human_visual_decision=visual.get("human_visual_decision"))
        elif gap["kind"] == "tables" and gap["ref"] in table_routes:
            route = table_routes[gap["ref"]]
            gap.update(structural_route_id=route.get("route_id"), structural_code=route.get("structural_code"))
    report.update(schema="source-coverage/v1", generated_at_epoch=time.time(),
                  source_document_sha256=hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest(),
                  dispositions=dict(Counter(gap["disposition"] for gap in report["items_without_search_reference"])),
                  inputs=inputs, automatic_corrections=0)
    report["recovery_queue"] = sorted((gap for gap in report["items_without_search_reference"] if gap["disposition"] == "needs_source_review"),
                                      key=lambda gap: (gap["priority"] != "high", gap["pages"] or [0], gap["ref"]))
    review_groups = {}
    for gap in report["recovery_queue"]:
        key = gap.get("review_group") or gap["ref"]
        if key not in review_groups:
            review_groups[key] = {**gap, "review_group": key, "related_source_refs": []}
        review_groups[key]["related_source_refs"].append(gap["ref"])
    report["review_groups"] = list(review_groups.values())
    report["visual_groups_missing_review_route"] = sum(group.get("visual_route_status") == "missing_review_route" for group in report["review_groups"])
    atomic_json(result_dir / "source_coverage.json", report)
    return report


def _input_stats(result_dir):
    names = ("retrieval_index.jsonl", "visual_evidence.jsonl", "technical_evidence_ledger.json", "source_manifest.json", "correction_ledger.json", "routes.json", "diagnostics.json")
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
