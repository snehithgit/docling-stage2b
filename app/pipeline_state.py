from __future__ import annotations

import hashlib
import json
import time
import os
import uuid
from pathlib import Path
from typing import Any, Iterable


HUMAN_VISUAL_RECOVERY_CODE = "HUMAN_VISUAL_EVIDENCE_RECOVERY"


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
    selected: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("code") or "")
        # Human evidence recovery is an additive queue action that merges into an
        # already-authoritative vision ledger entry. It must never become a new
        # Stage-2C source row or change the Stage-2C verification signature.
        if code == HUMAN_VISUAL_RECOVERY_CODE:
            continue
        is_sweep = code == "FULL_TECHNICAL_VISUAL"
        if artifact_sweep_required or not is_sweep or str(row.get("status") or "") == "completed":
            selected.append(row)
    return selected

def required_verification_state(
    rows: list[dict[str, Any]],
    *,
    discovery_current: bool,
    artifact_sweep_required: bool = True,
    expected_total: int | None = None,
) -> dict[str, Any]:
    """Canonical Stage 2B dependency state used by sequencing and UI.

    Rows excluded by verification_rows_for_stage2c are advisory/additive and
    must not block Stage 2C. A book with no required rows is complete only after
    route discovery is known current; this is the clean zero-route case.
    """
    selected = verification_rows_for_stage2c(
        rows, artifact_sweep_required=artifact_sweep_required
    )
    counts = {
        status: sum(str(row.get("status") or "") == status for row in selected)
        for status in ("pending", "processing", "completed", "failed")
    }
    snapshot_missing = bool(int(expected_total or 0) > 0 and not rows)
    ready = (
        not snapshot_missing
        and counts["completed"] == len(selected)
        and counts["pending"] == 0
        and counts["processing"] == 0
        and counts["failed"] == 0
        and (bool(selected) or bool(discovery_current))
    )
    return {
        "ready": bool(ready),
        "total": len(selected),
        **counts,
        "discovery_current": bool(discovery_current),
        "raw_total": len(rows),
        "excluded_rows": max(0, len(rows) - len(selected)),
        "snapshot_missing": snapshot_missing,
    }


def resolve_pipeline_stage(
    *,
    stage2a_ready: bool,
    verification: dict[str, Any],
    stage2c_ready: bool,
    stage2c_reason: str | None = None,
    structural_review_pending: int = 0,
    verifier_audit_pending: int = 0,
    verifier_audit_blocking: int = 0,
    audit_bypassed: bool = False,
    stage3_ready: bool = False,
    stage3_reason: str | None = None,
) -> dict[str, Any]:
    """Return the single operator-facing next stage for the canonical sequence."""
    if not stage2a_ready:
        return {"next_stage": "stage2a", "blocked_reason": None}
    if not verification.get("ready"):
        if int(verification.get("failed") or 0):
            reason = "Verification has failed required routes; retry them before finalization."
        elif not verification.get("discovery_current") and not int(verification.get("raw_total") or 0):
            reason = "Verification routes are still being prepared for this book."
        else:
            reason = "Required verification must finish before Stage 2C."
        return {"next_stage": "stage2b", "blocked_reason": reason}
    if not stage2c_ready:
        return {"next_stage": "stage2c", "blocked_reason": stage2c_reason}
    if int(structural_review_pending or 0) > 0:
        return {
            "next_stage": "stage2a_human_review",
            "blocked_reason": f"{int(structural_review_pending)} structural review item(s) must be resolved before Stage 3.",
        }
    if int(verifier_audit_blocking or 0) > 0:
        return {
            "next_stage": "verifier_audit",
            "blocked_reason": f"{int(verifier_audit_blocking)} verifier audit item(s) require a human decision before Stage 3.",
        }
    if not stage3_ready:
        return {"next_stage": "stage3", "blocked_reason": stage3_reason}
    return {
        "next_stage": "post_stage3",
        "blocked_reason": None,
        "verifier_audit_pending": int(verifier_audit_pending or 0),
        "audit_bypassed": bool(audit_bypassed),
    }

def pipeline_blockers(pipeline: dict[str, Any], *, book_status: str | None = None) -> list[dict[str, Any]]:
    """Return the canonical current blocker contract for one book."""
    next_stage = str(pipeline.get("next_stage") or "stage2a")
    verification = pipeline.get("required_verification") or {}
    reason = str(pipeline.get("blocked_reason") or "").strip()

    def one(code, stage, severity, message, *, operator, automatic, count=0, details=None):
        return [{"blocker_type": "pipeline", "code": code, "severity": severity,
                 "stage": stage, "message": message,
                 "operator_action_required": bool(operator),
                 "auto_resolvable": bool(automatic),
                 "count": max(0, int(count or 0)),
                 "details": dict(details or {})}]

    if next_stage == "stage2a":
        failed = str(book_status or "") == "failed"
        return one("stage2a_failed" if failed else "stage2a_in_progress", "stage2a",
                   "critical" if failed else "active",
                   reason or ("Extraction analysis failed and needs operator attention." if failed else "Extraction analysis is still running."),
                   operator=failed, automatic=not failed)
    if next_stage == "stage2b":
        failed = int(verification.get("failed") or 0)
        if failed:
            return one("stage2b_verification_failed", "stage2b", "critical",
                       reason or "Required verification has failed routes.",
                       operator=True, automatic=False, count=failed)
        if verification.get("snapshot_missing"):
            return one("stage2b_snapshot_missing", "stage2b", "attention",
                       reason or "The required verification snapshot is incomplete.",
                       operator=True, automatic=False, count=int(verification.get("total") or 0))
        waiting = int(verification.get("pending") or 0) + int(verification.get("processing") or 0)
        return one("stage2b_verification_pending", "stage2b", "active",
                   reason or "Required verification is still pending.",
                   operator=False, automatic=True, count=waiting)
    if next_stage == "stage2c":
        message = reason or "Stage 2C is not current."
        hard = any(token in message.casefold() for token in ("failed", "error", "incomplete"))
        return one("stage2c_not_current", "stage2c", "critical" if hard else "active",
                   message, operator=hard, automatic=not hard,
                   details={"reason": pipeline.get("stage2c_reason")})
    if next_stage == "stage2a_human_review":
        return one("stage2a_structural_review_required", next_stage, "attention",
                   reason or "Structural source review is required before Stage 3.",
                   operator=True, automatic=False,
                   count=int(pipeline.get("stage2a_human_review_pending") or 0))
    if next_stage == "verifier_audit":
        count = int(pipeline.get("verifier_audit_blocking") or pipeline.get("verifier_audit_pending") or 0)
        return one("verifier_audit_required", next_stage, "attention",
                   reason or "Verifier audit requires a human decision before Stage 3.",
                   operator=True, automatic=False, count=count)
    if next_stage == "stage3":
        message = reason or "Stage 3 chunks are not current."
        hard = any(token in message.casefold() for token in ("failed", "error", "incomplete"))
        return one("stage3_not_current", "stage3", "critical" if hard else "active",
                   message, operator=hard, automatic=not hard,
                   details={"reason": pipeline.get("stage3_reason")})
    if next_stage == "assign_machine":
        return one("manual_not_assigned_to_machine", next_stage, "attention",
                   reason or "Assign this manual to its physical machine before hybrid retrieval.",
                   operator=True, automatic=False)
    if next_stage == "machine_embedding":
        return one("machine_embedding_not_ready", next_stage, "active",
                   reason or "Machine embeddings are missing, stale, or waiting for all assigned manuals.",
                   operator=False, automatic=True,
                   details={"machine_id": pipeline.get("machine_id"),
                            "machine_name": pipeline.get("machine_name"),
                            "reason": pipeline.get("machine_embedding_reason")})
    return []


def attach_pipeline_blockers(pipeline: dict[str, Any], *, book_status: str | None = None, entity_id: int | str | None = None) -> dict[str, Any]:
    pipeline = dict(pipeline)
    blockers = pipeline_blockers(pipeline, book_status=book_status)
    if entity_id is not None:
        for blocker in blockers:
            blocker["entity_id"] = entity_id
    pipeline["blockers"] = blockers
    pipeline["primary_blocker"] = blockers[0] if blockers else None
    return pipeline


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
        # Human table-structure overlays are authoritative Stage-3 input. Any
        # saved/changed repair must make canonical chunks stale.
        result_dir / "table_structure_repairs.json",
        # Human Docling page/bbox repairs are direct Stage-3 input. Saving or
        # deactivating one must invalidate existing chunks without forcing 2A/2B.
        result_dir / "docling_page_repairs.json",
    ]
    return _sha256_parts(
        [f"{path.name}:{_ledger_content_signature(path) if path.name == 'correction_ledger.json' else file_signature(path)}".encode("utf-8") for path in paths]
    )


def stage2c_semantic_signature(result_dir: Path) -> str:
    """Hash accepted content and upstream authority, excluding backfill counters."""
    directory = Path(result_dir)
    state = load_json(directory / "stage2c_backfill.json")
    authority = {key: state.get(key) for key in ("status", "verification_signature", "rule_version")}
    parts = [json.dumps(authority, sort_keys=True).encode("utf-8")]
    for name in ("correction_ledger.json", "chunk_overlays.jsonl", "table_structure_repairs.json", "docling_page_repairs.json"):
        path = directory / name
        signature = _ledger_content_signature(path) if name == "correction_ledger.json" else file_signature(path)
        parts.append(f"{name}:{signature}".encode("utf-8"))
    return _sha256_parts(parts)


def migrate_stage3_semantic_signature(result_dir: Path, stage2c_info: dict) -> bool:
    """Migrate a proven-current legacy index without rechunking or GET writes."""
    path = Path(result_dir) / "stage3_chunking.json"
    state = load_json(path)
    if (state.get("status") != "completed" or state.get("stage2c_semantic_signature")
            or state.get("stage2c_signature") != stage2c_info.get("output_signature")
            or not stage2c_info.get("ready")
            or stage2c_output_signature(result_dir) != stage2c_info.get("output_signature")):
        return False
    state["stage2c_semantic_signature"] = stage2c_info.get("semantic_output_signature") or stage2c_semantic_signature(result_dir)
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _ledger_content_signature(path: Path) -> str:
    """Advisory AI audits do not change accepted Stage 3 input."""
    ledger = load_json(path)
    if not ledger:
        return file_signature(path)
    ledger.pop("updated_at_epoch", None)
    advisory = {"ai_review_assistant", "anomaly_review", "anomaly_review_history", "anomaly_review_decision"}
    ledger["entries"] = [
        {key: value for key, value in entry.items() if key not in advisory}
        if isinstance(entry, dict) else entry
        for entry in ledger.get("entries") or []
    ]
    return hashlib.sha256(json.dumps(ledger, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


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


def identity_metadata_status(result_dir: Path, expected_job_id: int | None = None, *, conversion_job_id: int | None = None) -> dict[str, Any]:
    """Audit persisted derived-state job IDs against the authoritative result directory.

    The directory names the conversion job. When its mapping is supplied,
    derived metadata is checked against the associated postprocess job.  This function never guesses when
    the directory lacks a job id.
    """
    result_dir = Path(result_dir)
    directory_id = result_dir_job_id(result_dir)
    authoritative = directory_id
    if conversion_job_id is not None:
        # Folder names contain the conversion ID, not the postprocess ID.
        if directory_id != int(conversion_job_id) or not expected_job_id or int(expected_job_id) <= 0:
            return {"ok": False, "authoritative_job_id": None, "expected_job_id": expected_job_id,
                    "conversion_job_id": int(conversion_job_id), "directory_job_id": directory_id,
                    "mismatches": {"conversion_directory": directory_id}}
        authoritative = int(expected_job_id)
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


def repair_identity_metadata(result_dir: Path, expected_job_id: int | None = None, *, conversion_job_id: int | None = None) -> dict[str, Any]:
    """Repair only unambiguous derived JSON metadata job IDs; never move data.

    Text/chunks/ledger content is untouched.  If the runtime job id disagrees
    with the directory identity the function refuses to repair.
    """
    result_dir = Path(result_dir)
    status = identity_metadata_status(result_dir, expected_job_id, conversion_job_id=conversion_job_id)
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
    final = identity_metadata_status(result_dir, expected_job_id, conversion_job_id=conversion_job_id)
    return {**final, "repaired": repaired, "repairable": True}


def stage2a_human_review_summary(result_dir: Path) -> dict[str, Any]:
    """Summarize durable Stage-2A structural human-review routes."""
    payload = load_json(Path(result_dir) / "routes.json")
    routes = [
        dict(route) for route in (payload.get("routes") or [])
        if isinstance(route, dict) and str(route.get("target") or "") == "human"
    ]
    pending = [route for route in routes if str(route.get("status") or "pending") == "pending"]
    resolved = [route for route in routes if str(route.get("status") or "") in {"resolved", "accepted", "dismissed"}]
    return {
        "total": len(routes),
        "pending": len(pending),
        "resolved": len(resolved),
        "blocking_review_required": len(pending),
        "pending_routes": pending,
        "routes": routes,
    }


def stage2a_structural_review_context(result_dir: Path, route_id: str, *, route: dict | None = None, diagnostics: dict | None = None) -> dict[str, Any]:
    """Return one structural route with its persisted diagnostic evidence.

    Older 40.11R ``routes.json`` files intentionally grouped diagnostics and
    retained only a count.  ``diagnostics.json`` is therefore the compatibility
    source of truth for review evidence.  Newer routes may also carry page hints,
    but human review never depends on regenerating Stage 2A solely to populate
    the UI.
    """
    result_dir = Path(result_dir)
    if route is None:
        summary = stage2a_human_review_summary(result_dir)
        route = next(
            (item for item in summary.get("routes", []) if str(item.get("route_id") or "") == str(route_id)),
            None,
        )
    if route is None:
        raise KeyError(route_id)

    code = str(route.get("code") or "")
    if diagnostics is None:
        diagnostics = load_json(result_dir / "diagnostics.json")
    signal = next(
        (item for item in (diagnostics.get("signals") or [])
         if isinstance(item, dict) and str(item.get("code") or "") == code),
        {},
    )
    items = [dict(item) for item in (signal.get("items") or signal.get("samples") or []) if isinstance(item, dict)]
    pages: list[int] = []
    for item in items:
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    pages.sort()
    evidence_items = []
    for idx, item in enumerate(items, start=1):
        page = None
        try:
            value = int(item.get("page"))
            page = value if value > 0 else None
        except (TypeError, ValueError):
            page = None
        evidence_items.append({
            "evidence_id": f"E{idx:04d}",
            "page": page,
            "data": item,
        })
    return {
        "route": route,
        "code": code,
        "signal": signal,
        "items": items,
        "evidence_items": evidence_items,
        "required_evidence_ids": [item["evidence_id"] for item in evidence_items],
        "required_pages": pages,
    }


def set_stage2a_human_review_decision(
    result_dir: Path,
    route_id: str,
    *,
    decision: str,
    note: str | None = None,
    reviewed_pages: list[int] | None = None,
    reviewed_items: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve one Stage-2A structural human-review route atomically.

    Structural review is intentionally separate from the Stage-2C text/visual
    correction ledger.  The decision is stored on the route itself so Stage 3
    can enforce the route's advertised ``review_before_chunking`` contract.
    """
    normalized = str(decision or "").strip().lower()
    if normalized not in {"accepted", "dismissed"}:
        raise ValueError("decision must be 'accepted' or 'dismissed'")
    path = Path(result_dir) / "routes.json"
    payload = load_json(path)
    routes = payload.get("routes") or []
    found: dict[str, Any] | None = None
    for route in routes:
        if not isinstance(route, dict):
            continue
        if str(route.get("route_id") or "") != str(route_id):
            continue
        if str(route.get("target") or "") != "human":
            raise ValueError("route is not a Stage-2A human-review route")
        normalized_page_set: set[int] = set()
        for value in reviewed_pages or []:
            try:
                page = int(value)
            except (TypeError, ValueError):
                continue
            if page > 0:
                normalized_page_set.add(page)
        normalized_pages = sorted(normalized_page_set)
        normalized_item_ids = sorted({
            str(value).strip() for value in (reviewed_items or []) if str(value).strip()
        })
        code = str(route.get("code") or "")
        if code == "READING_ORDER_ANOMALY":
            context = stage2a_structural_review_context(result_dir, route_id)
            required_pages = list(context.get("required_pages") or [])
            if not required_pages:
                raise ValueError("Reading-order review evidence is unavailable; keep the route unresolved until its Stage 2A diagnostics can be inspected.")
            missing = [page for page in required_pages if page not in normalized_pages]
            if missing:
                raise ValueError("Review every flagged reading-order page before resolving this route. Missing page(s): " + ", ".join(str(page) for page in missing))
            route["human_reviewed_pages"] = normalized_pages
        elif code == "TABLE_ROW_COLLAPSE":
            # A saved repair is accepted through the dedicated table-repair path.
            # False-positive dismissal must still come from that page after the
            # immutable source page was explicitly checked.
            if normalized == "dismissed" and "table-source" not in normalized_item_ids:
                raise ValueError("Review the collapsed table against the original source page before dismissing it as a false positive.")
            if normalized_item_ids:
                route["human_reviewed_evidence_ids"] = normalized_item_ids
        else:
            context = stage2a_structural_review_context(result_dir, route_id)
            required_ids = list(context.get("required_evidence_ids") or [])
            if not required_ids:
                raise ValueError("Structural-review evidence is unavailable; keep the route unresolved until its Stage 2A diagnostics can be inspected.")
            missing = [item_id for item_id in required_ids if item_id not in normalized_item_ids]
            if missing:
                raise ValueError("Review every structural evidence item before resolving this route. Missing item(s): " + ", ".join(missing))
            route["human_reviewed_evidence_ids"] = normalized_item_ids
        route["status"] = normalized
        route["human_decision"] = normalized
        route["human_decision_note"] = str(note or "").strip() or None
        route["human_decided_at_epoch"] = time.time()
        found = dict(route)
        break
    if found is None:
        raise KeyError(route_id)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return found


def stage2c_freshness(result_dir: Path, verification_rows: list[dict[str, Any]], *, rule_version: str | None = None, artifact_sweep_required: bool = True) -> dict[str, Any]:
    result_dir = Path(result_dir)
    state = load_json(result_dir / "stage2c_backfill.json")
    signature_rows = verification_rows_for_stage2c(verification_rows, artifact_sweep_required=artifact_sweep_required)
    current_verification = verification_signature(signature_rows)
    ledger = load_json(result_dir / "correction_ledger.json")
    ledger_entry_ids = {
        str(entry.get("entry_id") or "")
        for entry in (ledger.get("entries") or [])
        if isinstance(entry, dict) and entry.get("entry_id")
    }
    publication_blockers: list[dict[str, Any]] = []
    for row in signature_rows:
        if str(row.get("status") or "") != "completed":
            continue
        explicit_state = str(row.get("stage2c_entry_state") or "")
        if explicit_state in {"publishing", "error"}:
            publication_blockers.append(row)
            continue
        # Backward-compatible safety for rows completed by 40.11P or older:
        # those rows have no publication state column populated. A completed
        # uncertain/corrupt text-verifier result still requires its deterministic
        # ledger entry before Stage 3 may regard Stage 2C as current.
        if str(row.get("target") or "") != "pi5":
            continue
        if str(row.get("verdict") or "").upper() not in {"LIKELY_CORRUPT", "UNCERTAIN"}:
            continue
        try:
            source = json.loads(row.get("source_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            source = {}
        if str(source.get("type") or "") == "picture":
            continue
        expected_entry_id = f"{str(row.get('generation') or '')}:text:{str(row.get('route_id') or '')}"
        if expected_entry_id not in ledger_entry_ids:
            publication_blockers.append(row)
    ready_status = str(state.get("status") or "") == "completed"
    signature_match = bool(state.get("verification_signature")) and str(state.get("verification_signature")) == current_verification
    rule_match = (not rule_version) or str(state.get("rule_version") or "") == str(rule_version)
    outputs = (result_dir / "correction_ledger.json").is_file() and (result_dir / "chunk_overlays.jsonl").is_file()
    ready = bool(ready_status and signature_match and rule_match and outputs and not publication_blockers)
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
    elif publication_blockers:
        reason = "stage2c_publication_incomplete"
    return {
        "ready": ready,
        "reason": reason,
        "status": str(state.get("status") or "not_built"),
        "verification_signature": current_verification,
        "signature_route_count": len(signature_rows),
        "artifact_sweep_required": bool(artifact_sweep_required),
        "publication_blocker_count": len(publication_blockers),
        "publication_blocker_job_ids": [int(row.get("id") or 0) for row in publication_blockers],
        "recorded_verification_signature": state.get("verification_signature"),
        "rule_version": rule_version,
        "recorded_rule_version": state.get("rule_version"),
        "output_signature": stage2c_output_signature(result_dir) if outputs else None,
        "semantic_output_signature": stage2c_semantic_signature(result_dir) if outputs else None,
        "state": state,
    }


def stage3_freshness(
    result_dir: Path,
    stage2c_info: dict[str, Any],
    *,
    stage3_rule_version: str | None = None,
    retrieval_rule_version: str | None = None,
) -> dict[str, Any]:
    result_dir = Path(result_dir)
    state = load_json(result_dir / "stage3_chunking.json")
    expected = str(stage2c_info.get("output_signature") or "")
    chunks_output = (result_dir / "chunks.jsonl").is_file()
    retrieval_output = (result_dir / "retrieval_index.jsonl").is_file()
    outputs = chunks_output and retrieval_output
    ready_status = str(state.get("status") or "") == "completed"
    signature_match = bool(expected) and bool(state.get("stage2c_signature")) and str(state.get("stage2c_signature")) == expected
    if state.get("stage2c_semantic_signature") and stage2c_info.get("semantic_output_signature"):
        signature_match = state["stage2c_semantic_signature"] == stage2c_info["semantic_output_signature"]
    stage3_rule_match = (not stage3_rule_version) or str(state.get("rule_version") or "") == str(stage3_rule_version)
    quality = load_json(result_dir / "retrieval_quality.json")
    retrieval_rule_match = (not retrieval_rule_version) or str(quality.get("retrieval_rule_version") or "") == str(retrieval_rule_version)
    canonical_ready = bool(stage2c_info.get("ready") and ready_status and signature_match and stage3_rule_match and chunks_output)
    # Retrieval-rule versions describe query-time ranking/scoring behavior. They
    # do not change canonical Stage 3 chunks or the embedded document text, so a
    # ranking-only upgrade must not force text-index regeneration. Keep the
    # comparison as diagnostics only.
    ready = bool(canonical_ready and retrieval_output)
    reason = None
    if not stage2c_info.get("ready"):
        reason = "stage2c_not_current"
    elif not state:
        reason = "stage3_not_built"
    elif not ready_status:
        reason = f"stage3_{str(state.get('status') or 'not_ready')}"
    elif not signature_match:
        reason = "stage3_stale_after_stage2c"
    elif not stage3_rule_match:
        reason = "stage3_rule_version_stale"
    elif not chunks_output:
        reason = "stage3_chunks_missing"
    elif not retrieval_output:
        reason = "retrieval_index_missing"
    return {
        "ready": ready,
        "reason": reason,
        "status": str(state.get("status") or "not_built"),
        "stage2c_signature": expected or None,
        "recorded_stage2c_signature": state.get("stage2c_signature"),
        "stage3_rule_version": stage3_rule_version,
        "recorded_stage3_rule_version": state.get("rule_version"),
        "state": state,
        "canonical_ready": canonical_ready,
        "retrieval_rule_version": retrieval_rule_version,
        "recorded_retrieval_rule_version": quality.get("retrieval_rule_version"),
        "retrieval_rule_match": retrieval_rule_match,
        "ranking_only_version_drift": bool(retrieval_output and not retrieval_rule_match),
        "chunks_available": chunks_output,
        "retrieval_index_available": retrieval_output,
    }
