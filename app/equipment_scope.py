from __future__ import annotations

import json
import re
import shutil
import uuid
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .equipment_index_lock import EQUIPMENT_INDEX_SWAP_LOCK

SCHEMA = "docling-equipment-registry/v1"
_REGISTRY_LOCK = threading.RLock()

MANUAL_TYPES = (
    "description", "operation", "maintenance", "electrical", "hydraulic",
    "parts", "tools", "service", "installation", "other",
)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def registry_path(processed_dir: Path) -> Path:
    return Path(processed_dir) / "equipment_registry.json"


def _safe_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:48] or "equipment"


def load_registry(processed_dir: Path) -> dict[str, Any]:
    path = registry_path(processed_dir)
    if not path.is_file():
        return {"schema": SCHEMA, "updated_at": None, "equipment": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"schema": SCHEMA, "updated_at": None, "equipment": []}
    if payload.get("schema") != SCHEMA or not isinstance(payload.get("equipment"), list):
        return {"schema": SCHEMA, "updated_at": None, "equipment": []}
    return payload


def save_registry(processed_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path = registry_path(processed_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = {"schema": SCHEMA, "updated_at": _utcnow(), "equipment": list(payload.get("equipment") or [])}
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)
    return normalized


def _book_map(books: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {
        int(book.get("postprocess_job_id") or 0): book
        for book in books
        if int(book.get("postprocess_job_id") or 0) > 0
    }


def equipment_catalog(processed_dir: Path, books: list[dict[str, Any]]) -> dict[str, Any]:
    registry = load_registry(processed_dir)
    by_job = _book_map(books)
    assigned: set[int] = set()
    equipment_rows: list[dict[str, Any]] = []
    for raw in registry.get("equipment") or []:
        manuals: list[dict[str, Any]] = []
        for item in raw.get("manuals") or []:
            try:
                job_id = int(item.get("postprocess_job_id") or 0)
            except (TypeError, ValueError):
                continue
            book = by_job.get(job_id)
            if not book:
                continue
            assigned.add(job_id)
            authority_status = str(item.get("authority_status") or "authoritative")
            manuals.append({
                "postprocess_job_id": job_id,
                "source_filename": book.get("source_filename"),
                "result_dir": book.get("result_dir"),
                "index_ready": bool(book.get("index_ready")),
                "visual_index_ready": bool(book.get("visual_index_ready")),
                "hybrid_index_ready": bool(book.get("hybrid_index_ready")),
                "manual_type": str(item.get("manual_type") or "other"),
                "revision": str(item.get("revision") or ""),
                "revision_date": str(item.get("revision_date") or ""),
                "authority_status": authority_status,
                "supersedes_postprocess_job_id": item.get("supersedes_postprocess_job_id"),
                "active_for_rag": authority_status == "authoritative",
            })
        active_manuals = [item for item in manuals if item.get("active_for_rag")]
        equipment_rows.append({
            "equipment_id": str(raw.get("equipment_id") or ""),
            "name": str(raw.get("name") or "Unnamed equipment"),
            "manufacturer": str(raw.get("manufacturer") or ""),
            "model": str(raw.get("model") or ""),
            "notes": str(raw.get("notes") or ""),
            "manuals": manuals,
            "manual_count": len(manuals),
            "active_manual_count": len(active_manuals),
            "index_ready_count": sum(bool(item.get("index_ready")) for item in active_manuals),
            "searchable": bool(active_manuals) and all(item.get("index_ready") for item in active_manuals),
            "hybrid_ready": bool(active_manuals) and all(item.get("hybrid_index_ready") for item in active_manuals),
            "hybrid_ready_count": sum(bool(item.get("hybrid_index_ready")) for item in active_manuals),
        })
    unassigned = [book for job_id, book in by_job.items() if job_id not in assigned]
    unassigned.sort(key=lambda row: str(row.get("source_filename") or "").lower())
    equipment_rows.sort(key=lambda row: row["name"].lower())
    return {"schema": SCHEMA, "equipment": equipment_rows, "unassigned_books": unassigned, "manual_types": list(MANUAL_TYPES)}


def resolve_equipment_books(processed_dir: Path, books: list[dict[str, Any]], equipment_id: str) -> list[dict[str, Any]]:
    target = str(equipment_id or "").strip()
    if not target:
        return []
    catalog = equipment_catalog(processed_dir, books)
    for equipment in catalog.get("equipment") or []:
        if equipment.get("equipment_id") == target:
            ids = {int(item["postprocess_job_id"]) for item in equipment.get("manuals") or [] if item.get("active_for_rag", True)}
            return [book for book in books if int(book.get("postprocess_job_id") or 0) in ids]
    return []


def _upsert_equipment_unlocked(
    processed_dir: Path,
    books: list[dict[str, Any]],
    *,
    name: str,
    manufacturer: str = "",
    model: str = "",
    notes: str = "",
    manuals: list[dict[str, Any]],
    equipment_id: str | None = None,
) -> dict[str, Any]:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Equipment name is required.")
    by_job = _book_map(books)
    registry = load_registry(processed_dir)
    rows = list(registry.get("equipment") or [])
    current_id = str(equipment_id or "").strip()
    existing_manuals: dict[int, dict[str, Any]] = {}
    for row in rows:
        if current_id and str(row.get("equipment_id") or "") == current_id:
            existing_manuals = {int(item.get("postprocess_job_id") or 0): dict(item) for item in row.get("manuals") or []}
            break
    normalized_manuals: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in manuals or []:
        try:
            job_id = int(item.get("postprocess_job_id") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Manual job ID must be an integer.") from exc
        if job_id <= 0 or job_id not in by_job:
            raise ValueError(f"Manual job {job_id} is not an available Stage 3 book.")
        if job_id in seen:
            continue
        seen.add(job_id)
        manual_type = str(item.get("manual_type") or "other").strip().lower()
        if manual_type not in MANUAL_TYPES:
            raise ValueError(f"Unsupported manual type: {manual_type}")
        previous = existing_manuals.get(job_id) or {}
        authority_status = str(item.get("authority_status") or previous.get("authority_status") or "authoritative").strip().lower()
        if authority_status not in {"authoritative", "historical", "draft"}:
            raise ValueError(f"Unsupported manual authority status: {authority_status}")
        supersedes = item.get("supersedes_postprocess_job_id")
        if supersedes in {None, ""}:
            supersedes = previous.get("supersedes_postprocess_job_id")
        try:
            supersedes = int(supersedes) if supersedes not in {None, ""} else None
        except (TypeError, ValueError) as exc:
            raise ValueError("supersedes_postprocess_job_id must be an integer") from exc
        normalized_manuals.append({
            "postprocess_job_id": job_id,
            "source_filename": str(by_job[job_id].get("source_filename") or ""),
            "manual_type": manual_type,
            "revision": str(item.get("revision") if item.get("revision") is not None else previous.get("revision") or "").strip(),
            "revision_date": str(item.get("revision_date") if item.get("revision_date") is not None else previous.get("revision_date") or "").strip(),
            "authority_status": authority_status,
            "supersedes_postprocess_job_id": supersedes,
        })
    if not normalized_manuals:
        raise ValueError("Choose at least one manual for this equipment.")

    if not any(item.get("authority_status") == "authoritative" for item in normalized_manuals):
        raise ValueError("Equipment must have at least one authoritative manual for RAG.")
    selected_ids = {int(item["postprocess_job_id"]) for item in normalized_manuals}
    for item in normalized_manuals:
        supersedes = item.get("supersedes_postprocess_job_id")
        if supersedes is not None and (supersedes == int(item["postprocess_job_id"]) or supersedes not in selected_ids):
            raise ValueError("A superseded manual must be another manual assigned to the same equipment.")
    current_id = current_id or f"eq-{_safe_id(clean_name)}-{uuid.uuid4().hex[:6]}"
    for row in rows:
        if str(row.get("equipment_id") or "") == current_id:
            continue
        used = {int(item.get("postprocess_job_id") or 0) for item in row.get("manuals") or []}
        conflict = seen & used
        if conflict:
            ids = ", ".join(str(value) for value in sorted(conflict))
            raise ValueError(f"Manual job(s) {ids} already belong to another equipment scope.")

    new_row = {
        "equipment_id": current_id,
        "name": clean_name,
        "manufacturer": str(manufacturer or "").strip(),
        "model": str(model or "").strip(),
        "notes": str(notes or "").strip(),
        "manuals": normalized_manuals,
    }
    for index, row in enumerate(rows):
        if str(row.get("equipment_id") or "") == current_id:
            rows[index] = new_row
            break
    else:
        rows.append(new_row)
    save_registry(processed_dir, {"equipment": rows})
    return new_row


def _delete_equipment_unlocked(processed_dir: Path, equipment_id: str) -> bool:
    registry = load_registry(processed_dir)
    rows = list(registry.get("equipment") or [])
    target = str(equipment_id or "").strip()
    kept = [row for row in rows if str(row.get("equipment_id") or "") != target]
    if len(kept) == len(rows):
        return False
    save_registry(processed_dir, {"equipment": kept})
    # Machine embeddings are derived from the equipment registry. Once the
    # scope is deleted, keeping its persisted vectors only creates an orphan
    # that can never be selected again. Remove it immediately instead of
    # requiring the stale-file maintenance action later.
    index_dir = Path(processed_dir) / "equipment_embedding_index" / target
    with EQUIPMENT_INDEX_SWAP_LOCK:
        if index_dir.is_dir():
            shutil.rmtree(index_dir)
    return True


def _remove_manual_from_equipment_unlocked(processed_dir: Path, postprocess_job_id: int) -> dict[str, Any]:
    """Remove one deleted book from equipment scopes and invalidate machine vectors.

    A deleted manual must never remain in the machine registry because an old
    persisted embedding corpus could otherwise continue to reference content
    that no longer exists.  We do not auto-promote historical/draft manuals to
    authoritative when an authoritative manual is removed; that is a human
    equipment-scope decision.
    """
    job_id = int(postprocess_job_id)
    registry = load_registry(processed_dir)
    rows = list(registry.get("equipment") or [])
    affected: list[str] = []
    removed = 0
    kept_equipment: list[dict[str, Any]] = []

    for raw in rows:
        equipment = dict(raw)
        equipment_id = str(equipment.get("equipment_id") or "").strip()
        manuals: list[dict[str, Any]] = []
        changed = False
        for raw_manual in equipment.get("manuals") or []:
            item = dict(raw_manual)
            try:
                manual_job_id = int(item.get("postprocess_job_id") or 0)
            except (TypeError, ValueError):
                manuals.append(item)
                continue
            if manual_job_id == job_id:
                removed += 1
                changed = True
                continue
            if item.get("supersedes_postprocess_job_id") not in {None, ""}:
                try:
                    supersedes = int(item.get("supersedes_postprocess_job_id"))
                except (TypeError, ValueError):
                    supersedes = None
                if supersedes == job_id:
                    item["supersedes_postprocess_job_id"] = None
                    changed = True
            manuals.append(item)

        if changed and equipment_id:
            affected.append(equipment_id)
        if manuals:
            equipment["manuals"] = manuals
            kept_equipment.append(equipment)

    if removed:
        save_registry(processed_dir, {"equipment": kept_equipment})
        with EQUIPMENT_INDEX_SWAP_LOCK:
            for equipment_id in affected:
                index_dir = Path(processed_dir) / "equipment_embedding_index" / equipment_id
                if index_dir.is_dir():
                    shutil.rmtree(index_dir)

    return {
        "removed_assignments": removed,
        "affected_equipment_ids": affected,
        "deleted_empty_equipment": max(0, len(rows) - len(kept_equipment)),
    }


def upsert_equipment(processed_dir: Path, books: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    with _REGISTRY_LOCK:
        return _upsert_equipment_unlocked(processed_dir, books, **kwargs)


def delete_equipment(processed_dir: Path, equipment_id: str) -> bool:
    with _REGISTRY_LOCK:
        return _delete_equipment_unlocked(processed_dir, equipment_id)


def remove_manual_from_equipment(processed_dir: Path, postprocess_job_id: int) -> dict[str, Any]:
    with _REGISTRY_LOCK:
        return _remove_manual_from_equipment_unlocked(processed_dir, postprocess_job_id)
