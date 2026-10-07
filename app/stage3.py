from __future__ import annotations

import asyncio
import copy
import json
import uuid
import logging
import math
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from .archive import select_docling_document
from .config import AppConfig
from .docling_client import DoclingApiError, DoclingClient
from .events import EventBroker
from .postprocess_store import PostprocessStore
from .pipeline_state import stage2c_semantic_signature, stage2a_human_review_summary, stage2c_freshness, stage2c_output_signature, verification_rows_for_stage2c, verification_signature
from .technical_evidence import write_evidence_ledger
from .source_coverage import coverage_pipeline
from .source_recovery import omitted_prose_chunks
from .retrieval import RETRIEVAL_RULE_VERSION, annotate_retrieval_rows, _write_jsonl_atomic as _write_retrieval_jsonl
from .stage2c import STAGE2C_RULE_VERSION, human_review_summary, rebuild_chunk_overlays, verifier_audit_summary
from .book_lifecycle_lock import LifecycleLockGetter
from .table_repair import ensure_collapse_scan, apply_active_table_repairs, active_table_repairs
from .docling_review import apply_repairs_to_document, active_repairs as active_docling_page_repairs, derived_stage3_chunks


STAGE3_RULE_VERSION = "stage3-canonical-integrity-v5"

logger = logging.getLogger(__name__)


class Stage3ChunkBuilder:
    """Build corrected, provenance-rich HybridChunker output via Docling Serve.

    Raw converted ZIPs stay immutable. Accepted Stage 2C text overlays are applied
    only to an in-memory copy of the Docling JSON. That working JSON is uploaded
    back to the configured ``docling_url`` as ``json_docling`` input and chunked
    by Docling Serve's HybridChunker endpoint.
    """

    def __init__(
        self,
        config_getter: Callable[[], AppConfig],
        postprocess_store: PostprocessStore,
        docling_client: DoclingClient,
        events: EventBroker,
        verification_store: Any | None = None,
        lifecycle_lock_getter: LifecycleLockGetter | None = None,
        ledger_lock: asyncio.Lock | None = None,
    ) -> None:
        self._config_getter = config_getter
        self._postprocess_store = postprocess_store
        self._docling_client = docling_client
        self._events = events
        self._verification_store = verification_store
        self._lifecycle_lock_getter = lifecycle_lock_getter
        self._ledger_lock = ledger_lock
        self._tasks: dict[int, asyncio.Task[Any]] = {}
        self._state: dict[int, dict[str, Any]] = {}

    def state_for(self, postprocess_job_id: int) -> dict[str, Any] | None:
        state = self._state.get(int(postprocess_job_id))
        return dict(state) if state else None

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def start(self, postprocess_job_id: int) -> dict[str, Any]:
        postprocess_job_id = int(postprocess_job_id)
        if self._lifecycle_lock_getter is not None:
            async with self._lifecycle_lock_getter(postprocess_job_id):
                return await self._start_locked(postprocess_job_id)
        return await self._start_locked(postprocess_job_id)

    async def _start_locked(self, postprocess_job_id: int) -> dict[str, Any]:
        running = self._tasks.get(postprocess_job_id)
        if running and not running.done():
            return {"accepted": False, "reason": "already_running", **(self.state_for(postprocess_job_id) or {})}

        config = self._config_getter()
        if not config.stage3_enabled:
            raise ValueError("Stage 3 HybridChunker is disabled in config")

        job = await self._postprocess_store.get_job(postprocess_job_id)
        if not job or job.get("status") != "completed" or not job.get("result_dir"):
            raise ValueError("Stage 2A must be completed before Stage 3 chunking")

        result_dir = Path(config.processed_dir) / Path(str(job["result_dir"])).name
        if not (result_dir / "correction_ledger.json").is_file() or not (result_dir / "chunk_overlays.jsonl").is_file():
            raise ValueError("Build Stage 2C before building HybridChunker chunks")
        stage2c_path = result_dir / "stage2c_backfill.json"
        try:
            stage2c_state = json.loads(stage2c_path.read_text(encoding="utf-8")) if stage2c_path.is_file() else {}
        except (OSError, json.JSONDecodeError, TypeError):
            stage2c_state = {}
        if stage2c_state.get("status") != "completed":
            raise ValueError("Stage 2C must complete successfully before building HybridChunker chunks")

        stage2c_info: dict[str, Any] | None = None
        if self._verification_store is not None:
            verification_rows = await self._verification_store.list_book_jobs_raw(postprocess_job_id)
            stage2c_info = await asyncio.to_thread(stage2c_freshness,
                result_dir,
                verification_rows,
                rule_version=STAGE2C_RULE_VERSION,
                artifact_sweep_required=bool(getattr(config, "stage2b_artifact_sweep_required_for_finalize", True)),
            )
            if not stage2c_info.get("ready"):
                reason = str(stage2c_info.get("reason") or "stage2c_not_current")
                raise ValueError(f"Stage 2C is stale or incomplete ({reason}); rebuild Stage 2C first")

        # Compatibility scan for Q-era books: detect newly-known collapsed
        # table rows directly from the immutable Docling ZIP without creating a
        # new Stage-2A/Stage-2B generation.  _start_locked already runs under
        # the per-book lifecycle lock when configured.
        try:
            manifest = json.loads((result_dir / "source_manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            manifest = {}
        converted_name = Path(str(manifest.get("converted_zip") or job.get("output_filename") or "")).name
        converted_zip = Path(config.output_dir) / converted_name if converted_name else None
        if converted_zip is not None and converted_zip.is_file():
            await asyncio.to_thread(ensure_collapse_scan, result_dir, converted_zip)

        structural_review = stage2a_human_review_summary(result_dir)
        if structural_review.get("blocking_review_required", 0):
            raise ValueError(
                f"Stage 2A structural human review is still required for "
                f"{structural_review['blocking_review_required']} item(s) before chunking"
            )

        review = verifier_audit_summary(result_dir, text_require_human=bool(getattr(config, "stage2c_require_human_review", False)))
        if review.get("blocking_review_required", 0):
            raise ValueError(f"Verifier Audit is still required for {review['review_required']} item(s) before chunking; review them or use the testing bypass")

        state = {
            "postprocess_job_id": postprocess_job_id,
            "status": "queued",
            "docling_url": config.docling_url,
            "chunker": "hybrid",
            "tokenizer": config.stage3_chunk_tokenizer,
            "max_tokens": config.stage3_chunk_max_tokens,
            "merge_peers": config.stage3_chunk_merge_peers,
            "use_markdown_tables": config.stage3_chunk_use_markdown_tables,
            "include_raw_text": config.stage3_chunk_include_raw_text,
            "overlays_applied": 0,
            "vision_overlays_preserved": 0,
            "docling_page_repairs_applied": 0,
            "docling_missing_region_chunks": 0,
            "chunk_count": 0,
            "docling_chunk_count": 0,
            "oversized_chunks_seen": 0,
            "oversized_chunks_split": 0,
            "oversized_chunks_remaining": 0,
            "retrieval_searchable_chunks": 0,
            "retrieval_excluded_chunks": 0,
            "retrieval_mean_quality_score": 0.0,
            "rule_version": STAGE3_RULE_VERSION,
            "retrieval_rule_version": RETRIEVAL_RULE_VERSION,
            "task_id": None,
            "error": None,
            "started_at_epoch": None,
            "completed_at_epoch": None,
            "stage2c_signature": (stage2c_info or {}).get("output_signature") or stage2c_output_signature(result_dir),
            "stage2c_semantic_signature": (stage2c_info or {}).get("semantic_output_signature") or stage2c_semantic_signature(result_dir),
            "stage2a_human_review_pending": int(structural_review.get("blocking_review_required") or 0),
        }
        self._state[postprocess_job_id] = state
        task = asyncio.create_task(self._run(postprocess_job_id, job), name=f"stage3-chunks-{postprocess_job_id}")
        self._tasks[postprocess_job_id] = task
        self._events.notify("stage3_chunking_started")
        return {"accepted": True, **state}

    @staticmethod
    def _load_working_document(path):
        with zipfile.ZipFile(path) as archive:
            document, member = select_docling_document(archive)
        return document, member, copy.deepcopy(document)

    async def _run(self, postprocess_job_id: int, job: dict[str, Any]) -> None:
        config = self._config_getter()
        state = self._state[postprocess_job_id]
        state["status"] = "running"
        state["started_at_epoch"] = time.time()
        result_dir = Path(config.processed_dir) / Path(str(job["result_dir"])).name
        status_path = result_dir / "stage3_chunking.json"

        def persist() -> None:
            payload = {
                "schema": "docling-stage3-hybrid-chunking/v1",
                "raw_docling_immutable": True,
                **state,
            }
            tmp = status_path.with_suffix(status_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(status_path)

        try:
            persist()
            manifest = json.loads((result_dir / "source_manifest.json").read_text(encoding="utf-8"))
            converted_name = Path(str(manifest.get("converted_zip") or job.get("output_filename") or "")).name
            converted_zip = Path(config.output_dir) / converted_name
            if not converted_name or not converted_zip.is_file():
                raise ValueError("Converted Docling ZIP is not available")
            if not zipfile.is_zipfile(converted_zip):
                raise ValueError("Converted Docling output is not a ZIP archive")

            document, json_member, working_document = await asyncio.to_thread(self._load_working_document, converted_zip)

            # Rebuild derived overlays from the authoritative ledger on every
            # Stage 3 run. This applies current safety rules to older ledgers
            # and prevents a stale partial automatic transcription from being
            # carried into chunks. Raw Docling remains untouched.
            async def _rebuild_current_overlays() -> None:
                ledger_payload = json.loads((result_dir / "correction_ledger.json").read_text(encoding="utf-8"))
                ledger_entries = list(ledger_payload.get("entries") or [])
                # Empty is meaningful: it must clear an older overlay file.
                await asyncio.to_thread(rebuild_chunk_overlays, result_dir, ledger_entries)

            try:
                if self._ledger_lock is not None:
                    async with self._ledger_lock:
                        await _rebuild_current_overlays()
                else:
                    await _rebuild_current_overlays()
            except (OSError, json.JSONDecodeError, TypeError):
                logger.exception("Could not rebuild Stage 2C overlays before Stage 3 for book %s", postprocess_job_id)
                raise
            overlays = self._read_overlays(result_dir / "chunk_overlays.jsonl")
            text_corrections: dict[int, dict[str, Any]] = {}
            table_corrections: dict[int, list[dict[str, Any]]] = {}
            vision_by_page: dict[int, list[dict[str, Any]]] = {}
            texts = working_document.get("texts") or []
            for overlay in overlays:
                if overlay.get("entry_type") == "text_correction":
                    replacement = str(overlay.get("text") or "").strip()
                    if not replacement:
                        continue
                    source_type = str(overlay.get("source_type") or "text")
                    if source_type == "table_cell":
                        try:
                            table_index = int(overlay.get("table_index")); cell_index = int(overlay.get("cell_index"))
                            tables = working_document.get("tables") or []
                            cells = ((tables[table_index].get("data") or {}).get("table_cells") or [])
                            cell = cells[cell_index]
                        except (TypeError, ValueError, IndexError, AttributeError):
                            continue
                        original_text = str(cell.get("text") or "")
                        cell["text"] = replacement
                        table_corrections.setdefault(table_index, []).append({
                            "entry_id": overlay.get("entry_id"), "source_type": "table_cell",
                            "table_index": table_index, "cell_index": cell_index, "page": overlay.get("page"),
                            "row_start": overlay.get("row_start"), "row_end": overlay.get("row_end"),
                            "col_start": overlay.get("col_start"), "col_end": overlay.get("col_end"),
                            "provenance": overlay.get("provenance"), "human_verified": bool(overlay.get("human_verified")),
                            "original_text": original_text, "corrected_text": replacement,
                        })
                    else:
                        try:
                            source_index = int(overlay.get("source_index"))
                        except (TypeError, ValueError):
                            continue
                        if source_index < 0 or source_index >= len(texts):
                            continue
                        original_text = str((texts[source_index] or {}).get("text") or "")
                        texts[source_index]["text"] = replacement
                        text_corrections[source_index] = {
                            "entry_id": overlay.get("entry_id"), "source_index": source_index,
                            "page": overlay.get("page"), "provenance": overlay.get("provenance"),
                            "human_verified": bool(overlay.get("human_verified")),
                            "original_text": original_text, "corrected_text": replacement,
                        }
                    state["overlays_applied"] += 1
                elif overlay.get("entry_type") == "vision_enrichment":
                    try:
                        page = int(overlay.get("page"))
                    except (TypeError, ValueError):
                        continue
                    vision_by_page.setdefault(page, []).append(overlay)
                    state["vision_overlays_preserved"] += 1

            # Apply human-authoritative whole-table structure repairs only after
            # ordinary Stage-2C text overlays are known.  A structural repair
            # must never silently discard an already human-approved table-cell
            # correction; fail closed unless the corrected text is represented
            # in the repaired matrix.
            for repair in active_table_repairs(result_dir, document):
                table_index = int(repair.get("table_index"))
                repaired_text = "\n".join("\t".join(str(v or "") for v in row) for row in (repair.get("matrix") or []))
                for correction in table_corrections.get(table_index, []):
                    if not bool(correction.get("human_verified")):
                        continue
                    corrected_text = str(correction.get("corrected_text") or "").strip()
                    if corrected_text and corrected_text not in repaired_text:
                        raise ValueError(
                            f"Table structure repair for table {table_index} would discard human-approved "
                            f"table-cell correction {correction.get('entry_id')}; include that corrected text in the repair first"
                        )
            applied_table_repairs = apply_active_table_repairs(result_dir, document, working_document)
            state["table_structure_repairs_applied"] = len(applied_table_repairs)

            # Apply the dedicated Docling page-review overlay last so an approved
            # bbox remains authoritative even when an older structural table
            # repair reconstructed the table matrix first. Whole-table content
            # repairs are checked against existing human table-cell corrections.
            current_page_repairs = active_docling_page_repairs(result_dir, document)
            for page_repair in current_page_repairs:
                if str(page_repair.get("source_collection") or "") != "tables" or not page_repair.get("table_matrix"):
                    continue
                try:
                    table_index = int(page_repair.get("source_index"))
                except (TypeError, ValueError):
                    continue
                repaired_text = "\n".join(
                    "\t".join(str(v or "") for v in row) for row in (page_repair.get("table_matrix") or [])
                )
                for correction in table_corrections.get(table_index, []):
                    if not bool(correction.get("human_verified")):
                        continue
                    corrected_text = str(correction.get("corrected_text") or "").strip()
                    if corrected_text and corrected_text not in repaired_text:
                        raise ValueError(
                            f"Docling page repair {page_repair.get('repair_id')} for table {table_index} would discard "
                            f"human-approved table-cell correction {correction.get('entry_id')}; include that corrected text first"
                        )
            applied_page_repairs = apply_repairs_to_document(result_dir, document, working_document)
            state["docling_page_repairs_applied"] = len(applied_page_repairs)

            working_bytes = await asyncio.to_thread(lambda: json.dumps(working_document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            task_id = await self._docling_client.submit_hybrid_chunks_json(
                filename=f"{Path(json_member).stem}.stage2c.json",
                content=working_bytes,
                max_tokens=config.stage3_chunk_max_tokens,
                tokenizer=config.stage3_chunk_tokenizer,
                merge_peers=config.stage3_chunk_merge_peers,
                use_markdown_tables=config.stage3_chunk_use_markdown_tables,
                include_raw_text=config.stage3_chunk_include_raw_text,
            )
            state["task_id"] = task_id
            persist()

            started = time.monotonic()
            consecutive_errors = 0
            while True:
                if time.monotonic() - started > config.stage3_timeout_minutes * 60:
                    raise TimeoutError(f"Hybrid chunking exceeded {config.stage3_timeout_minutes} minutes")
                try:
                    poll = await self._docling_client.poll(task_id)
                except DoclingApiError:
                    consecutive_errors += 1
                    if consecutive_errors >= config.poll_max_consecutive_errors:
                        raise
                    await asyncio.sleep(min(config.docling_poll_interval_seconds * consecutive_errors, 30))
                    continue
                consecutive_errors = 0
                task_status = str(poll.get("task_status") or "").lower()
                if task_status == "success":
                    break
                if task_status == "failure":
                    detail = poll.get("task_meta") or poll.get("error") or poll.get("detail") or "Docling Serve chunking task failed"
                    raise DoclingApiError(str(detail))
                await asyncio.sleep(config.docling_poll_interval_seconds)

            result = await self._docling_client.result(task_id)
            payload = result.json_data
            if payload is None:
                try:
                    payload = json.loads(result.content.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise DoclingApiError("Docling Serve returned a non-JSON HybridChunker result") from exc
            chunks = self._extract_chunks(payload)
            if not chunks:
                raise DoclingApiError("Docling Serve returned zero HybridChunker chunks")
            state["docling_chunk_count"] = len(chunks)
            missing_region_chunks = derived_stage3_chunks(result_dir, document)
            if missing_region_chunks:
                chunks.extend(missing_region_chunks)
            state["docling_missing_region_chunks"] = len(missing_region_chunks)
            chunks, post_stats = await asyncio.to_thread(self._post_validate_chunks,
                chunks,
                max_tokens=config.stage3_chunk_max_tokens,
                enforce=config.stage3_enforce_max_tokens,
                repeat_table_header=config.stage3_table_split_repeat_header,
            )
            state.update(post_stats)

            narrowed = 0
            narrowed_chunks: list[dict[str, Any]] = []
            for chunk in chunks:
                updated, changed = self._narrow_generic_picture_placeholder_chunk(working_document, chunk)
                narrowed_chunks.append(updated)
                narrowed += int(changed)
            chunks = narrowed_chunks
            state["wide_placeholder_chunks_narrowed"] = narrowed

            picture_chunks = self._missing_picture_child_chunks(
                working_document,
                chunks,
                max_tokens=config.stage3_chunk_max_tokens,
            )
            if picture_chunks:
                chunks.extend(picture_chunks)
            state["picture_child_evidence_chunks"] = len(picture_chunks)

            recovered_prose = await asyncio.to_thread(omitted_prose_chunks, working_document, chunks,
                                                     max_tokens=config.stage3_chunk_max_tokens)
            chunks.extend(recovered_prose)
            state["omitted_prose_recovered"] = len(recovered_prose)

            source_sha = str(manifest.get("converted_zip_sha256") or "")
            output_rows: list[dict[str, Any]] = []
            for idx, raw_chunk in enumerate(chunks):
                row = dict(raw_chunk)
                docling_chunk_index = row.get("chunk_index")
                refs = [str(v) for v in (row.get("doc_items") or [])]
                pages = []
                for value in row.get("page_numbers") or []:
                    try:
                        pages.append(int(value))
                    except (TypeError, ValueError):
                        pass
                corrected_refs: list[dict[str, Any]] = []
                for source_index, correction in text_corrections.items():
                    ref = f"#/texts/{source_index}"
                    if ref in refs:
                        corrected_refs.append(correction)
                # HybridChunker references table chunks at table granularity
                # (for example ``#/tables/7``), while Stage 2C corrections are
                # cell-granular. Preserve every applied cell correction for the
                # referenced table so the final chunk keeps a complete audit
                # trail instead of silently dropping table-cell provenance.
                for table_index, table_entries in table_corrections.items():
                    if f"#/tables/{table_index}" in refs:
                        corrected_refs.extend(table_entries)
                # A table may be split into several chunks; keep provenance on
                # each fragment but never duplicate the same ledger entry inside
                # one chunk.
                deduped_refs: list[dict[str, Any]] = []
                seen_corrections: set[str] = set()
                for correction in corrected_refs:
                    key = str(correction.get("entry_id") or f"{correction.get('source_type')}:{correction.get('table_index')}:{correction.get('cell_index')}:{correction.get('source_index')}")
                    if key in seen_corrections:
                        continue
                    seen_corrections.add(key)
                    deduped_refs.append(correction)
                corrected_refs = deduped_refs
                visual = []
                for page in pages:
                    visual.extend(vision_by_page.get(page, []))
                row["chunk_id"] = f"CHK-{idx + 1:06d}"
                row["docling_chunk_index"] = docling_chunk_index
                row["chunk_index"] = idx
                row["source_zip_sha256"] = source_sha
                row["stage2c_rule_version"] = STAGE2C_RULE_VERSION
                structural_repairs = [
                    repair for repair in applied_table_repairs
                    if f"#/tables/{int(repair.get('table_index'))}" in refs
                ]
                page_repairs = []
                for repair in current_page_repairs:
                    repair_id = str(repair.get("repair_id") or "")
                    source_ref = str(repair.get("source_ref") or "")
                    if (source_ref and source_ref in refs) or (repair_id and f"repair://{repair_id}" in refs):
                        page_repairs.append({
                            "repair_id": repair_id,
                            "page": repair.get("page"),
                            "region_type": repair.get("region_type"),
                            "source_ref": repair.get("source_ref"),
                            "bbox": repair.get("bbox"),
                            "human_verified": True,
                            "approved_at_epoch": repair.get("approved_at_epoch"),
                            "note": repair.get("note"),
                            "provenance": "human_bbox_source_reconstruction",
                        })
                row["stage2c"] = {
                    "text_corrections": corrected_refs,
                    "vision_enrichment": visual,
                    "table_structure_repairs": structural_repairs,
                    "docling_page_repairs": page_repairs,
                }
                row["chunker"] = {
                    "provider": "docling_serve",
                    "server": config.docling_url,
                    "type": "hybrid",
                    "tokenizer": config.stage3_chunk_tokenizer,
                    "max_tokens": config.stage3_chunk_max_tokens,
                    "merge_peers": config.stage3_chunk_merge_peers,
                    "use_markdown_tables": config.stage3_chunk_use_markdown_tables,
                    "enforce_max_tokens": config.stage3_enforce_max_tokens,
                    "table_split_repeat_header": config.stage3_table_split_repeat_header,
                }
                output_rows.append(row)

            output_rows, retrieval_rows, retrieval_quality = await asyncio.to_thread(annotate_retrieval_rows,
                output_rows,
                postprocess_job_id=postprocess_job_id,
                source_filename=str(manifest.get("source_filename") or Path(converted_name).stem),
                result_dir_name=result_dir.name,
                max_tokens=config.stage3_chunk_max_tokens,
            )
            await asyncio.to_thread(self._write_jsonl_atomic, result_dir / "chunks.jsonl", output_rows)
            await asyncio.to_thread(write_evidence_ledger, result_dir, retrieval_rows)
            await asyncio.to_thread(_write_retrieval_jsonl, result_dir / "retrieval_index.jsonl", retrieval_rows)
            await asyncio.to_thread(_write_retrieval_jsonl, result_dir / "table_evidence.jsonl", [row for row in retrieval_rows if row.get("stitched_table")])
            quality_tmp = result_dir / "retrieval_quality.json.tmp"
            quality_tmp.write_text(json.dumps(retrieval_quality, indent=2, ensure_ascii=False), encoding="utf-8")
            quality_tmp.replace(result_dir / "retrieval_quality.json")
            coverage_report = await asyncio.to_thread(coverage_pipeline, document, result_dir)
            state["source_coverage_pending"] = len(coverage_report["recovery_queue"])
            state["retrieval_searchable_chunks"] = int(retrieval_quality.get("searchable_chunks") or 0)
            state["retrieval_excluded_chunks"] = int(retrieval_quality.get("excluded_chunks") or 0)
            state["retrieval_mean_quality_score"] = float(retrieval_quality.get("mean_quality_score") or 0.0)
            state["retrieval_rule_version"] = RETRIEVAL_RULE_VERSION
            state["retrieval_stitched_table_evidence"] = int(retrieval_quality.get("stitched_table_evidence") or 0)
            state["retrieval_table_data_without_header"] = int(retrieval_quality.get("table_data_without_header_chunks") or 0)
            state["chunk_count"] = len(output_rows)
            state["status"] = "completed"
            state["completed_at_epoch"] = time.time()
            persist()
            self._events.notify(
                "stage3_chunking_completed",
                filename=job.get("source_filename") or job.get("output_filename"),
                postprocess_job_id=postprocess_job_id,
            )
        except asyncio.CancelledError:
            state["status"] = "cancelled"
            state["completed_at_epoch"] = time.time()
            persist()
            raise
        except Exception as exc:  # keep background builder failure visible in status artifact
            state["status"] = "failed"
            state["error"] = f"{type(exc).__name__}: {exc}"
            state["completed_at_epoch"] = time.time()
            persist()
            self._events.notify(
                "stage3_chunking_failed",
                filename=job.get("source_filename") or job.get("output_filename"),
                postprocess_job_id=postprocess_job_id,
                error=f"{type(exc).__name__}: {exc}",
            )


    @staticmethod
    def _narrow_generic_picture_placeholder_chunk(
        document: dict[str, Any], chunk: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Remove generic drawing placeholders that artificially widen citations.

        HybridChunker can merge useful text/table evidence with several picture
        placeholders from later pages (for example five ``Engineering drawing``
        lines), producing a tiny chunk whose citation spans six pages.  When the
        picture contribution is *only* a generic placeholder, remove those
        placeholder lines/refs from this canonical text chunk.  Picture evidence
        remains available through visual evidence and picture-child conservation.
        """
        pages = []
        for value in chunk.get("page_numbers") or []:
            try:
                pages.append(int(value))
            except (TypeError, ValueError):
                pass
        refs = [str(v) for v in (chunk.get("doc_items") or [])]
        picture_refs = [ref for ref in refs if ref.startswith("#/pictures/")]
        if len(set(pages)) <= 2 or len(picture_refs) < 2:
            return dict(chunk), False

        generic = {"engineering drawing", "drawing", "figure", "image"}
        def strip_lines(value: str) -> tuple[str, int]:
            kept: list[str] = []
            removed = 0
            for line in str(value or "").splitlines():
                if line.strip().casefold() in generic:
                    removed += 1
                else:
                    kept.append(line)
            return "\n".join(kept).strip(), removed

        new_text, removed_text = strip_lines(str(chunk.get("text") or ""))
        new_raw, removed_raw = strip_lines(str(chunk.get("raw_text") or ""))
        if max(removed_text, removed_raw) < 2 or not (new_text or new_raw):
            return dict(chunk), False

        remaining_refs = [ref for ref in refs if not ref.startswith("#/pictures/")]
        ref_pages: dict[str, int] = {}
        for collection in ("texts", "tables", "pictures", "key_value_items", "form_items", "groups"):
            for item in document.get(collection) or []:
                if not isinstance(item, dict):
                    continue
                ref = str(item.get("self_ref") or "")
                try:
                    page = int(((item.get("prov") or [{}])[0] or {}).get("page_no"))
                except (TypeError, ValueError, IndexError):
                    continue
                if ref and page > 0:
                    ref_pages[ref] = page
        narrowed_pages = sorted({ref_pages[ref] for ref in remaining_refs if ref in ref_pages})
        out = dict(chunk)
        if new_text:
            out["text"] = new_text
        if "raw_text" in out:
            out["raw_text"] = new_raw
        out["doc_items"] = remaining_refs
        if narrowed_pages:
            out["page_numbers"] = narrowed_pages
        meta = dict(out.get("stage3_postprocess") or {})
        meta.update({
            "generic_picture_placeholders_removed": max(removed_text, removed_raw),
            "picture_refs_removed": len(picture_refs),
            "citation_pages_narrowed": bool(narrowed_pages),
        })
        out["stage3_postprocess"] = meta
        return out, True


    @classmethod
    def _missing_picture_child_chunks(
        cls,
        document: dict[str, Any],
        existing_chunks: list[dict[str, Any]],
        *,
        max_tokens: int,
    ) -> list[dict[str, Any]]:
        """Preserve technical text nested under pictures when HybridChunker omits it.

        Docling often represents labels/values inside engineering drawings as text
        children of ``#/pictures/N``. HybridChunker may emit only a generic
        ``Engineering drawing`` placeholder and omit those child refs entirely.
        We add bounded, provenance-rich source-text chunks only for missing child
        refs, so exact technical values such as ``+5V`` or ``2450 Nm`` cannot
        silently disappear merely because their parent is a picture.
        """
        texts = list(document.get("texts") or [])
        pictures = list(document.get("pictures") or [])
        already = {
            str(ref)
            for chunk in existing_chunks
            for ref in (chunk.get("doc_items") or [])
        }
        technical_re = re.compile(
            r"(?:[+\-]?\d+(?:[.,]\d+)?\s*(?:mA|A|mV|V|kV|Hz|kHz|bar|mbar|Pa|kPa|MPa|°?C|rpm|r/min|Nm|N·m|mm|cm|m|kg|t|%))"
            r"|(?:\b[A-Za-z]{1,8}[-_/]?[A-Za-z]*\d+[A-Za-z0-9_./+\-]*\b)",
            re.IGNORECASE,
        )
        rows: list[dict[str, Any]] = []
        for picture_index, picture in enumerate(pictures):
            child_rows: list[tuple[str, str]] = []
            for child in picture.get("children") or []:
                ref = str((child or {}).get("$ref") or "") if isinstance(child, dict) else ""
                match = re.fullmatch(r"#/texts/(\d+)", ref)
                if not match or ref in already:
                    continue
                idx = int(match.group(1))
                if idx < 0 or idx >= len(texts):
                    continue
                value = str((texts[idx] or {}).get("text") or "").strip()
                if value:
                    child_rows.append((ref, value))
            if not child_rows:
                continue
            combined = "\n".join(value for _, value in child_rows)
            if not technical_re.search(combined):
                continue
            try:
                page = int(((picture.get("prov") or [{}])[0] or {}).get("page_no"))
            except (TypeError, ValueError, IndexError):
                page = 0
            picture_ref = str(picture.get("self_ref") or f"#/pictures/{picture_index}")

            batch_refs: list[str] = []
            batch_lines: list[str] = []
            def flush() -> None:
                if not batch_lines:
                    return
                text = "\n".join(batch_lines)
                rows.append({
                    "filename": str(document.get("name") or ""),
                    "chunk_index": None,
                    "text": text,
                    "raw_text": text,
                    "num_tokens": max(1, math.ceil(len(text) / 3.0)),
                    "num_tokens_estimated": True,
                    "headings": [],
                    "captions": [],
                    "doc_items": [picture_ref, *batch_refs],
                    "page_numbers": [page] if page > 0 else [],
                    "metadata": {"picture_child_text_evidence": True, "picture_index": picture_index},
                    "stage3_postprocess": {
                        "action": "preserve_missing_picture_child_text",
                        "source": "immutable_docling_picture_children",
                    },
                })
                batch_refs.clear(); batch_lines.clear()

            for ref, value in child_rows:
                candidate = "\n".join([*batch_lines, value])
                estimate = max(1, math.ceil(len(candidate) / 3.0))
                if batch_lines and estimate > max_tokens:
                    flush()
                batch_refs.append(ref)
                batch_lines.append(value)
            flush()
        return rows

    @staticmethod
    def _estimate_child_tokens(candidate_text: str, parent_text: str, parent_tokens: int) -> int:
        """Conservative tokenizer-free estimate for locally modified chunks.

        Docling supplies an exact token count only for the original chunk. Once
        we compact/split that text we cannot call the configured tokenizer
        locally without adding a heavyweight dependency. Use three independent
        signals and keep the highest: proportional density from Docling's exact
        parent count with an 8% margin, a conservative 2.5 chars/token bound,
        and a lexical/punctuation piece count. This intentionally errs high for
        dense identifiers/tables instead of reporting an over-budget child as
        compliant.
        """
        text = str(candidate_text or "")
        parent = str(parent_text or "")
        try:
            parent_count = max(1, int(parent_tokens))
        except (TypeError, ValueError):
            parent_count = 1
        proportional = math.ceil(len(text) * (parent_count / max(1, len(parent))) * 1.08)
        char_bound = math.ceil(max(1, len(text)) / 2.5)
        piece_bound = len(re.findall(r"[A-Za-z]+|\d+(?:\.\d+)?|[^\w\s]", text, flags=re.UNICODE))
        return max(1, proportional, char_bound, piece_bound)

    @staticmethod
    def _is_markdown_separator(line: str) -> bool:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        return len(cells) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)

    @classmethod
    def _split_oversized_markdown_table(cls, chunk: dict[str, Any], max_tokens: int, repeat_header: bool) -> list[dict[str, Any]]:
        """Split one oversized Markdown table on complete rows only.

        Docling may intentionally keep a coherent table slightly over max_tokens.
        We never cut a row/cell blindly.  Child token counts are conservative
        estimates calibrated from Docling's exact parent count and are explicitly
        marked as estimates in provenance.
        """
        raw_text = str(chunk.get("raw_text") or "")
        full_text = str(chunk.get("text") or raw_text)
        try:
            parent_tokens = int(chunk.get("num_tokens") or 0)
        except (TypeError, ValueError):
            return []
        if parent_tokens <= max_tokens or not raw_text.strip() or not full_text.strip():
            return []

        lines = [line for line in raw_text.splitlines() if line.strip()]
        # Safe mode: a canonical Markdown table must start with header + separator.
        if len(lines) < 4 or not cls._is_markdown_separator(lines[1]):
            return []
        if any("|" not in line for line in lines[2:]):
            return []

        header_lines = lines[:2]
        data_rows = lines[2:]
        if len(data_rows) < 2:
            return []

        if full_text.endswith(raw_text):
            prefix = full_text[: -len(raw_text)]
        else:
            headings = [str(value) for value in (chunk.get("headings") or []) if str(value).strip()]
            prefix = ("\n".join(headings) + "\n") if headings else ""

        # Child counts are conservative estimates derived from Docling's exact
        # parent count plus tokenizer-independent density bounds.

        def make_raw(rows: list[str]) -> str:
            if repeat_header:
                return "\n".join(header_lines + rows) + "\n"
            return "\n".join(rows) + "\n"

        def estimate(rows: list[str]) -> int:
            candidate_text = prefix + make_raw(rows)
            return cls._estimate_child_tokens(candidate_text, full_text, parent_tokens)

        groups: list[list[str]] = []
        current: list[str] = []
        for row in data_rows:
            candidate = current + [row]
            if current and estimate(candidate) > max_tokens:
                groups.append(current)
                current = [row]
            else:
                current = candidate
        if current:
            groups.append(current)
        if len(groups) <= 1:
            return []

        children: list[dict[str, Any]] = []
        for part_index, rows in enumerate(groups, start=1):
            child = copy.deepcopy(chunk)
            child_raw = make_raw(rows)
            child_text = prefix + child_raw
            estimated = estimate(rows)
            child["raw_text"] = child_raw
            child["text"] = child_text
            child["num_tokens"] = estimated
            child["num_tokens_estimated"] = True
            child["num_tokens_source"] = "conservative_multi_signal_estimate_v2"
            child["stage3_postprocess"] = {
                "action": "split_oversized_markdown_table",
                "parent_num_tokens": parent_tokens,
                "configured_max_tokens": max_tokens,
                "part_index": part_index,
                "part_count": len(groups),
                "repeated_table_header": bool(repeat_header),
                "row_boundary_safe": True,
            }
            children.append(child)
        return children

    @classmethod
    def _split_oversized_logical_blocks(cls, chunk: dict[str, Any], max_tokens: int) -> list[dict[str, Any]]:
        """Split an oversized chunk only at explicit logical boundaries.

        This is a conservative fallback for Docling chunks that are slightly
        over the requested budget but are not a canonical Markdown table. It
        never cuts arbitrary token/character offsets. Table-like content is
        split only between complete rows; prose is split only between existing
        paragraphs or complete sentences. If no such boundary exists, the
        chunk is retained and flagged rather than damaged.
        """
        raw_text = str(chunk.get("raw_text") or "")
        full_text = str(chunk.get("text") or raw_text)
        try:
            parent_tokens = int(chunk.get("num_tokens") or 0)
        except (TypeError, ValueError):
            return []
        if parent_tokens <= max_tokens or not raw_text.strip() or not full_text.strip():
            return []

        if full_text.endswith(raw_text):
            full_prefix = full_text[: -len(raw_text)]
        else:
            full_prefix = ""
        headings = [str(v).strip() for v in (chunk.get("headings") or []) if str(v).strip()]
        # Preserve the most local heading in text; all original headings remain
        # in metadata. This avoids spending a large fraction of a 256-token
        # budget repeating long ancestor headings in every child.
        prefix = (headings[-1] + "\n") if headings else full_prefix
        lines = [line.rstrip() for line in raw_text.splitlines() if line.strip()]
        table_like = len(lines) >= 2 and sum(1 for line in lines if "|" in line) / len(lines) >= 0.70
        boundary = "table_row" if table_like else "paragraph_or_sentence"
        units: list[str] = []
        if table_like:
            for line in lines:
                if cls._is_markdown_separator(line) and units:
                    units[-1] = units[-1] + "\n" + line
                else:
                    units.append(line)
        else:
            paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", raw_text) if part.strip()]
            if len(paragraphs) >= 2:
                units = paragraphs
            else:
                # Sentence boundaries only. Do not split unpunctuated OCR runs.
                units = [part.strip() for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", raw_text.strip()) if part.strip()]
        if len(units) < 2:
            return []

        def make_raw(group: list[str]) -> str:
            sep = "\n" if table_like else "\n\n"
            return sep.join(group).strip() + "\n"

        def estimate(group: list[str]) -> int:
            candidate = prefix + make_raw(group)
            return cls._estimate_child_tokens(candidate, full_text, parent_tokens)

        groups: list[list[str]] = []
        current: list[str] = []
        for unit in units:
            candidate = current + [unit]
            if current and estimate(candidate) > max_tokens:
                groups.append(current)
                current = [unit]
            else:
                current = candidate
        if current:
            groups.append(current)
        if len(groups) <= 1:
            return []

        children: list[dict[str, Any]] = []
        for part_index, group in enumerate(groups, start=1):
            child = copy.deepcopy(chunk)
            child_raw = make_raw(group)
            child_text = prefix + child_raw
            child["raw_text"] = child_raw
            child["text"] = child_text
            child["num_tokens"] = estimate(group)
            child["num_tokens_estimated"] = True
            child["num_tokens_source"] = "conservative_multi_signal_estimate_v2"
            child["stage3_postprocess"] = {
                "action": "split_oversized_logical_blocks",
                "parent_num_tokens": parent_tokens,
                "configured_max_tokens": max_tokens,
                "part_index": part_index,
                "part_count": len(groups),
                "boundary": boundary,
                "blind_character_split": False,
                "heading_context_compacted": bool(full_prefix and prefix != full_prefix),
            }
            children.append(child)
        return children

    @classmethod
    def _compact_separator_only_markup(cls, chunk: dict[str, Any], max_tokens: int) -> dict[str, Any] | None:
        """Remove Markdown separator-only retrieval noise while preserving raw_text."""
        raw_text = str(chunk.get("raw_text") or "")
        full_text = str(chunk.get("text") or raw_text)
        try:
            parent_tokens = int(chunk.get("num_tokens") or 0)
        except (TypeError, ValueError):
            return None
        if parent_tokens <= max_tokens or not raw_text.strip():
            return None
        if not re.fullmatch(r"[\s|:\-]+", raw_text):
            return None
        headings = [str(v).strip() for v in (chunk.get("headings") or []) if str(v).strip()]
        candidate = (headings[-1] + "\n") if headings else ""
        estimate = cls._estimate_child_tokens(candidate, full_text, parent_tokens)
        child = copy.deepcopy(chunk)
        child["text"] = candidate
        child["num_tokens"] = estimate
        child["num_tokens_estimated"] = True
        child["num_tokens_source"] = "conservative_multi_signal_estimate_v2"
        child["stage3_postprocess"] = {
            "action": "compact_separator_only_markup",
            "parent_num_tokens": parent_tokens,
            "configured_max_tokens": max_tokens,
            "raw_text_unchanged": True,
            "retrieval_markup_removed": True,
            "full_headings_preserved_in_metadata": True,
        }
        return child

    @classmethod
    def _compact_oversized_table_fragment(cls, chunk: dict[str, Any], max_tokens: int) -> dict[str, Any] | None:
        """Compact a broken Markdown table fragment without inventing row meaning.

        Some Docling HybridChunker outputs contain a padded table header/separator
        followed by an orphaned cell value outside the pipes.  The old fallback
        could retain these fragments above the token budget because the orphaned
        line made the chunk look only 2/3 table-like.  For retrieval we keep
        ``raw_text`` unchanged, preserve every visible value, and replace only
        Markdown padding/separator syntax with a compact neutral representation.
        We deliberately do *not* guess which column an orphan value belongs to.
        """
        raw_text = str(chunk.get("raw_text") or "")
        full_text = str(chunk.get("text") or raw_text)
        try:
            parent_tokens = int(chunk.get("num_tokens") or 0)
        except (TypeError, ValueError):
            return None
        if parent_tokens <= max_tokens or not raw_text.strip() or not full_text.strip():
            return None
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if len(lines) < 2 or "|" not in lines[0] or not cls._is_markdown_separator(lines[1]):
            return None
        headers = [cell.strip() for cell in lines[0].strip().strip("|").split("|") if cell.strip()]
        if len(headers) < 2:
            return None
        compact_lines = ["Table columns: " + " | ".join(headers)]
        for line in lines[2:]:
            if cls._is_markdown_separator(line):
                continue
            if "|" in line:
                cells = [re.sub(r"\s+", " ", cell.strip()) for cell in line.strip().strip("|").split("|")]
                compact_lines.append(" | ".join(cells).strip())
            else:
                compact_lines.append(re.sub(r"\s+", " ", line).strip())
        headings = [str(v).strip() for v in (chunk.get("headings") or []) if str(v).strip()]
        prefix = (headings[-1] + "\n") if headings else ""
        candidate = prefix + "\n".join(line for line in compact_lines if line).strip() + "\n"
        if len(candidate) >= len(full_text):
            return None
        estimate = cls._estimate_child_tokens(candidate, full_text, parent_tokens)
        child = copy.deepcopy(chunk)
        child["text"] = candidate
        child["num_tokens"] = estimate
        child["num_tokens_estimated"] = True
        child["num_tokens_source"] = "conservative_multi_signal_estimate_v2"
        child["stage3_postprocess"] = {
            "action": "compact_oversized_table_fragment",
            "parent_num_tokens": parent_tokens,
            "configured_max_tokens": max_tokens,
            "raw_text_unchanged": True,
            "column_assignment_inferred": False,
            "full_headings_preserved_in_metadata": True,
        }
        return child

    @classmethod
    def _compact_oversized_markdown_text(cls, chunk: dict[str, Any], max_tokens: int) -> dict[str, Any] | None:
        """Compact Markdown presentation without changing table values.

        Docling tables can spend many tokens on alignment spaces and separator
        dashes. For retrieval text those characters carry no engineering data.
        Keep ``raw_text`` untouched for audit/provenance, but normalize cell
        padding and omit separator rows in ``text``. Full heading ancestry stays
        in metadata while only the deepest heading is repeated in text.
        """
        raw_text = str(chunk.get("raw_text") or "")
        full_text = str(chunk.get("text") or raw_text)
        try:
            parent_tokens = int(chunk.get("num_tokens") or 0)
        except (TypeError, ValueError):
            return None
        if parent_tokens <= max_tokens or not raw_text.strip() or not full_text.strip():
            return None
        lines = [line for line in raw_text.splitlines() if line.strip()]
        if len(lines) < 1 or sum(1 for line in lines if "|" in line) / len(lines) < 0.70:
            return None
        compact_lines: list[str] = []
        for line in lines:
            if "|" not in line:
                compact_lines.append(line.strip())
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
                continue
            compact_lines.append("| " + " | ".join(cells) + " |")
        if not compact_lines:
            return None
        headings = [str(v).strip() for v in (chunk.get("headings") or []) if str(v).strip()]
        prefix = (headings[-1] + "\n") if headings else ""
        candidate = prefix + "\n".join(compact_lines).strip() + "\n"
        if len(candidate) >= len(full_text):
            return None
        estimate = cls._estimate_child_tokens(candidate, full_text, parent_tokens)
        child = copy.deepcopy(chunk)
        child["text"] = candidate
        child["num_tokens"] = estimate
        child["num_tokens_estimated"] = True
        child["num_tokens_source"] = "conservative_multi_signal_estimate_v2"
        child["stage3_postprocess"] = {
            "action": "compact_oversized_markdown_text",
            "parent_num_tokens": parent_tokens,
            "configured_max_tokens": max_tokens,
            "raw_text_unchanged": True,
            "separator_rows_removed_from_retrieval_text": True,
            "cell_padding_compacted": True,
            "full_headings_preserved_in_metadata": True,
        }
        return child

    @classmethod
    def _compact_oversized_heading_context(cls, chunk: dict[str, Any], max_tokens: int) -> dict[str, Any] | None:
        """Remove repeated ancestor-heading text when raw content already fits.

        Docling's HybridChunker may prepend a long heading ancestry to every
        child. For retrieval we keep the full heading list in metadata but only
        repeat the deepest useful heading in ``text`` when that is enough to
        bring a chunk under the configured token target. Raw content is never
        removed or split by this fallback.
        """
        raw_text = str(chunk.get("raw_text") or "")
        full_text = str(chunk.get("text") or raw_text)
        try:
            parent_tokens = int(chunk.get("num_tokens") or 0)
        except (TypeError, ValueError):
            return None
        if parent_tokens <= max_tokens or not raw_text.strip() or not full_text.endswith(raw_text):
            return None
        headings = [str(v).strip() for v in (chunk.get("headings") or []) if str(v).strip()]
        candidates: list[tuple[str, str]] = []
        if headings:
            candidates.append(("deepest_heading", headings[-1] + "\n" + raw_text))
        candidates.append(("raw_only", raw_text))
        for mode, candidate in candidates:
            estimate = cls._estimate_child_tokens(candidate, full_text, parent_tokens)
            if estimate > max_tokens:
                continue
            child = copy.deepcopy(chunk)
            child["text"] = candidate
            child["num_tokens"] = estimate
            child["num_tokens_estimated"] = True
            child["num_tokens_source"] = "conservative_multi_signal_estimate_v2"
            child["stage3_postprocess"] = {
                "action": "compact_oversized_heading_context",
                "parent_num_tokens": parent_tokens,
                "configured_max_tokens": max_tokens,
                "heading_mode": mode,
                "raw_text_unchanged": True,
                "full_headings_preserved_in_metadata": True,
            }
            return child
        return None

    @classmethod
    def _resolve_oversized_chunk(
        cls,
        chunk: dict[str, Any],
        *,
        max_tokens: int,
        repeat_table_header: bool,
        depth: int = 0,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Recursively apply only lossless/structural safe reductions.

        The important difference from the .27 post-validator is that children
        produced by a safe split are validated again. This fixes the real-book
        case where a child containing only a padded Markdown header was still
        estimated at 258-265 tokens and was never compacted a second time.
        """
        current = dict(chunk)
        try:
            tokens = int(current.get("num_tokens") or 0)
        except (TypeError, ValueError):
            tokens = 0
        if tokens <= max_tokens:
            return [current], False
        if depth >= 6:
            current["stage3_postprocess"] = {
                "action": "oversized_chunk_retained",
                "configured_max_tokens": max_tokens,
                "parent_num_tokens": tokens,
                "reason": "SAFE_RESOLUTION_DEPTH_LIMIT",
            }
            return [current], False

        changed = False
        # Presentation-only compaction is safest and should happen before any
        # structural split. raw_text remains untouched in both compactors.
        for compactor in (cls._compact_separator_only_markup, cls._compact_oversized_markdown_text, cls._compact_oversized_table_fragment):
            candidate = compactor(current, max_tokens)
            if candidate is None:
                continue
            try:
                candidate_tokens = int(candidate.get("num_tokens") or 0)
            except (TypeError, ValueError):
                candidate_tokens = tokens
            if candidate_tokens >= tokens and len(str(candidate.get("text") or "")) >= len(str(current.get("text") or "")):
                continue
            current = candidate
            tokens = candidate_tokens
            changed = True
            if tokens <= max_tokens:
                return [current], True

        table_children = cls._split_oversized_markdown_table(current, max_tokens, repeat_table_header)
        if table_children:
            resolved: list[dict[str, Any]] = []
            for child in table_children:
                rows, _ = cls._resolve_oversized_chunk(
                    child, max_tokens=max_tokens, repeat_table_header=repeat_table_header, depth=depth + 1
                )
                resolved.extend(rows)
            return resolved, True

        logical_children = cls._split_oversized_logical_blocks(current, max_tokens)
        if logical_children:
            resolved = []
            for child in logical_children:
                rows, _ = cls._resolve_oversized_chunk(
                    child, max_tokens=max_tokens, repeat_table_header=repeat_table_header, depth=depth + 1
                )
                resolved.extend(rows)
            return resolved, True

        compacted = cls._compact_oversized_heading_context(current, max_tokens)
        if compacted is not None:
            return [compacted], True

        current["stage3_postprocess"] = {
            "action": "oversized_chunk_retained",
            "configured_max_tokens": max_tokens,
            "parent_num_tokens": tokens,
            "reason": "NO_SAFE_LOGICAL_BOUNDARY_SPLIT",
        }
        return [current], changed

    @classmethod
    def _post_validate_chunks(
        cls,
        chunks: list[dict[str, Any]],
        *,
        max_tokens: int,
        enforce: bool,
        repeat_table_header: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        output: list[dict[str, Any]] = []
        seen = 0
        split = 0
        remaining = 0
        for original in chunks:
            chunk = dict(original)
            try:
                tokens = int(chunk.get("num_tokens") or 0)
            except (TypeError, ValueError):
                tokens = 0
            if not enforce or tokens <= max_tokens:
                output.append(chunk)
                continue
            seen += 1
            resolved, changed = cls._resolve_oversized_chunk(
                chunk, max_tokens=max_tokens, repeat_table_header=repeat_table_header
            )
            output.extend(resolved)
            if changed:
                split += 1
            remaining += sum(1 for row in resolved if int(row.get("num_tokens") or 0) > max_tokens)

        return output, {
            "oversized_chunks_seen": seen,
            "oversized_chunks_split": split,
            "oversized_chunks_remaining": remaining,
        }

    @classmethod
    def refresh_existing_outputs(
        cls,
        result_dir: Path,
        *,
        max_tokens: int = 256,
        repeat_table_header: bool = True,
        additional_chunks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Re-run only the deterministic Stage 3 post-validator and retrieval index.

        This is intentionally cheap: no Docling, Pi5, OnePlus, or Groq call is
        made. It lets books built by an older release benefit from safer table
        compaction and retrieval filtering after an upgrade.
        """
        result_dir = Path(result_dir)
        chunks_path = result_dir / "chunks.jsonl"
        if not chunks_path.is_file():
            raise FileNotFoundError("Stage 3 chunks.jsonl is missing")
        chunks: list[dict[str, Any]] = []
        for line in chunks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                chunks.append(value)
        chunks.extend(additional_chunks or [])
        optimized, post_stats = cls._post_validate_chunks(
            chunks,
            max_tokens=max_tokens,
            enforce=True,
            repeat_table_header=repeat_table_header,
        )
        for idx, row in enumerate(optimized):
            row["chunk_id"] = f"CHK-{idx + 1:06d}"
            row["chunk_index"] = idx
        manifest: dict[str, Any] = {}
        try:
            manifest = json.loads((result_dir / "source_manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        job_id = None
        status_path = result_dir / "stage3_chunking.json"
        if status_path.is_file():
            try:
                persisted_status = json.loads(status_path.read_text(encoding="utf-8"))
                job_id = int(persisted_status.get("postprocess_job_id") or 0) or None
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                job_id = None
        if job_id is None:
            job_match = re.search(r"__job(\d+)", result_dir.name)
            job_id = int(job_match.group(1)) if job_match else None
        optimized, retrieval_rows, retrieval_quality = annotate_retrieval_rows(
            optimized,
            postprocess_job_id=job_id,
            source_filename=str(manifest.get("source_filename") or result_dir.name),
            result_dir_name=result_dir.name,
            max_tokens=max_tokens,
        )
        cls._write_jsonl_atomic(chunks_path, optimized)
        write_evidence_ledger(result_dir, retrieval_rows)
        _write_retrieval_jsonl(result_dir / "retrieval_index.jsonl", retrieval_rows)
        _write_retrieval_jsonl(result_dir / "table_evidence.jsonl", [row for row in retrieval_rows if row.get("stitched_table")])
        tmp = result_dir / "retrieval_quality.json.tmp"
        tmp.write_text(json.dumps(retrieval_quality, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(result_dir / "retrieval_quality.json")

        status_path = result_dir / "stage3_chunking.json"
        if status_path.is_file():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                status = {}
            status.update(post_stats)
            status["chunk_count"] = len(optimized)
            status["retrieval_searchable_chunks"] = retrieval_quality.get("searchable_chunks", 0)
            status["retrieval_excluded_chunks"] = retrieval_quality.get("excluded_chunks", 0)
            status["retrieval_mean_quality_score"] = retrieval_quality.get("mean_quality_score", 0.0)
            status["retrieval_rule_version"] = RETRIEVAL_RULE_VERSION
            status["retrieval_stitched_table_evidence"] = retrieval_quality.get("stitched_table_evidence", 0)
            status["retrieval_table_data_without_header"] = retrieval_quality.get("table_data_without_header_chunks", 0)
            status["deterministic_refresh_at_epoch"] = time.time()
            status["raw_docling_immutable"] = True
            status_tmp = status_path.with_suffix(status_path.suffix + ".tmp")
            status_tmp.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
            status_tmp.replace(status_path)
        return {
            **post_stats,
            **retrieval_quality,
            "chunk_count": len(optimized),
        }

    @staticmethod
    def _read_overlays(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not path.is_file():
            return rows
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows

    @staticmethod
    def _extract_chunks(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            chunks = payload.get("chunks")
            if isinstance(chunks, list):
                return [row for row in chunks if isinstance(row, dict)]
            document = payload.get("document")
            if isinstance(document, dict) and isinstance(document.get("chunks"), list):
                return [row for row in document["chunks"] if isinstance(row, dict)]
            result = payload.get("result")
            if isinstance(result, dict) and isinstance(result.get("chunks"), list):
                return [row for row in result["chunks"] if isinstance(row, dict)]
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []

    @staticmethod
    def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
        tmp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp.replace(path)
