from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def _sha256_parts(parts: Iterable[bytes]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part)
        h.update(b"\0")
    return h.hexdigest()



def verification_rows_for_stage2c(rows: list[dict[str, Any]], *, artifact_sweep_required: bool = True) -> list[dict[str, Any]]:
    """Return the verification rows that define Stage 2C freshness/readiness.

    When the technical artifact sweep is optional, unfinished sweep rows do not
    block Stage 2C and are excluded from its signature. A sweep result enters the
    signature as soon as it completes, which deliberately marks Stage 2C stale so
    the newly available evidence can be incorporated on rebuild.
    """
    if artifact_sweep_required:
        return list(rows)
    selected: list[dict[str, Any]] = []
    for row in rows:
        is_sweep = str(row.get("code") or "") == "FULL_TECHNICAL_VISUAL"
        if not is_sweep or str(row.get("status") or "") == "completed":
            selected.append(row)
    return selected

def verification_signature(rows: list[dict[str, Any]]) -> str:
    """Stable signature of the current Stage 2B outputs for one book."""
    normalized: list[dict[str, Any]] = []
    for row in sorted(
        rows,
        key=lambda item: (
            str(item.get("generation") or ""),
            str(item.get("route_id") or ""),
            str(item.get("target") or ""),
            int(item.get("id") or 0),
        ),
    ):
        normalized.append({
            "id": int(row.get("id") or 0),
            "generation": str(row.get("generation") or ""),
            "route_id": str(row.get("route_id") or ""),
            "target": str(row.get("target") or ""),
            "status": str(row.get("status") or ""),
            "verdict": str(row.get("verdict") or ""),
            "completed_at": str(row.get("completed_at") or ""),
            "model": str(row.get("model") or ""),
            "result_json": str(row.get("result_json") or ""),
            "artifact_path": str(row.get("artifact_path") or ""),
            "error_type": str(row.get("error_type") or ""),
            "error_message": str(row.get("error_message") or ""),
        })
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_signature(path: Path) -> str:
    path = Path(path)
    if not path.is_file():
        return "missing"
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stage2c_output_signature(result_dir: Path) -> str:
    result_dir = Path(result_dir)
    paths = [
        result_dir / "stage2c_backfill.json",
        result_dir / "correction_ledger.json",
        result_dir / "chunk_overlays.jsonl",
    ]
    return _sha256_parts(
        [f"{path.name}:{file_signature(path)}".encode("utf-8") for path in paths]
    )


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}



def result_dir_job_id(result_dir: Path) -> int | None:
    import re
    match = re.search(r"__job(\d+)(?:__|$)", Path(result_dir).name)
    return int(match.group(1)) if match else None


def identity_metadata_status(result_dir: Path, expected_job_id: int | None = None) -> dict[str, Any]:
    """Audit persisted derived-state job IDs against the authoritative result directory.

    The directory name is created by the post-process store and is the stable
    identity for persisted book artifacts.  This function never guesses when
    the directory lacks a job id.
    """
    result_dir = Path(result_dir)
    authoritative = result_dir_job_id(result_dir)
    if expected_job_id is not None and authoritative is not None and int(expected_job_id) != authoritative:
        return {"ok": False, "authoritative_job_id": authoritative, "expected_job_id": int(expected_job_id), "mismatches": {"runtime": int(expected_job_id)}}
    mismatches: dict[str, int] = {}
    for name in ("stage2c_backfill.json", "stage3_chunking.json", "retrieval_quality.json"):
        payload = load_json(result_dir / name)
        if not payload or payload.get("postprocess_job_id") is None or authoritative is None:
            continue
        try:
            stored = int(payload.get("postprocess_job_id"))
        except (TypeError, ValueError):
            continue
        if stored != authoritative:
            mismatches[name] = stored
    return {
        "ok": authoritative is not None and not mismatches,
        "authoritative_job_id": authoritative,
        "expected_job_id": int(expected_job_id) if expected_job_id is not None else None,
        "mismatches": mismatches,
    }


def repair_identity_metadata(result_dir: Path, expected_job_id: int | None = None) -> dict[str, Any]:
    """Repair only unambiguous derived JSON metadata job IDs; never move data.

    Text/chunks/ledger content is untouched.  If the runtime job id disagrees
    with the directory identity the function refuses to repair.
    """
    result_dir = Path(result_dir)
    status = identity_metadata_status(result_dir, expected_job_id)
    authoritative = status.get("authoritative_job_id")
    if authoritative is None or (expected_job_id is not None and int(expected_job_id) != int(authoritative)):
        return {**status, "repaired": [], "repairable": False}
    repaired: list[str] = []
    for name in ("stage2c_backfill.json", "stage3_chunking.json", "retrieval_quality.json"):
        path = result_dir / name
        payload = load_json(path)
        if not payload or payload.get("postprocess_job_id") is None:
            continue
        try:
            stored = int(payload.get("postprocess_job_id"))
        except (TypeError, ValueError):
            continue
        if stored == int(authoritative):
            continue
        payload["identity_repair"] = {
            "previous_postprocess_job_id": stored,
            "authoritative_postprocess_job_id": int(authoritative),
        }
        payload["postprocess_job_id"] = int(authoritative)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        repaired.append(name)
    final = identity_metadata_status(result_dir, expected_job_id)
    return {**final, "repaired": repaired, "repairable": True}

def stage2c_freshness(result_dir: Path, verification_rows: list[dict[str, Any]], *, rule_version: str | None = None, artifact_sweep_required: bool = True) -> dict[str, Any]:
    result_dir = Path(result_dir)
    state = load_json(result_dir / "stage2c_backfill.json")
    signature_rows = verification_rows_for_stage2c(verification_rows, artifact_sweep_required=artifact_sweep_required)
    current_verification = verification_signature(signature_rows)
    ready_status = str(state.get("status") or "") == "completed"
    signature_match = bool(state.get("verification_signature")) and str(state.get("verification_signature")) == current_verification
    rule_match = (not rule_version) or str(state.get("rule_version") or "") == str(rule_version)
    outputs = (result_dir / "correction_ledger.json").is_file() and (result_dir / "chunk_overlays.jsonl").is_file()
    ready = bool(ready_status and signature_match and rule_match and outputs)
    reason = None
    if not state:
        reason = "stage2c_not_built"
    elif not ready_status:
        reason = f"stage2c_{str(state.get('status') or 'not_ready')}"
    elif not signature_match:
        reason = "stage2c_stale_after_verification"
    elif not rule_match:
        reason = "stage2c_rule_version_stale"
    elif not outputs:
        reason = "stage2c_outputs_missing"
    return {
        "ready": ready,
        "reason": reason,
        "status": str(state.get("status") or "not_built"),
        "verification_signature": current_verification,
        "signature_route_count": len(signature_rows),
        "artifact_sweep_required": bool(artifact_sweep_required),
        "recorded_verification_signature": state.get("verification_signature"),
        "rule_version": rule_version,
        "recorded_rule_version": state.get("rule_version"),
        "output_signature": stage2c_output_signature(result_dir) if outputs else None,
        "state": state,
    }


def stage3_freshness(result_dir: Path, stage2c_info: dict[str, Any], *, retrieval_rule_version: str | None = None) -> dict[str, Any]:
    result_dir = Path(result_dir)
    state = load_json(result_dir / "stage3_chunking.json")
    expected = str(stage2c_info.get("output_signature") or "")
    chunks_output = (result_dir / "chunks.jsonl").is_file()
    retrieval_output = (result_dir / "retrieval_index.jsonl").is_file()
    outputs = chunks_output and retrieval_output
    ready_status = str(state.get("status") or "") == "completed"
    signature_match = bool(expected) and bool(state.get("stage2c_signature")) and str(state.get("stage2c_signature")) == expected
    quality = load_json(result_dir / "retrieval_quality.json")
    retrieval_rule_match = (not retrieval_rule_version) or str(quality.get("retrieval_rule_version") or "") == str(retrieval_rule_version)
    canonical_ready = bool(stage2c_info.get("ready") and ready_status and signature_match and chunks_output)
    ready = bool(canonical_ready and retrieval_output and retrieval_rule_match)
    reason = None
    if not stage2c_info.get("ready"):
        reason = "stage2c_not_current"
    elif not state:
        reason = "stage3_not_built"
    elif not ready_status:
        reason = f"stage3_{str(state.get('status') or 'not_ready')}"
    elif not signature_match:
        reason = "stage3_stale_after_stage2c"
    elif not chunks_output:
        reason = "stage3_chunks_missing"
    elif not retrieval_output:
        reason = "retrieval_index_missing"
    elif not retrieval_rule_match:
        reason = "retrieval_rules_stale"
    return {
        "ready": ready,
        "reason": reason,
        "status": str(state.get("status") or "not_built"),
        "stage2c_signature": expected or None,
        "recorded_stage2c_signature": state.get("stage2c_signature"),
        "state": state,
        "canonical_ready": canonical_ready,
        "retrieval_rule_version": retrieval_rule_version,
        "recorded_retrieval_rule_version": quality.get("retrieval_rule_version"),
        "chunks_available": chunks_output,
        "retrieval_index_available": retrieval_output,
    }
