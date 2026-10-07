"""Conserve omitted prose from the corrected working document, never infer diagram facts."""
import math
import re

VERSION = "omitted-prose/v1"


def recover_existing_prose(result_dir, source, *, max_tokens, apply=False):
    """Use current Stage 2C overlays; caller must hold the book lock and check freshness."""
    import copy
    import hashlib
    import json
    import tarfile
    import zipfile
    from .archive import select_docling_document
    from .docling_review import apply_repairs_to_document
    from .stage3 import Stage3ChunkBuilder
    from .source_coverage import coverage_pipeline
    with zipfile.ZipFile(source) as archive:
        original, _ = select_docling_document(archive)
    working = copy.deepcopy(original)
    corrections = {}
    for overlay in Stage3ChunkBuilder._read_overlays(result_dir / "chunk_overlays.jsonl"):
        if overlay.get("entry_type") != "text_correction" or overlay.get("source_type", "text") != "text":
            continue
        index = int(overlay["source_index"])
        texts = working.get("texts") or []
        if not 0 <= index < len(texts):
            raise ValueError("Correction overlay source is invalid")
        replacement = str(overlay.get("text") or "").strip()
        if not replacement:
            continue
        corrections[f"#/texts/{index}"] = {**overlay, "original_text": texts[index].get("text"), "corrected_text": replacement}
        texts[index]["text"] = replacement
    repairs = apply_repairs_to_document(result_dir, original, working)
    chunks_path = result_dir / "chunks.jsonl"
    chunks = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    recovered = omitted_prose_chunks(working, chunks, max_tokens=max_tokens)
    for row in recovered:
        row["stage2c"] = {"text_corrections": [corrections[ref] for ref in row["doc_items"] if ref in corrections],
                          "docling_page_repairs": [repair for repair in repairs if repair.get("source_ref") in row["doc_items"]]}
    result = {"recovery_version": VERSION, "eligible_passages": len(recovered), "applied": False,
              "passages": [{"doc_items": row["doc_items"], "page_numbers": row["page_numbers"], "text": row["text"]} for row in recovered],
              "model_calls": 0, "human_decisions_modified": False}
    if apply and recovered:
        digest = hashlib.sha256(chunks_path.read_bytes()).hexdigest()[:16]
        backup = result_dir / f"source-recovery.{digest}.tar"
        if not backup.exists():
            with tarfile.open(backup, "x") as archive:
                for name in ("chunks.jsonl", "retrieval_index.jsonl", "table_evidence.jsonl", "retrieval_quality.json", "technical_evidence_ledger.json", "stage3_chunking.json", "source_coverage.json"):
                    if (result_dir / name).exists():
                        archive.add(result_dir / name, arcname=name)
        Stage3ChunkBuilder.refresh_existing_outputs(result_dir, max_tokens=max_tokens, additional_chunks=recovered)
        coverage_pipeline(original, result_dir)
        result.update(applied=True, backup_file=backup.name)
    return result


def _literal(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def omitted_prose_chunks(document, chunks, *, max_tokens):
    referenced = {str(ref) for chunk in chunks for ref in chunk.get("doc_items") or []}
    by_page = {}
    for chunk in chunks:
        text = _literal(str(chunk.get("text") or "") + " " + " ".join(chunk.get("headings") or []))
        for page in chunk.get("page_numbers") or []:
            by_page.setdefault(page, []).append(text)
    groups = {str(item.get("self_ref") or f"#/groups/{i}"): item for i, item in enumerate(document.get("groups") or [])}

    def body_prose(item):
        parent = (item.get("parent") or {}).get("$ref")
        seen = set()
        while parent and parent not in seen:
            if parent == "#/body":
                return True
            seen.add(parent)
            group = groups.get(parent)
            if not group or str(group.get("label") or "").lower() in {"picture", "table", "figure", "diagram"}:
                return False
            parent = (group.get("parent") or {}).get("$ref")
        return False

    output = []
    for index, item in enumerate(document.get("texts") or []):
        ref = f"#/texts/{index}"
        if ref in referenced or not body_prose(item):
            continue
        text = str(item.get("text") or "").strip()
        label = str(item.get("label") or "")
        safety = bool(re.search(r"\bwarning\b|\bcaution\b|\bn\s*\.\s*b\s*\.|\bdo not\b|\bmust not\b", text, re.I))
        if label not in {"text", "paragraph", "list_item", "footnote", "section_header"} or (label == "section_header" and not safety):
            continue
        pages = {p.get("page_no") for p in item.get("prov") or []}
        if len(pages) != 1:
            continue
        page = next(iter(pages))
        if not isinstance(page, int) or isinstance(page, bool) or page <= 0:
            continue
        # Short fragments and unresolved labels stay in source/visual review.
        words = re.findall(r"\b\w+\b", text)
        estimate = max(math.ceil(len(text) / 2.5), len(re.findall(r"\w+|[^\w\s]", text)))
        if len(words) < 8 or estimate > max_tokens or not re.search(r"[.!?]", text):
            continue
        if any(_literal(text) in value for value in by_page.get(page, [])):
            continue
        output.append({"filename": str(document.get("name") or ""), "chunk_index": None,
                       "text": text, "raw_text": text, "num_tokens": max(1, estimate),
                       "num_tokens_estimated": True, "headings": [], "captions": [],
                       "doc_items": [ref], "page_numbers": [page],
                       "stage3_postprocess": {"action": "recover_omitted_body_prose", "version": VERSION,
                                              "source": "corrected_working_document", "inference_used": False}})
        referenced.add(ref)
        by_page.setdefault(page, []).append(_literal(text))
    return output
