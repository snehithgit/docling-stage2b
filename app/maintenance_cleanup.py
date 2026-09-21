from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .equipment_scope import load_registry

_ERROR_RE = re.compile(r"^stage2b_job_(\d+)_attempt_\d+_error\.json$")
_CHECKPOINT_RE = re.compile(r"^checkpoint_(\d+)\.json$")

_CATEGORY_LABELS = {
    "superseded_verification_errors": "Superseded verification errors",
    "completed_checkpoints": "Completed-job checkpoints",
    "orphan_equipment_indexes": "Orphan equipment indexes",
    "stale_retrieval_quality": "Outdated retrieval metadata",
    "resolved_endpoint_outages": "Resolved endpoint outage logs",
}


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size) if path.is_file() else 0
    except OSError:
        return 0


def _directory_stats(path: Path) -> tuple[int, int]:
    files = 0
    total = 0
    try:
        for child in path.rglob("*"):
            if child.is_file():
                files += 1
                total += _file_size(child)
    except OSError:
        pass
    return files, total


def _candidate(path: Path, root: Path, category: str, reason: str, *, kind: str = "file") -> dict[str, Any]:
    if kind == "directory":
        file_count, size_bytes = _directory_stats(path)
    else:
        file_count, size_bytes = 1, _file_size(path)
    return {
        "category": category,
        "category_label": _CATEGORY_LABELS[category],
        "path": path.relative_to(root).as_posix(),
        "kind": kind,
        "file_count": file_count,
        "size_bytes": size_bytes,
        "reason": reason,
    }


def _preview_token(candidates: list[dict[str, Any]], retrieval_rule_version: str) -> str:
    payload = json.dumps(
        {
            "retrieval_rule_version": str(retrieval_rule_version),
            "items": [
                {
                    "path": str(item.get("path") or ""),
                    "kind": str(item.get("kind") or ""),
                    "category": str(item.get("category") or ""),
                    "file_count": int(item.get("file_count") or 0),
                    "size_bytes": int(item.get("size_bytes") or 0),
                }
                for item in candidates
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def scan_stale_files(processed_dir: Path, *, retrieval_rule_version: str) -> dict[str, Any]:
    """Find derived artifacts that are provably stale and safe to delete.

    The scanner intentionally avoids source manuals, converted ZIPs, Stage 2C/3
    canonical outputs, current verifier result JSON, and active machine indexes.
    A path is included only when a deterministic replacement/current artifact is
    already present, or when its owner no longer exists in the equipment registry.
    """
    root = Path(processed_dir)
    candidates: list[dict[str, Any]] = []
    if not root.is_dir():
        return {
            "processed_dir": str(root), "exists": False, "items": [],
            "categories": {}, "candidate_items": 0, "candidate_files": 0,
            "candidate_bytes": 0,
            "confirmation_token": _preview_token([], retrieval_rule_version),
        }

    # Old retry diagnostics are redundant once the same verification job has a
    # final successful result artifact. This is the large outage-churn case.
    for verification_dir in root.glob("*/verification"):
        if not verification_dir.is_dir():
            continue
        try:
            children = list(verification_dir.iterdir())
        except OSError:
            continue
        names = {child.name for child in children if child.is_file()}
        for path in children:
            if not path.is_file():
                continue
            match = _ERROR_RE.match(path.name)
            if match:
                job_id = int(match.group(1))
                final_name = f"stage2b_job_{job_id:06d}.json"
                if final_name in names:
                    candidates.append(_candidate(
                        path, root, "superseded_verification_errors",
                        f"Job {job_id} has a final successful verification artifact.",
                    ))
                continue
            checkpoint = _CHECKPOINT_RE.match(path.name)
            if checkpoint:
                job_id = int(checkpoint.group(1))
                final_name = f"stage2b_job_{job_id:06d}.json"
                if final_name in names:
                    candidates.append(_candidate(
                        path, root, "completed_checkpoints",
                        f"Job {job_id} is complete; its inference checkpoint is no longer needed.",
                    ))

    # A machine embedding directory is stale when the equipment registry no
    # longer contains that exact equipment id.
    registry_file = root / "equipment_registry.json"
    registry_valid = False
    valid_equipment_ids: set[str] = set()
    if registry_file.is_file():
        try:
            raw_registry = json.loads(registry_file.read_text(encoding="utf-8"))
            registry_valid = (
                isinstance(raw_registry, dict)
                and raw_registry.get("schema") == "docling-equipment-registry/v1"
                and isinstance(raw_registry.get("equipment"), list)
            )
        except (OSError, json.JSONDecodeError, TypeError):
            registry_valid = False
        if registry_valid:
            registry = load_registry(root)
            valid_equipment_ids = {
                str(item.get("equipment_id") or "").strip()
                for item in (registry.get("equipment") or [])
                if str(item.get("equipment_id") or "").strip()
            }
    equipment_root = root / "equipment_embedding_index"
    if registry_valid and equipment_root.is_dir():
        try:
            equipment_dirs = list(equipment_root.iterdir())
        except OSError:
            equipment_dirs = []
        for path in equipment_dirs:
            if path.is_dir() and path.name not in valid_equipment_ids:
                candidates.append(_candidate(
                    path, root, "orphan_equipment_indexes",
                    f"Equipment id {path.name!r} is not present in equipment_registry.json.",
                    kind="directory",
                ))

    # Circuit-breaker outage logs are intentionally one file per outage. Once
    # the endpoint has recovered they are historical diagnostics and can be
    # cleared safely; an active outage file is never a deletion candidate.
    outage_dir = root / "_verification_outages"
    if outage_dir.is_dir():
        for outage_path in outage_dir.glob("*_outage_*.json"):
            try:
                outage = json.loads(outage_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if outage.get("recovered_at_epoch"):
                candidates.append(_candidate(
                    outage_path, root, "resolved_endpoint_outages",
                    f"{outage.get('provider') or 'Verifier'} endpoint outage has recovered.",
                ))

    # retrieval_quality.json is derived metadata. If its rule version is old or
    # absent, deleting it is safe; Revalidate all + rebuild / retrieval refresh
    # regenerates the current version from canonical Stage 3 data.
    for quality_path in root.glob("*/retrieval_quality.json"):
        try:
            payload = json.loads(quality_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            payload = {}
        actual = str(payload.get("retrieval_rule_version") or "").strip()
        if actual != str(retrieval_rule_version):
            candidates.append(_candidate(
                quality_path, root, "stale_retrieval_quality",
                f"Retrieval rule is {actual or 'missing'}; current rule is {retrieval_rule_version}.",
            ))

    # Stable ordering keeps preview and delete results easy to audit.
    candidates.sort(key=lambda item: (item["category"], item["path"]))
    categories: dict[str, dict[str, Any]] = {}
    for item in candidates:
        cat = categories.setdefault(item["category"], {
            "label": item["category_label"], "items": 0, "files": 0, "bytes": 0,
        })
        cat["items"] += 1
        cat["files"] += int(item["file_count"])
        cat["bytes"] += int(item["size_bytes"])
    return {
        "processed_dir": str(root),
        "exists": True,
        "items": candidates,
        "categories": categories,
        "candidate_items": len(candidates),
        "candidate_files": sum(int(item["file_count"]) for item in candidates),
        "candidate_bytes": sum(int(item["size_bytes"]) for item in candidates),
        "confirmation_token": _preview_token(candidates, retrieval_rule_version),
    }


def clear_stale_files(processed_dir: Path, *, retrieval_rule_version: str, confirmation_token: str) -> dict[str, Any]:
    """Rescan then remove only the exact candidate set the user previewed."""
    root = Path(processed_dir)
    preview = scan_stale_files(root, retrieval_rule_version=retrieval_rule_version)
    expected = str(preview.get("confirmation_token") or "")
    supplied = str(confirmation_token or "")
    if not supplied or supplied != expected:
        raise ValueError("Stale-file preview changed or confirmation token is missing; scan again before deleting.")
    removed_items = 0
    removed_files = 0
    removed_bytes = 0
    errors: list[dict[str, str]] = []
    removed: list[dict[str, Any]] = []

    for item in preview.get("items") or []:
        path = root / str(item.get("path") or "")
        if not _inside(root, path):
            errors.append({"path": str(item.get("path") or ""), "error": "Path escaped processed directory"})
            continue
        try:
            if item.get("kind") == "directory":
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    continue
            else:
                if path.is_file():
                    path.unlink()
                else:
                    continue
            removed_items += 1
            removed_files += int(item.get("file_count") or 0)
            removed_bytes += int(item.get("size_bytes") or 0)
            removed.append(item)
        except OSError as exc:
            errors.append({"path": str(item.get("path") or ""), "error": str(exc)})

    after = scan_stale_files(root, retrieval_rule_version=retrieval_rule_version)
    return {
        "removed_items": removed_items,
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "errors": errors,
        "removed": removed,
        "remaining": after,
    }
