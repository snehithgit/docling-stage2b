from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _unique_target(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(2, 10_000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"Could not allocate quarantine path for {name}")


def _move(path: Path, quarantine_dir: Path, *, prefix: str) -> dict[str, str] | None:
    if not path.exists():
        return None
    target = _unique_target(quarantine_dir, f"{prefix}__{path.name}")
    shutil.move(str(path), str(target))
    return {"original": str(path), "quarantined": str(target)}


def quarantine_book_artifacts(config: Any, book: dict[str, Any]) -> dict[str, Any]:
    """Move book-owned artifacts out of all watched roots before DB deletion.

    Source manuals are intentionally quarantined rather than destroyed.  This
    prevents the input watcher or converted-folder importer from immediately
    re-adding a deleted book while still giving the operator a manual recovery
    path if the deletion was accidental.
    """
    job_id = int(book.get("id") or 0)
    conversion_job_id = int(book.get("conversion_job_id") or 0)
    stamp = _timestamp_slug()
    prefix = f"{stamp}__book{job_id}__conversion{conversion_job_id}"
    moves: list[dict[str, str]] = []

    source_kind = str(book.get("conversion_source_kind") or book.get("source_kind") or "watcher")
    source_name = str(book.get("conversion_filename") or book.get("source_filename") or "").strip()
    if source_kind != "converted_folder" and source_name:
        moved = _move(
            Path(config.input_dir) / Path(source_name).name,
            Path(config.input_dir) / "_deleted_books",
            prefix=prefix,
        )
        if moved:
            moves.append(moved)

    output_name = str(book.get("conversion_output_filename") or book.get("output_filename") or "").strip()
    if output_name:
        moved = _move(
            Path(config.output_dir) / Path(output_name).name,
            Path(config.output_dir) / "_deleted_books",
            prefix=prefix,
        )
        if moved:
            moves.append(moved)

    result_dir_name = str(book.get("result_dir") or "").strip()
    if result_dir_name:
        moved = _move(
            Path(config.processed_dir) / Path(result_dir_name).name,
            Path(config.processed_dir) / "_deleted_books",
            prefix=prefix,
        )
        if moved:
            moves.append(moved)

    manifest_dir = Path(config.processed_dir) / "_deleted_books"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "docling-deleted-book/v1",
        "deleted_at": datetime.now(UTC).isoformat(),
        "postprocess_job_id": job_id,
        "conversion_job_id": conversion_job_id,
        "source_filename": book.get("source_filename") or source_name,
        "source_kind": source_kind,
        "output_filename": output_name,
        "result_dir": result_dir_name,
        "moves": moves,
        "note": "Book removed from the active pipeline. Files were quarantined, not destroyed.",
    }
    manifest_path = _unique_target(manifest_dir, f"{prefix}__deletion.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"moves": moves, "manifest": str(manifest_path)}


def restore_quarantined_artifacts(quarantine: dict[str, Any]) -> list[str]:
    """Best-effort rollback used only when database deletion fails."""
    errors: list[str] = []
    for move in reversed(list(quarantine.get("moves") or [])):
        source = Path(str(move.get("quarantined") or ""))
        destination = Path(str(move.get("original") or ""))
        if not source.exists():
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        except OSError as exc:
            errors.append(f"{source} -> {destination}: {exc}")
    manifest = Path(str(quarantine.get("manifest") or ""))
    if manifest.is_file():
        try:
            manifest.unlink()
        except OSError as exc:
            errors.append(f"{manifest}: {exc}")
    return errors
