from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .pipeline_state import load_json, stage2a_human_review_summary, stage2a_structural_review_context

AUDIT_FILE = "structural_anomaly_reviews.json"


def _pages(value: Any) -> set[int]:
    pages = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"page", "page_no"}:
                try:
                    if int(item) > 0:
                        pages.add(int(item))
                except (TypeError, ValueError):
                    pass
            elif key in {"pages", "page_numbers"} and isinstance(item, list):
                for page in item:
                    try:
                        if int(page) > 0:
                            pages.add(int(page))
                    except (TypeError, ValueError):
                        pass
            else:
                pages.update(_pages(item))
    elif isinstance(value, list):
        for item in value:
            pages.update(_pages(item))
    return pages


def structural_entries(result_dir: Path) -> list[dict[str, Any]]:
    """Expose durable human routes, including resolved routes, for advisory audit."""
    result_dir = Path(result_dir)
    audits = load_json(result_dir / AUDIT_FILE).get("reviews") or {}
    repairs = load_json(result_dir / "table_structure_repairs.json").get("repairs") or {}
    page_repairs = load_json(result_dir / "docling_page_repairs.json")
    entries = []
    diagnostics = load_json(result_dir / "diagnostics.json")
    for route in stage2a_human_review_summary(result_dir)["routes"]:
        if route.get("status") == "superseded" or not route.get("route_id") or not route.get("code"):
            continue
        context = stage2a_structural_review_context(result_dir, str(route["route_id"]), route=route, diagnostics=diagnostics)
        # A per-table route must not inherit findings belonging to other tables.
        table_index = (route.get("source") or {}).get("table_index")
        if table_index is not None:
            context["items"] = [item for item in context["items"] if str(item.get("table_index")) == str(table_index)]
            context["signal"] = {key: value for key, value in context["signal"].items() if key not in {"items", "samples"}}
            context.pop("evidence_items", None)
            context.pop("required_evidence_ids", None)
            context.pop("required_pages", None)
        evidence = {"route": route, "diagnostics": context["items"], "signal": {key: value for key, value in context["signal"].items() if key not in {"items", "samples"}},
                    "table_repairs": repairs if table_index is None else {str(table_index): repairs.get(str(table_index))},
                    "docling_page_repairs": page_repairs}
        pages = sorted(_pages({"route": route, "diagnostics": context["items"]}))
        entry_id = "structural:" + str(route["route_id"])
        audit = audits.get(entry_id) if isinstance(audits, dict) else None
        entries.append({"entry_id": entry_id, "entry_type": "structural_anomaly", "route_id": str(route["route_id"]),
                        "structural_code": str(route["code"]), "status": str(route.get("status") or "pending"),
                        "page": pages[0] if pages else None, "pages": pages, "source_type": "table_structure" if str(route["code"]).startswith("TABLE_") else "document_structure",
                        "human_verified": str(route.get("status")) in {"accepted", "dismissed", "resolved"},
                        "human_review": {"action": route.get("human_decision"), "saved_at_epoch": route.get("human_decided_at_epoch"), "note": route.get("human_decision_note")},
                        "_structural_evidence": evidence, "anomaly_review": (audit or {}).get("anomaly_review"),
                        "anomaly_review_history": (audit or {}).get("anomaly_review_history") or []})
    return entries


def structural_signature(entry: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(entry["_structural_evidence"], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def save_structural_audit(result_dir: Path, entry: dict[str, Any], result: dict[str, Any]) -> None:
    path = Path(result_dir) / AUDIT_FILE
    payload = load_json(path)
    reviews = payload.setdefault("reviews", {})
    old = reviews.get(entry["entry_id"]) or {}
    history = list(old.get("anomaly_review_history") or [])
    if old.get("anomaly_review"):
        history.append(old["anomaly_review"])
    reviews[entry["entry_id"]] = {"anomaly_review": result, "anomaly_review_history": history[-10:]}
    payload.update(schema="docling-structural-anomaly-reviews/v1", updated_at_epoch=time.time())
    tmp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


async def run_structural_audit(worker, *, worker_id: str, postprocess_job_id: int, entry_id: str, manual_requested: bool) -> dict[str, Any]:
    """Audit every relevant PDF page; proposals never mutate human-owned files."""
    import asyncio
    from .stage2b import _render_source_page, _json_from_model_response, _response_finish_reason
    from .table_repair import table_to_matrix, matrix_to_tsv
    from .verifier_clients import OpenAICompatibleVerifier, ReviewWorkerBusyError

    registry = worker._worker_registry
    selected = registry.get_colab(worker_id) if registry else None
    if not selected or not selected.get("enabled") or selected.get("paused"):
        raise ValueError("Selected Colab anomaly worker is disabled or stopped")
    api_key = registry.read_api_key(worker_id)
    endpoint = str(selected.get("url") or "").rstrip("/")
    if not api_key or not endpoint:
        raise ValueError("Selected Colab anomaly worker is not configured")
    provider = worker._colab_provider_key(worker_id)
    owner = f"{provider}:structural:{postprocess_job_id}:{entry_id}"
    if not await worker._reserve_provider(provider, owner):
        raise ReviewWorkerBusyError("Selected Colab anomaly worker is busy")
    try:
        job = await worker._postprocess_store.get_job(postprocess_job_id)
        if not job or not job.get("result_dir"):
            raise ValueError("Post-process book is unavailable")
        config = worker._config_getter()
        result_dir = Path(config.processed_dir) / Path(str(job["result_dir"])).name
        entries = await asyncio.to_thread(structural_entries, result_dir)
        entry = next((e for e in entries if e["entry_id"] == entry_id), None)
        if entry is None:
            raise ValueError("Structural anomaly route is no longer current")
        signature = structural_signature(entry)
        conversion = await worker._postprocess_store.get_conversion_job(int(job.get("conversion_job_id") or 0))
        filename = str((conversion or {}).get("filename") or job.get("source_filename") or "")
        evidence = entry["_structural_evidence"]
        tables = []
        if entry["structural_code"].startswith("TABLE_"):
            output = str(job.get("output_filename") or (conversion or {}).get("output_filename") or "")
            doc = await worker._document_for(Path(config.output_dir) / Path(output).name)
            indexes = set()
            def table_indexes(value):
                if isinstance(value, dict):
                    if value.get("table_index") is not None:
                        indexes.add(int(value["table_index"]))
                    for child in value.values():
                        table_indexes(child)
                elif isinstance(value, list):
                    for child in value:
                        table_indexes(child)
            table_indexes({"route": evidence["route"], "diagnostics": evidence["diagnostics"]})
            for index in sorted(indexes):
                if index < 0 or index >= len(doc.get("tables") or []):
                    raise ValueError("Structural anomaly table is missing from immutable source")
                table = doc["tables"][index]
                pages = sorted(_pages(table.get("prov") or []))
                tables.append({"table_index": index, "pages": pages, "raw_matrix": table_to_matrix(table),
                               "approved_repair": evidence["table_repairs"].get(str(index))})
            pages = sorted(set(entry["pages"]) | {p for t in tables for p in t["pages"]})
        else:
            pages = entry["pages"]
        client = OpenAICompatibleVerifier(endpoint, timeout_seconds=int(config.colab_timeout_seconds), api_key=api_key)
        worker._ensure_provider_state(provider)
        started = time.monotonic()
        audits = []
        system = (
            "You independently audit extraction anomalies in a technical manual. Source PDF pixels are ground truth. "
            "Prior diagnostics and approved human repairs are context. Never change a human decision. "
            "Check table row/column associations, merged cells, reading order, headings, geometry or references as applicable. "
            "Return one JSON object with verdict (CONFIRM_CURRENT, ANOMALY_CONFIRMED, REPAIR_SUGGESTED, NEEDS_HUMAN), "
            "confidence, reason, suggested_action, table_proposals. table_proposals is a list of objects with table_index, "
            "matrix (rectangular string rows), header_rows. Propose a matrix only for a table visibly readable on this page. "
            "Do not invent cells or associations. For a partial/multipage table or missing visual evidence, request human review. "
            "All proposals remain advisory and require explicit human source review before saving. "
        )
        for page in pages or [None]:
            diagnostics = [item for item in evidence["diagnostics"] if not _pages(item) or page in _pages(item)]
            page_tables = [t for t in tables if not t["pages"] or page in t["pages"]]
            context = {"code": entry["structural_code"], "route": evidence["route"], "page": page,
                       "diagnostics": diagnostics, "tables": page_tables, "human_page_repairs": evidence["docling_page_repairs"],
                       "evidence_scope": "source_page" if page else "diagnostics_only"}
            serialized = json.dumps(context, ensure_ascii=False)
            if len(serialized) > 48000:
                raise ValueError("Structural evidence exceeds this worker's safe prompt window; review this route manually")
            prompt = system + "\nAUDIT CONTEXT:\n" + serialized
            async with worker._review_inference_window(postprocess_job_id), worker._device_locks[provider]:
                if page:
                    rendered = await asyncio.to_thread(_render_source_page, config, filename, page)
                    if not rendered:
                        raise ValueError(f"Original source page {page} is unavailable for structural anomaly review")
                    response = await client.inspect_image(rendered[0], prompt, mime_type=rendered[1], model=selected.get("model") or "koboldcpp", max_tokens=4096)
                else:
                    response = await client.chat_text(system, serialized, model=selected.get("model") or "koboldcpp", max_tokens=4096)
            if _response_finish_reason(response) == "length":
                raise ValueError("Structural anomaly response was truncated; no partial repair was published")
            parsed = _json_from_model_response(response)
            verdict = str(parsed.get("verdict") or "NEEDS_HUMAN").upper()
            if verdict not in {"CONFIRM_CURRENT", "ANOMALY_CONFIRMED", "REPAIR_SUGGESTED", "NEEDS_HUMAN"} or not page:
                verdict = "NEEDS_HUMAN"
            proposals = []
            candidates = parsed.get("table_proposals") or []
            if not isinstance(candidates, list):
                raise ValueError("Structural table proposals must be a list")
            for proposal in candidates:
                if not isinstance(proposal, dict):
                    raise ValueError("Structural table proposal is malformed")
                matrix = proposal.get("matrix")
                index = proposal.get("table_index")
                if not page or not any(t["table_index"] == index and t["pages"] == [page] for t in page_tables):
                    verdict = "NEEDS_HUMAN"
                    continue
                if not isinstance(matrix, list) or not matrix or len(matrix) > 500 or not isinstance(matrix[0], list) or not 0 < len(matrix[0]) <= 64:
                    raise ValueError("Structural table proposal has invalid dimensions")
                width = len(matrix[0])
                if any(not isinstance(row, list) or len(row) != width or any(not isinstance(cell, str) for cell in row) for row in matrix):
                    raise ValueError("Structural table proposal must contain rectangular string rows")
                headers = int(proposal.get("header_rows") or 0)
                if not 0 <= headers <= len(matrix):
                    raise ValueError("Structural table proposal has invalid header rows")
                proposals.append({"page": page, "table_index": index, "matrix": matrix, "header_rows": headers, "tsv": matrix_to_tsv(matrix)})
            audits.append({"page": page, "verdict": verdict, "confidence": parsed.get("confidence"),
                           "reason": str(parsed.get("reason") or ""), "suggested_action": str(parsed.get("suggested_action") or ""),
                           "table_proposals": proposals, "evidence_scope": context["evidence_scope"]})
        verdicts = {a["verdict"] for a in audits}
        verdict = next(v for v in ("NEEDS_HUMAN", "REPAIR_SUGGESTED", "ANOMALY_CONFIRMED", "CONFIRM_CURRENT") if v in verdicts)
        result = {"schema": "marine-structural-anomaly-review/v1", "review_type": "structural", "worker_id": worker_id,
                  "worker_name": selected.get("name") or worker_id, "model": selected.get("model"), "provider": provider,
                  "manual_requested": manual_requested, "evidence_signature": signature, "verdict": verdict,
                  "reason": "\n".join(a["reason"] for a in audits), "page_audits": audits,
                  "table_proposals": [p for a in audits for p in a["table_proposals"]],
                  "anomaly_types": [entry["structural_code"]], "anomaly_types_confirmed": [] if verdict == "CONFIRM_CURRENT" else [entry["structural_code"]],
                  "source": {"required_pages": pages, "audited_pages": [a["page"] for a in audits if a["page"]], "diagnostics_only": not bool(pages)},
                  "created_at_epoch": time.time(), "processing_seconds": round(time.monotonic() - started, 3), "human_authority_preserved": True}
        async with worker._stage2c_ledger_lock:
            current = next((e for e in await asyncio.to_thread(structural_entries, result_dir) if e["entry_id"] == entry_id), None)
            if current is None or structural_signature(current) != signature:
                result.update(stored=False, discarded=True, discard_reason="Structural evidence or human repair changed during Colab audit")
                return result
            result["stored"] = True
            await asyncio.to_thread(save_structural_audit, result_dir, current, result)
        worker._events.notify("structural_anomaly_review_completed")
        return result
    finally:
        await worker._release_provider(provider, owner)
