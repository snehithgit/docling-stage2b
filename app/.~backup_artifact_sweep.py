from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from .archive import select_docling_document


TECHNICAL_PICTURE_CLASSES = {
    "engineering_drawing", "flow_chart", "screenshot_from_manual", "table",
    "line_chart", "bar_chart", "box_plot", "full_page_image", "geographical_map",
}


def picture_top_class(picture: dict[str, Any]) -> tuple[str | None, float | None]:
    predictions: list[dict[str, Any]] = []
    for item in picture.get("annotations") or []:
        if isinstance(item, dict) and item.get("kind") == "classification":
            values = item.get("predicted_classes") or []
            predictions = [value for value in values if isinstance(value, dict)]
            if predictions:
                break
    if not predictions:
        meta_predictions = (((picture.get("meta") or {}).get("classification") or {}).get("predictions") or [])
        predictions = [value for value in meta_predictions if isinstance(value, dict)]
    if not predictions:
        return None, None
    best = max(predictions, key=lambda item: float(item.get("confidence") or 0.0))
    class_name = str(best.get("class_name") or best.get("name") or "").strip() or None
    confidence = best.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    return class_name, confidence


def is_technical_picture_class(class_name: str | None) -> bool:
    return bool(class_name and str(class_name) in TECHNICAL_PICTURE_CLASSES)


def docling_pictures(zip_path: Path) -> list[dict[str, Any]]:
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("Converted output is not a ZIP archive")
    with zipfile.ZipFile(zip_path) as archive:
        document, _json_member = select_docling_document(archive)
    return [item for item in (document.get("pictures") or []) if isinstance(item, dict)]


def normal_picture_route_indices(rows: list[dict[str, Any]]) -> set[int]:
    """Return picture indices already covered by a current normal Stage 2B route."""
    indices: set[int] = set()
    for row in rows:
        if str(row.get("code") or "") == "FULL_TECHNICAL_VISUAL":
            continue
        try:
            source = json.loads(row.get("source_json") or "{}") if isinstance(row.get("source_json"), str) else (row.get("source") or {})
        except (json.JSONDecodeError, TypeError):
            source = {}
        if str(source.get("type") or "") != "picture":
            continue
        try:
            indices.add(int(source.get("index")))
        except (TypeError, ValueError):
            continue
    return indices


def build_artifact_sweep_plan(zip_path: Path, existing_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build non-overlapping FULL_TECHNICAL_VISUAL jobs for one book.

    A picture with a current normal picture route is deliberately omitted. This
    prevents duplicate inference and competing Stage 2C vision entries for the
    same Docling picture.
    """
    pictures = docling_pictures(zip_path)
    normal_indices = normal_picture_route_indices(existing_rows)
    jobs: list[dict[str, Any]] = []
    technical_visuals = 0
    skipped_normal_picture_routes = 0

    for picture_index, picture in enumerate(pictures):
        class_name, confidence = picture_top_class(picture)
        if not is_technical_picture_class(class_name):
            continue
        technical_visuals += 1
        if picture_index in normal_indices:
            skipped_normal_picture_routes += 1
            continue
        prov = picture.get("prov") or []
        page = None
        if prov and isinstance(prov[0], dict) and isinstance(prov[0].get("page_no"), (int, float)):
            page = int(prov[0]["page_no"])
        image = picture.get("image") or {}
        jobs.append({
            "route_id": f"AV{picture_index:06d}",
            "target": "oneplus",  # historical lane; runtime uses shared work stealing
            "code": "FULL_TECHNICAL_VISUAL",
            "priority": "low",
            "source": {
                "type": "picture",
                "index": picture_index,
                "page": page,
                "artifact": image.get("uri"),
                "class": class_name,
                "confidence": confidence,
                "artifact_sweep": True,
            },
            "action": "full_image_first_then_quality_gate_then_overlap_crops_if_needed",
            "reason": "full_technical_artifact_sweep",
        })

    return {
        "jobs": jobs,
        "technical_visuals": technical_visuals,
        "eligible_sweep_jobs": len(jobs),
        "skipped_normal_picture_routes": skipped_normal_picture_routes,
        "normal_picture_indices": sorted(normal_indices),
    }
