from __future__ import annotations

import asyncio
import copy
import json
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
from .pipeline_state import stage2c_output_signature, verification_signature
from .retrieval import RETRIEVAL_RULE_VERSION, annotate_retrieval_rows, _write_jsonl_atomic as _write_retrieval_jsonl
from .stage2c import STAGE2C_RULE_VERSION, human_review_summary, rebuild_chunk_overlays


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
    ) -> None:
        self._config_getter = config_getter
        self._postprocess_store = postprocess_store
        self._docling_client = docling_client
        self._events = events
        self._verification_store = verification_store
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
        if self._verification_store is not None:
            verification_rows = await self._verification_store.list_book_jobs_raw(postprocess_job_id)
            current_verification_signature = verification_signature(verification_rows)
            if not stage2c_state.get("verification_signature") or stage2c_state.get("verification_signature") != current_verification_signature:
                raise ValueError("Stage 2C is stale because Stage 2B verification changed; rebuild Stage 2C first")
        review = human_review_summary(result_dir, require_human=bool(getattr(config, "stage2c_require_human_review", True)))
        if review.get("blocking_review_required", review.get("review_required", 0)):
            raise ValueError(
                f"Human review is still required for {review['review_required']} corrupted/uncertain text item(s) before chunking"
            )

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
            "chunk_count": 0,
            "docling_chunk_count": 0,
            "oversized_chunks_seen": 0,
            "oversized_chunks_split": 0,
            "oversized_chunks_remaining": 0,
            "retrieval_searchable_chunks": 0,
            "retrieval_excluded_chunks": 0,
            "retrieval_mean_quality_score": 0.0,
            "retrieval_rule_version": RETRIEVAL_RULE_VERSION,
            "task_id": None,
            "error": None,
            "started_at_epoch": None,
            "completed_at_epoch": None,
            "stage2c_signature": stage2c_output_signature(result_dir),
        }
        self._state[postprocess_job_id] = state
        task = asyncio.create_task(self._run(postprocess_job_id, job), name=f"stage3-chunks-{postprocess_job_id}")
        self._tasks[postprocess_job_id] = task
        self._events.notify("stage3_chunking_started")
        return {"accepted": True, **state}

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

            with zipfile.ZipFile(converted_zip) as archive:
                document, json_member = select_docling_document(archive)
            working_document = copy.deepcopy(document)

            # Rebuild derived overlays from the authoritative ledger on every
            # Stage 3 run. This applies current safety rules to older ledgers
            # and prevents a stale partial automatic transcription from being
            # carried into chunks. Raw Docling remains untouched.
            try:
                ledger_payload = json.loads((result_dir / "correction_ledger.json").read_text(encoding="utf-8"))
                ledger_entries = list(ledger_payload.get("entries") or [])
                if ledger_entries:
                    await asyncio.to_thread(rebuild_chunk_overlays, result_dir, ledger_entries)
            except (OSError, json.JSONDecodeError, TypeError):
                pass
            overlays = self._read_overlays(result_dir / "chunk_overlays.jsonl")
            corrections: dict[int, dict[str, Any]] = {}
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
                        corrections[f"table:{table_index}:{cell_index}"] = {
                            "entry_id": overlay.get("entry_id"), "source_type": "table_cell",
                            "table_index": table_index, "cell_index": cell_index, "page": overlay.get("page"),
                            "provenance": overlay.get("provenance"), "human_verified": bool(overlay.get("human_verified")),
                            "original_text": original_text, "corrected_text": replacement,
                        }
                    else:
                        try:
                            source_index = int(overlay.get("source_index"))
                        except (TypeError, ValueError):
                            continue
                        if source_index < 0 or source_index >= len(texts):
                            continue
                        original_text = str((texts[source_index] or {}).get("text") or "")
                        texts[source_index]["text"] = replacement
                        corrections[source_index] = {
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

            working_bytes = json.dumps(working_document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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
            chunks, post_stats = self._post_validate_chunks(
                chunks,
                max_tokens=config.stage3_chunk_max_tokens,
                enforce=config.stage3_enforce_max_tokens,
                repeat_table_header=config.stage3_table_split_repeat_header,
            )
            state.update(post_stats)

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
                corrected_refs = []
                for source_index, correction in corrections.items():
                    ref = f"#/texts/{source_index}"
                    if ref in refs:
                        corrected_refs.append(correction)
                visual = []
                for page in pages:
                    visual.extend(vision_by_page.get(page, []))
                row["chunk_id"] = f"CHK-{idx + 1:06d}"
                row["docling_chunk_index"] = docling_chunk_index
                row["chunk_index"] = idx
                row["source_zip_sha256"] = source_sha
                row["stage2c_rule_version"] = STAGE2C_RULE_VERSION
                row["stage2c"] = {
                    "text_corrections": corrected_refs,
                    "vision_enrichment": visual,
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

            output_rows, retrieval_rows, retrieval_quality = annotate_retrieval_rows(
                output_rows,
                postprocess_job_id=postprocess_job_id,
                source_filename=str(manifest.get("source_filename") or Path(converted_name).stem),
                result_dir_name=result_dir.name,
                max_tokens=config.stage3_chunk_max_tokens,
            )
            self._write_jsonl_atomic(result_dir / "chunks.jsonl", output_rows)
            _write_retrieval_jsonl(result_dir / "retrieval_index.jsonl", retrieval_rows)
            _write_retrieval_jsonl(result_dir / "table_evidence.jsonl", [row for row in retrieval_rows if row.get("stitched_table")])
            quality_tmp = result_dir / "retrieval_quality.json.tmp"
            quality_tmp.write_text(json.dumps(retrieval_quality, indent=2, ensure_ascii=False), encoding="utf-8")
            quality_tmp.replace(result_dir / "retrieval_quality.json")
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
            self._events.notify("stage3_chunking_completed")
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
            self._events.notify("stage3_chunking_failed")


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

        # Derive token density from Docling's exact tokenizer count for the parent.
        # Add 8% safety to account for repeated table headers and token-boundary drift.
        tokens_per_char = parent_tokens / max(1, len(full_text))

        def make_raw(rows: list[str]) -> str:
            if repeat_header:
                return "\n".join(header_lines + rows) + "\n"
            return "\n".join(rows) + "\n"

        def estimate(rows: list[str]) -> int:
            candidate_text = prefix + make_raw(rows)
            return max(1, math.ceil(len(candidate_text) * tokens_per_char * 1.03))

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
            child["num_tokens_source"] = "parent_docling_count_proportional_estimate"
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
        density = parent_tokens / max(1, len(full_text))

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
            # Small safety margin; this count is explicitly marked estimated.
            return max(1, math.ceil(len(candidate) * density * 1.03))

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
            child["num_tokens_source"] = "parent_docling_count_logical_boundary_estimate"
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
        density = parent_tokens / max(1, len(full_text))
        estimate = max(1, math.ceil(max(1, len(candidate)) * density * 1.03))
        child = copy.deepcopy(chunk)
        child["text"] = candidate
        child["num_tokens"] = estimate
        child["num_tokens_estimated"] = True
        child["num_tokens_source"] = "separator_only_markup_compaction_estimate"
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
        density = parent_tokens / max(1, len(full_text))
        estimate = max(1, math.ceil(len(candidate) * density * 1.03))
        child = copy.deepcopy(chunk)
        child["text"] = candidate
        child["num_tokens"] = estimate
        child["num_tokens_estimated"] = True
        child["num_tokens_source"] = "parent_docling_count_table_fragment_compaction_estimate"
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
        density = parent_tokens / max(1, len(full_text))
        estimate = max(1, math.ceil(len(candidate) * density * 1.03))
        child = copy.deepcopy(chunk)
        child["text"] = candidate
        child["num_tokens"] = estimate
        child["num_tokens_estimated"] = True
        child["num_tokens_source"] = "parent_docling_count_markdown_compaction_estimate"
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
        density = parent_tokens / max(1, len(full_text))
        headings = [str(v).strip() for v in (chunk.get("headings") or []) if str(v).strip()]
        candidates: list[tuple[str, str]] = []
        if headings:
            candidates.append(("deepest_heading", headings[-1] + "\n" + raw_text))
        candidates.append(("raw_only", raw_text))
        for mode, candidate in candidates:
            estimate = max(1, math.ceil(len(candidate) * density * 1.03))
            if estimate > max_tokens:
                continue
            child = copy.deepcopy(chunk)
            child["text"] = candidate
            child["num_tokens"] = estimate
            child["num_tokens_estimated"] = True
            child["num_tokens_source"] = "parent_docling_count_heading_compaction_estimate"
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
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp.replace(path)
