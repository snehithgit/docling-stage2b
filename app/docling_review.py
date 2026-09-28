from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any

import fitz

from .table_repair import apply_matrix_to_table, parse_tsv_matrix


DOCLING_REVIEW_SCHEMA = "docling-page-repairs/v1"
DOCLING_REVIEW_RULE_VERSION = "docling-page-review-v1"
_ALLOWED_TYPES = {"paragraph", "heading", "table", "picture"}
_ALLOWED_COLLECTIONS = {"texts", "tables", "pictures"}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def repairs_path(result_dir: Path) -> Path:
    return Path(result_dir) / "docling_page_repairs.json"


def load_repairs(result_dir: Path) -> dict[str, Any]:
    payload = _load_json(repairs_path(result_dir))
    if not payload:
        return {
            "schema": DOCLING_REVIEW_SCHEMA,
            "rule_version": DOCLING_REVIEW_RULE_VERSION,
            "raw_docling_immutable": True,
            "updated_at_epoch": None,
            "repairs": [],
        }
    payload.setdefault("schema", DOCLING_REVIEW_SCHEMA)
    payload.setdefault("rule_version", DOCLING_REVIEW_RULE_VERSION)
    payload.setdefault("raw_docling_immutable", True)
    payload.setdefault("repairs", [])
    return payload


def _stable_json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _page_of(item: dict[str, Any]) -> int | None:
    for prov in item.get("prov") or []:
        if not isinstance(prov, dict):
            continue
        try:
            page = int(prov.get("page_no"))
        except (TypeError, ValueError):
            continue
        if page > 0:
            return page
    return None


def _bbox_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    for prov in item.get("prov") or []:
        if isinstance(prov, dict) and isinstance(prov.get("bbox"), dict):
            return copy.deepcopy(prov["bbox"])
    return None


def _bbox_top_left_points(bbox: dict[str, Any], page_height: float) -> tuple[float, float, float, float] | None:
    try:
        left = float(bbox.get("l")); right = float(bbox.get("r"))
        top = float(bbox.get("t")); bottom = float(bbox.get("b"))
    except (TypeError, ValueError):
        return None
    origin = str(bbox.get("coord_origin") or "BOTTOMLEFT").upper()
    if origin == "BOTTOMLEFT":
        y0, y1 = page_height - top, page_height - bottom
    else:
        y0, y1 = top, bottom
    x0, x1 = min(left, right), max(left, right)
    y0, y1 = min(y0, y1), max(y0, y1)
    if x1 - x0 <= 0.25 or y1 - y0 <= 0.25:
        return None
    return x0, y0, x1, y1


def _norm_bbox_from_docling(bbox: dict[str, Any] | None, page_width: float, page_height: float) -> dict[str, float] | None:
    if not isinstance(bbox, dict) or page_width <= 0 or page_height <= 0:
        return None
    pts = _bbox_top_left_points(bbox, page_height)
    if pts is None:
        return None
    x0, y0, x1, y1 = pts
    return {
        "x0": max(0.0, min(1.0, x0 / page_width)),
        "y0": max(0.0, min(1.0, y0 / page_height)),
        "x1": max(0.0, min(1.0, x1 / page_width)),
        "y1": max(0.0, min(1.0, y1 / page_height)),
    }


def _normalize_bbox(bbox: dict[str, Any]) -> dict[str, float]:
    try:
        x0 = float(bbox.get("x0")); y0 = float(bbox.get("y0")); x1 = float(bbox.get("x1")); y1 = float(bbox.get("y1"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("BBox must contain numeric x0, y0, x1 and y1 values.") from exc
    x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
    x0 = max(0.0, min(1.0, x0)); x1 = max(0.0, min(1.0, x1))
    y0 = max(0.0, min(1.0, y0)); y1 = max(0.0, min(1.0, y1))
    if x1 - x0 < 0.003 or y1 - y0 < 0.003:
        raise ValueError("BBox is too small to review safely.")
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def normalized_to_docling_bbox(bbox: dict[str, Any], page_width: float, page_height: float) -> dict[str, Any]:
    norm = _normalize_bbox(bbox)
    return {
        "l": round(norm["x0"] * page_width, 4),
        "t": round(page_height - norm["y0"] * page_height, 4),
        "r": round(norm["x1"] * page_width, 4),
        "b": round(page_height - norm["y1"] * page_height, 4),
        "coord_origin": "BOTTOMLEFT",
    }


def page_items(document: dict[str, Any], page: int, *, page_width: float, page_height: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for collection in ("texts", "tables", "pictures"):
        for index, item in enumerate(document.get(collection) or []):
            if not isinstance(item, dict) or _page_of(item) != int(page):
                continue
            bbox_raw = _bbox_from_item(item)
            bbox = _norm_bbox_from_docling(bbox_raw, page_width, page_height)
            if collection == "texts":
                label = str(item.get("label") or "text")
                region_type = "heading" if label in {"section_header", "title"} else "paragraph"
                text = str(item.get("text") or "")
            elif collection == "tables":
                label = str(item.get("label") or "table")
                region_type = "table"
                cells = ((item.get("data") or {}).get("table_cells") or [])
                text = "\n".join(str(cell.get("text") or "") for cell in cells if isinstance(cell, dict))
            else:
                label = str(item.get("label") or "picture")
                region_type = "picture"
                text = ""
            rows.append({
                "ref": str(item.get("self_ref") or f"#/{collection}/{index}"),
                "collection": collection,
                "index": index,
                "page": int(page),
                "label": label,
                "region_type": region_type,
                "text": text[:12000],
                "bbox": bbox,
                "bbox_available": bbox is not None,
                "raw_bbox": bbox_raw,
                "source_signature": _stable_json_hash(item),
            })
    rows.sort(key=lambda row: (
        (row.get("bbox") or {}).get("y0", 2.0),
        (row.get("bbox") or {}).get("x0", 2.0),
        str(row.get("ref") or ""),
    ))
    return rows


def _ref_parts(source_ref: str | None) -> tuple[str | None, int | None]:
    value = str(source_ref or "").strip()
    if not value.startswith("#/"):
        return None, None
    parts = value[2:].split("/")
    if len(parts) != 2 or parts[0] not in _ALLOWED_COLLECTIONS:
        return None, None
    try:
        idx = int(parts[1])
    except ValueError:
        return None, None
    return parts[0], idx


def _source_item(document: dict[str, Any], source_ref: str | None) -> dict[str, Any] | None:
    collection, index = _ref_parts(source_ref)
    if collection is None or index is None:
        return None
    values = document.get(collection) or []
    if index < 0 or index >= len(values) or not isinstance(values[index], dict):
        return None
    return values[index]


def _source_region_type(collection: str | None, source: dict[str, Any] | None) -> str | None:
    if source is None or collection is None:
        return None
    if collection == "tables":
        return "table"
    if collection == "pictures":
        return "picture"
    if collection == "texts":
        label = str(source.get("label") or "text")
        return "heading" if label in {"section_header", "title"} else "paragraph"
    return None


def save_approved_repair(
    result_dir: Path,
    immutable_document: dict[str, Any],
    *,
    page: int,
    page_width: float,
    page_height: float,
    bbox: dict[str, Any],
    region_type: str,
    source_ref: str | None,
    proposed_text: str | None = None,
    table_tsv: str | None = None,
    header_rows: int = 1,
    note: str | None = None,
    extraction: dict[str, Any] | None = None,
    source_zip_sha256: str | None = None,
) -> dict[str, Any]:
    if int(page) < 1:
        raise ValueError("Page number must be positive.")
    region_type = str(region_type or "").strip().lower()
    if region_type not in _ALLOWED_TYPES:
        raise ValueError(f"Unsupported region type: {region_type or 'empty'}")
    norm_bbox = _normalize_bbox(bbox)
    source = _source_item(immutable_document, source_ref)
    collection, source_index = _ref_parts(source_ref)
    if source_ref and source is None:
        raise ValueError("Selected Docling source item no longer exists.")
    if source is not None and _page_of(source) not in {None, int(page)}:
        raise ValueError("Selected Docling item belongs to a different page.")
    source_region_type = _source_region_type(collection, source)
    if source_region_type is not None and region_type != source_region_type:
        raise ValueError(
            f"Existing Docling {source_region_type} regions cannot be reclassified as {region_type} in Page Review v1; "
            "draw a new missing region instead."
        )

    proposed = str(proposed_text or "").strip()
    matrix: list[list[str]] | None = None
    if source is None and region_type == "picture":
        raise ValueError("Creating a new picture region is not supported by Docling Page Review v1; only existing picture bboxes can be repaired safely.")
    if region_type == "table":
        if table_tsv is not None and str(table_tsv).strip():
            matrix = parse_tsv_matrix(str(table_tsv))
        elif source is None:
            raise ValueError("A new table region requires reviewed table content before it can be approved.")
    elif region_type in {"paragraph", "heading"}:
        if source is None and not proposed:
            raise ValueError("A new text region requires reviewed text before it can be approved.")

    original_bbox = _bbox_from_item(source) if source is not None else None
    original_text = str(source.get("text") or "") if source is not None and collection == "texts" else ""
    source_signature = _stable_json_hash(source) if source is not None else None
    payload = load_repairs(result_dir)
    repairs = [row for row in (payload.get("repairs") or []) if isinstance(row, dict)]

    # One active repair per physical Docling ref. Re-approving replaces the old
    # derived repair while preserving the historical row as superseded.
    now = time.time()
    if source_ref:
        for row in repairs:
            if str(row.get("source_ref") or "") == str(source_ref) and row.get("status") == "applied":
                row["status"] = "superseded"
                row["superseded_at_epoch"] = now

    repair_id = f"DPR-{uuid.uuid4().hex[:12].upper()}"
    repair = {
        "repair_id": repair_id,
        "status": "applied",
        "human_verified": True,
        "raw_docling_immutable": True,
        "rule_version": DOCLING_REVIEW_RULE_VERSION,
        "page": int(page),
        "region_type": region_type,
        "source_ref": source_ref or None,
        "source_collection": collection,
        "source_index": source_index,
        "source_item_signature": source_signature,
        "source_zip_sha256": str(source_zip_sha256 or ""),
        "original_bbox": original_bbox,
        "bbox": normalized_to_docling_bbox(norm_bbox, page_width, page_height),
        "bbox_normalized": norm_bbox,
        "original_text": original_text,
        "proposed_text": proposed or None,
        "table_matrix": matrix,
        "header_rows": max(0, min(int(header_rows), len(matrix))) if matrix is not None else None,
        "note": str(note or "").strip()[:2000],
        "extraction": dict(extraction or {}),
        "approved_at_epoch": now,
    }
    repairs.append(repair)
    payload.update({
        "schema": DOCLING_REVIEW_SCHEMA,
        "rule_version": DOCLING_REVIEW_RULE_VERSION,
        "raw_docling_immutable": True,
        "updated_at_epoch": now,
        "repairs": repairs,
    })
    _atomic_json(repairs_path(result_dir), payload)
    return repair


def deactivate_repair(result_dir: Path, repair_id: str) -> dict[str, Any]:
    payload = load_repairs(result_dir)
    repairs = [row for row in (payload.get("repairs") or []) if isinstance(row, dict)]
    match = next((row for row in repairs if str(row.get("repair_id") or "") == str(repair_id)), None)
    if match is None:
        raise KeyError(repair_id)
    if match.get("status") == "applied":
        match["status"] = "inactive"
        match["deactivated_at_epoch"] = time.time()
    payload["updated_at_epoch"] = time.time()
    payload["repairs"] = repairs
    _atomic_json(repairs_path(result_dir), payload)
    return match


def active_repairs(result_dir: Path, immutable_document: dict[str, Any]) -> list[dict[str, Any]]:
    payload = load_repairs(result_dir)
    manifest = _load_json(Path(result_dir) / "source_manifest.json")
    current_source_sha = str(manifest.get("converted_zip_sha256") or "")
    rows: list[dict[str, Any]] = []
    for repair in payload.get("repairs") or []:
        if not isinstance(repair, dict) or repair.get("status") != "applied" or not repair.get("human_verified"):
            continue
        repair_source_sha = str(repair.get("source_zip_sha256") or "")
        if repair_source_sha and current_source_sha and repair_source_sha != current_source_sha:
            continue
        source_ref = repair.get("source_ref")
        if source_ref:
            source = _source_item(immutable_document, source_ref)
            if source is None:
                continue
            expected = str(repair.get("source_item_signature") or "")
            if expected and expected != _stable_json_hash(source):
                # Raw Docling source changed since approval; stale repairs are
                # never silently reused.
                continue
        rows.append(copy.deepcopy(repair))
    rows.sort(key=lambda row: (int(row.get("page") or 0), str(row.get("repair_id") or "")))
    return rows


def _replace_item_bbox(item: dict[str, Any], bbox: dict[str, Any], page: int) -> None:
    prov = [dict(value) for value in (item.get("prov") or []) if isinstance(value, dict)]
    target = None
    for value in prov:
        try:
            if int(value.get("page_no")) == int(page):
                target = value
                break
        except (TypeError, ValueError):
            continue
    if target is None:
        target = {"page_no": int(page)}
        prov.insert(0, target)
    target["bbox"] = copy.deepcopy(bbox)
    item["prov"] = prov


def apply_repairs_to_document(
    result_dir: Path,
    immutable_document: dict[str, Any],
    working_document: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply approved repairs to the in-memory Stage-3 Docling document only.

    New missing regions are deliberately not spliced into Docling's body graph;
    they are emitted as dedicated derived Stage-3 chunks instead. Existing
    Docling items may have their bbox/text/table matrix repaired in the working
    copy because their body references already exist and remain stable.
    """
    applied: list[dict[str, Any]] = []
    for repair in active_repairs(result_dir, immutable_document):
        source_ref = str(repair.get("source_ref") or "")
        if not source_ref:
            continue
        collection, index = _ref_parts(source_ref)
        if collection is None or index is None:
            continue
        values = working_document.get(collection) or []
        if index < 0 or index >= len(values) or not isinstance(values[index], dict):
            continue
        target = values[index]
        _replace_item_bbox(target, dict(repair.get("bbox") or {}), int(repair.get("page") or 1))
        if collection == "texts" and repair.get("proposed_text"):
            target["text"] = str(repair.get("proposed_text") or "")
        elif collection == "tables" and repair.get("table_matrix"):
            apply_matrix_to_table(
                working_document, index, list(repair.get("table_matrix") or []),
                header_rows=int(repair.get("header_rows") if repair.get("header_rows") is not None else 1),
            )
            # apply_matrix_to_table reconstructs the table item, so restore the
            # reviewer-approved physical bbox afterwards.
            target = (working_document.get("tables") or [])[index]
            _replace_item_bbox(target, dict(repair.get("bbox") or {}), int(repair.get("page") or 1))
        applied.append(repair)
    return applied


def derived_stage3_chunks(result_dir: Path, immutable_document: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for repair in active_repairs(result_dir, immutable_document):
        if repair.get("source_ref"):
            continue
        region_type = str(repair.get("region_type") or "paragraph")
        page = int(repair.get("page") or 0)
        if region_type == "table":
            matrix = repair.get("table_matrix") or []
            if not matrix:
                continue
            raw_text = "\n".join("\t".join(str(value or "") for value in row) for row in matrix).strip()
            content_type = "table"
        else:
            raw_text = str(repair.get("proposed_text") or "").strip()
            content_type = "prose"
        if not raw_text:
            continue
        repair_id = str(repair.get("repair_id") or "")
        rows.append({
            "filename": str(immutable_document.get("name") or ""),
            "chunk_index": None,
            "text": raw_text,
            "raw_text": raw_text,
            "num_tokens": max(1, math.ceil(len(raw_text) / 3.0)),
            "num_tokens_estimated": True,
            "headings": [],
            "captions": [],
            "doc_items": [f"repair://{repair_id}"],
            "page_numbers": [page] if page > 0 else [],
            "content_type": content_type,
            "metadata": {
                "docling_page_repair": True,
                "repair_id": repair_id,
                "region_type": region_type,
                "bbox": repair.get("bbox"),
                "header_rows": repair.get("header_rows"),
                "human_verified": True,
            },
            "stage3_postprocess": {
                "action": "preserve_human_reconstructed_missing_region",
                "source": "human_bbox_source_reconstruction",
                "raw_docling_immutable": True,
            },
        })
    return rows


def crop_pdf_region(
    pdf_path: Path,
    page: int,
    bbox: dict[str, Any],
    *,
    scale: float = 3.0,
) -> tuple[bytes, dict[str, Any]]:
    norm = _normalize_bbox(bbox)
    with fitz.open(pdf_path) as pdf:
        index = int(page) - 1
        if index < 0 or index >= len(pdf):
            raise IndexError("PDF page not found")
        p = pdf[index]
        rect = fitz.Rect(
            norm["x0"] * p.rect.width,
            norm["y0"] * p.rect.height,
            norm["x1"] * p.rect.width,
            norm["y1"] * p.rect.height,
        )
        rect &= p.rect
        if rect.width < 1 or rect.height < 1:
            raise ValueError("BBox does not intersect the source page.")
        pix = p.get_pixmap(matrix=fitz.Matrix(float(scale), float(scale)), clip=rect, alpha=False)
        meta = {
            "page": int(page),
            "bbox_normalized": norm,
            "clip_points": [round(rect.x0, 4), round(rect.y0, 4), round(rect.x1, 4), round(rect.y1, 4)],
            "scale": float(scale),
            "pixel_width": int(pix.width),
            "pixel_height": int(pix.height),
            "source_kind": "pdf",
        }
        return pix.tobytes("png"), meta
