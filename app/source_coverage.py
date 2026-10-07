"""Compare immutable Docling source items with derived evidence, without inference."""
from collections import Counter


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
