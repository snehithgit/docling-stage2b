"""Source-local context candidates; these are not verified diagram branches."""
from __future__ import annotations

import re

LINK_RULE_VERSION = "technical-context/v1"
CALLOUT = re.compile(r"^(?:n\s*\.\s*b\s*\.?|note|warning|caution)\s*[:!.]?\s*$", re.I)
FIGURE = re.compile(r"\b(?:fig(?:ure)?\.?|diagram)\s*(\d+(?:\.\d+)*[a-z]?)(?!\w|\.\d)", re.I)
STOP = set("the a an and or of for to in on at is be it this that with from when as system diagram chart flow note warning caution technical procedure instruction general spare parts service".split())


def body_text(row: dict) -> str:
    headings = {re.sub(r"\s+", " ", str(h)).strip().casefold() for h in row.get("headings") or []}
    lines = str(row.get("text") or "").splitlines()
    while lines and (not lines[0].strip() or re.sub(r"\s+", " ", lines[0]).strip().casefold() in headings):
        lines.pop(0)
    return "\n".join(lines).strip()


def callout_kind(row: dict) -> str | None:
    body = body_text(row)
    deepest = str((row.get("headings") or [""])[-1]).strip()
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    marker = deepest if CALLOUT.fullmatch(deepest) else first
    if re.match(r"^(?:n\s*\.\s*b\s*\.?|note)\b|^n\s*\.\s*b\s*\.", marker, re.I):
        return "note"
    if re.match(r"^(?:warning|caution)\b", marker, re.I):
        return "warning"
    return None


def _topics(row: dict) -> set[str]:
    # Use body and deepest specific heading, never inherited chapter titles.
    value = body_text(row) + " " + str((row.get("headings") or [""])[-1])
    words = set(re.findall(r"[a-z][a-z0-9-]{3,}", value.lower())) - STOP
    return {re.sub(r"(?:ing|ed|s)$", "", w) for w in words}


def _order(row: dict, fallback: int) -> int:
    value = row.get("chunk_index")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else fallback


def context_links(rows: list[dict], records: list[dict]) -> dict[str, list[dict]]:
    """Nearest same-page diagrams with matching topics or explicit figure refs.

    All links remain candidates. Proximity does not certify that a note applies
    to a particular flowchart branch or component. No cross-book/page links.
    """
    by_chunk = {r["source_chunk_id"]: r for r in records}
    diagrams = []
    for position, row in enumerate(rows):
        position = _order(row, position)
        record = by_chunk.get(row.get("chunk_id"))
        if record and record.get("visual_doc_items") and record.get("validation_status") != "missing_provenance":
            diagrams.append((position, row, record))
    links = {}
    for position, row in enumerate(rows):
        position = _order(row, position)
        kind = callout_kind(row)
        caption = FIGURE.search(body_text(row) + " " + str((row.get("headings") or [""])[-1]))
        if not kind and not caption:
            continue
        if not row.get("doc_items") or not any(str(ref).startswith(("#/texts/", "#/tables/")) for ref in row["doc_items"]):
            continue
        record = by_chunk.get(row.get("chunk_id"))
        if not record or record.get("validation_status") == "missing_provenance":
            continue
        candidates = []
        for diagram_position, diagram, diagram_record in diagrams:
            if diagram.get("chunk_id") == row.get("chunk_id") or abs(position - diagram_position) > 3:
                continue
            if (diagram.get("postprocess_job_id"), diagram.get("source_filename")) != (row.get("postprocess_job_id"), row.get("source_filename")):
                continue
            pages, diagram_pages = row.get("page_numbers") or [], diagram.get("page_numbers") or []
            if not pages or not diagram_pages or pages[0] != diagram_pages[0]:
                continue
            shared = sorted(_topics(row) & _topics(diagram))
            figure_match = bool(caption and any(match.group(1).casefold() == caption.group(1).casefold() for match in FIGURE.finditer(str(diagram.get("text") or ""))))
            # Notes normally follow the chart. A matching explicit figure label
            # may precede it. Generic adjacency alone is never a link.
            if not figure_match and (diagram_position >= position or not shared):
                continue
            candidates.append((0 if figure_match else 1, abs(position - diagram_position), diagram, diagram_record, shared))
        if not candidates:
            continue
        candidates.sort(key=lambda item: (item[0], item[1]))
        # Equally plausible diagrams remain ambiguous; do not pick arbitrarily.
        if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2]:
            continue
        _, distance, diagram, diagram_record, shared = candidates[0]
        relation = f"{kind or 'caption'}_for_diagram"
        for source, target in ((diagram_record, record), (record, diagram_record)):
            links.setdefault(source["source_chunk_id"], []).append({
                "link_type": relation, "status": "candidate", "rule_version": LINK_RULE_VERSION,
                "basis": "explicit_figure_reference" if candidates[0][0] == 0 else "same_page_nearest_preceding_diagram_and_topic",
                "shared_topics": shared, "chunk_distance": distance,
                "source_chunk_id": source["source_chunk_id"], "target_chunk_id": target["source_chunk_id"],
                "target_source_sha256": target["source_sha256"], "target_page_numbers": target["page_numbers"],
                "target_doc_items": target["doc_items"], "branch_relationship_verified": False,
            })
    return links
