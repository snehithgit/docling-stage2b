from __future__ import annotations

from functools import lru_cache

from contextlib import asynccontextmanager
import asyncio
import hashlib
import json
import re
import sqlite3
import time
import uuid
import zipfile
from urllib.parse import unquote, urlparse
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
import fitz
import httpx

from .archive import select_docling_document
from .book_lifecycle import quarantine_book_artifacts, quarantine_conversion_job, restore_quarantined_artifacts
from .config import AppConfig, config_path, load_config, save_config
from .database import JobStore
from .docling_client import DoclingApiError, DoclingClient
from .events import EventBroker
from .equipment_scope import delete_equipment, equipment_catalog, load_registry, remove_manual_from_equipment, resolve_equipment_books, upsert_equipment
from .hybrid_retrieval import (
    EmbeddingServiceError,
    HybridIndexNotReady,
    build_equipment_embedding_index,
    equipment_hybrid_index_status,
    hybrid_search_equipment,
)
from .groq_quota import GroqQuotaGuard
from .manual_options import ConvertUrlRequest, ManualConvertOptions
from .maintenance_cleanup import clear_stale_files, scan_stale_files
from .oneplus_control import OnePlusControlError, OnePlusController
from .postprocess import PostprocessWorker
from .postprocess_store import PostprocessStore
from .pipeline_state import identity_metadata_status, repair_identity_metadata, stage2c_freshness, stage3_freshness, verification_rows_for_stage2c
from .retrieval import (
    add_benchmark_item,
    delete_benchmark_item,
    _load_index,
    load_benchmark,
    load_benchmark_result,
    run_benchmark,
    benchmark_expected_rank,
    search_indices,
    follow_reference,
    docling_highlight_rects,
    refresh_retrieval_artifacts,
    RETRIEVAL_RULE_VERSION,
)
from .rag_generation import build_portable_prompt, generate_grounded_answer, prepare_generation_sources, is_cross_book_query
from .groq_quota import CloudQuotaPausedError
from .stage2b import Stage2BWorker
from .stage2c import STAGE2C_RULE_VERSION, apply_human_correction_to_entry, human_review_summary, upsert_ledger_entry, verifier_audit_summary, apply_human_visual_decision, set_audit_gate_bypass
from .visual_evidence import ensure_visual_evidence_fresh, normalize_visual_entry, search_visual_indices
from .stage3 import STAGE3_RULE_VERSION, Stage3ChunkBuilder
from .telegram_bot import TelegramBotService
from .stage2b_store import Stage2BStore
from .worker import ConversionWorker
from .version import APP_VERSION


TECHNICAL_PICTURE_CLASSES = {
    "engineering_drawing", "flow_chart", "screenshot_from_manual", "table",
    "line_chart", "bar_chart", "box_plot", "full_page_image", "geographical_map"
}


def _load_json_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _entry_source_identity(entry: dict) -> tuple:
    """Stable source identity used to carry human authority across reruns/routes."""
    return (
        str(entry.get("source_type") or ""),
        entry.get("source_index"),
        entry.get("table_index"),
        entry.get("cell_index"),
        entry.get("row_start"),
        entry.get("row_end"),
        entry.get("col_start"),
        entry.get("col_end"),
    )


def _authoritative_text_entry(ledger: dict, entry_id: str) -> dict | None:
    """Resolve a text audit row against the current ledger only.

    Exact current entries are preferred, but a human-reviewed entry for the
    same Docling source target wins across a rerun/generation boundary. Old
    verification rows whose entry is no longer present in the current ledger
    are not allowed to reopen human review.
    """
    entries = [
        item for item in (ledger.get("entries") or [])
        if isinstance(item, dict)
        and item.get("entry_type") in {"text_correction", "table_cell_correction"}
        and str(item.get("status") or "") != "superseded"
    ]
    exact = next((item for item in entries if str(item.get("entry_id") or "") == str(entry_id)), None)
    if exact is None:
        return None
    identity = _entry_source_identity(exact)
    reviewed = [item for item in entries if _entry_source_identity(item) == identity and bool(item.get("human_verified"))]
    if reviewed:
        return max(reviewed, key=lambda item: float((item.get("human_review") or {}).get("saved_at_epoch") or (item.get("human_review") or {}).get("decided_at_epoch") or item.get("updated_at_epoch") or item.get("created_at_epoch") or 0))
    return exact


def _authoritative_visual_entry(
    ledger: dict,
    *,
    entry_id: str | None = None,
    source_index: int | None = None,
) -> dict | None:
    """Return one authoritative visual state for a physical Docling picture.

    Human visual decisions are image-level authority. If duplicate normal
    Vision / artifact-sweep / rerun entries exist for the same source image,
    any human-reviewed entry wins so a reviewed picture cannot reappear as an
    unresolved duplicate. Without a human decision the exact route entry wins,
    then the newest current entry is used as a deterministic fallback.
    """
    entries = [
        item for item in (ledger.get("entries") or [])
        if isinstance(item, dict)
        and item.get("entry_type") == "vision_enrichment"
        and str(item.get("status") or "") != "superseded"
    ]
    exact = next((item for item in entries if entry_id and str(item.get("entry_id") or "") == str(entry_id)), None)
    if source_index is None and exact is not None:
        try:
            source_index = int(exact.get("source_index"))
        except (TypeError, ValueError):
            source_index = None
    candidates = entries
    if source_index is not None:
        candidates = []
        for item in entries:
            try:
                if int(item.get("source_index")) == int(source_index):
                    candidates.append(item)
            except (TypeError, ValueError):
                continue
    reviewed = [
        item for item in candidates
        if bool(item.get("human_verified"))
        and str(item.get("human_visual_decision") or "") in {"technical", "decorative", "useful", "not_useful"}
    ]
    if reviewed:
        return max(reviewed, key=lambda item: float(item.get("human_visual_decided_at_epoch") or item.get("created_at_epoch") or 0))
    if exact is not None and exact in candidates:
        return exact
    if candidates:
        return max(candidates, key=lambda item: float(item.get("created_at_epoch") or 0))
    return None


async def _current_audit_result_dirs() -> dict[int, str]:
    """Return current post-process result directories when the lifecycle table exists.

    Audit endpoints also remain readable during partial test/upgrade states where
    only the verification table has been initialized; in that case callers
    safely fall back to the verification row's persisted result_dir.
    """
    try:
        rows = await runtime.postprocess_store.list_jobs(limit=5000)
    except sqlite3.OperationalError:
        return {}
    return {
        int(item.get("id") or 0): Path(str(item.get("result_dir") or "")).name
        for item in rows if item.get("result_dir")
    }


def _render_pdf_page_png(
    pdf_path: Path,
    page: int,
    *,
    highlight_refs: list[str] | None = None,
    converted_zip: Path | None = None,
) -> bytes:
    """Synchronous PDF rendering helper intended for ``asyncio.to_thread``."""
    with fitz.open(pdf_path) as pdf:
        if page > len(pdf):
            raise IndexError("PDF page not found")
        pdf_page = pdf[page - 1]
        refs = [value for value in (highlight_refs or []) if value.strip()]
        if refs and converted_zip is not None and converted_zip.is_file():
            try:
                rects = docling_highlight_rects(converted_zip, refs, page, pdf_page.rect.height)
            except (OSError, ValueError, zipfile.BadZipFile):
                rects = []
            for rect in rects:
                clipped = rect & pdf_page.rect
                if clipped.width > 0.5 and clipped.height > 0.5:
                    pdf_page.draw_rect(clipped, color=(0.86, 0.18, 0.12), width=2.2, overlay=True)
        pix = pdf_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        return pix.tobytes("png")


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _picture_top_class(picture: dict) -> tuple[str | None, float | None]:
    predictions: list[dict] = []
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


def _is_technical_picture_class(class_name: str | None) -> bool:
    return bool(class_name and str(class_name) in TECHNICAL_PICTURE_CLASSES)


def _docling_pictures(zip_path: Path) -> list[dict]:
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("Converted output is not a ZIP archive")
    with zipfile.ZipFile(zip_path) as archive:
        document, _json_member = select_docling_document(archive)
    return [item for item in (document.get("pictures") or []) if isinstance(item, dict)]


def _picture_image_from_converted_zip(zip_path: Path, picture_index: int) -> tuple[bytes, str, str]:
    pictures = _docling_pictures(zip_path)
    if picture_index < 0 or picture_index >= len(pictures):
        raise IndexError("Picture index is outside the Docling document")
    picture = pictures[picture_index]
    image = picture.get("image") or {}
    uri = Path(str(image.get("uri") or "")).as_posix()
    if not uri:
        raise KeyError("Picture artifact path is missing")
    mime = str(image.get("mimetype") or "image/png")
    with zipfile.ZipFile(zip_path) as archive:
        data = archive.read(uri)
    return data, mime, uri


class SettingsUpdate(BaseModel):
    docling_url: str = Field(min_length=8, max_length=500)
    input_dir: str = Field(min_length=1, max_length=1000)
    output_dir: str = Field(min_length=1, max_length=1000)
    # New API: explicit watcher multi-select. Legacy output_format remains
    # accepted so an existing UI/client does not break during upgrade.
    output_formats: list[str] | None = None
    output_format: str | None = Field(default=None, pattern="^(md|json|html|text|doctags)$")

    @field_validator("output_formats")
    @classmethod
    def validate_output_formats(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        allowed = {"md", "json", "html", "text", "doctags"}
        normalized = list(dict.fromkeys(value))
        if not normalized or any(item not in allowed for item in normalized):
            raise ValueError("Choose one or more valid output formats")
        return normalized

    @model_validator(mode="after")
    def require_a_format_choice(self):
        if not self.output_formats and not self.output_format:
            raise ValueError("Choose at least one watcher output format")
        return self




class AddBookUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Enter a valid http(s) document URL")
        return value.strip()


class WatcherAutoRunUpdate(BaseModel):
    enabled: bool


class DeleteBookRequest(BaseModel):
    confirm: bool = False


class Stage2BAutoRunUpdate(BaseModel):
    enabled: bool


class VerifierProviderUpdate(BaseModel):
    provider: str = Field(pattern="^(pi5|oneplus|groq)$")


class EquipmentManualAssignment(BaseModel):
    postprocess_job_id: int = Field(gt=0)
    manual_type: str = Field(default="other", pattern="^(description|operation|maintenance|electrical|hydraulic|parts|tools|service|installation|other)$")
    revision: str | None = Field(default=None, max_length=80)
    revision_date: str | None = Field(default=None, max_length=40)
    authority_status: str | None = Field(default=None, pattern="^(authoritative|historical|draft)$")
    supersedes_postprocess_job_id: int | None = Field(default=None, gt=0)


class EquipmentUpsertRequest(BaseModel):
    equipment_id: str | None = Field(default=None, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    manufacturer: str = Field(default="", max_length=160)
    model: str = Field(default="", max_length=160)
    notes: str = Field(default="", max_length=1000)
    manuals: list[EquipmentManualAssignment] = Field(min_length=1)


class HumanCorrectionUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    action: str = Field(default="apply", pattern="^(apply|reject|propose)$")


class VisualAuditDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(technical|decorative|useful|not_useful)$")


class AuditBypassRequest(BaseModel):
    enabled: bool = True
    reason: str = Field(default="testing", max_length=200)


class StaleCleanupConfirmRequest(BaseModel):
    confirmation_token: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")



def _require_exact_retrieval_scope(postprocess_job_id: int | None, equipment_id: str | None) -> None:
    has_book = postprocess_job_id is not None
    has_equipment = bool(str(equipment_id or "").strip())
    if has_book == has_equipment:
        raise ValueError("Choose exactly one retrieval scope: one book or one equipment")


class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    postprocess_job_id: int | None = None
    equipment_id: str | None = Field(default=None, max_length=120)
    retrieval_mode: str = Field(default="hybrid", pattern="^(hybrid|lexical)$")

    @model_validator(mode="after")
    def require_scope(self):
        _require_exact_retrieval_scope(self.postprocess_job_id, self.equipment_id)
        return self


class RetrievalGenerateRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    provider: str = Field(pattern="^(pi5|oneplus|groq)$")
    top_k: int = Field(default=5, ge=1, le=10)
    postprocess_job_id: int | None = None
    equipment_id: str | None = Field(default=None, max_length=120)
    retrieval_mode: str = Field(default="hybrid", pattern="^(hybrid|lexical)$")

    @model_validator(mode="after")
    def require_scope(self):
        _require_exact_retrieval_scope(self.postprocess_job_id, self.equipment_id)
        return self


class RetrievalPromptExportRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=10)
    postprocess_job_id: int | None = None
    equipment_id: str | None = Field(default=None, max_length=120)
    retrieval_mode: str = Field(default="hybrid", pattern="^(hybrid|lexical)$")

    @model_validator(mode="after")
    def require_scope(self):
        _require_exact_retrieval_scope(self.postprocess_job_id, self.equipment_id)
        return self


class RetrievalHybridIndexRequest(BaseModel):
    postprocess_job_id: int | None = None
    equipment_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def require_machine_scope(self):
        if self.postprocess_job_id is not None:
            raise ValueError("Hybrid embeddings are machine-scoped; choose a machine/equipment scope")
        if not str(self.equipment_id or "").strip():
            raise ValueError("Choose one machine/equipment scope before building embeddings")
        return self


class RetrievalBenchmarkAddRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    result: dict
    note: str = Field(default="", max_length=1000)
    equipment_id: str | None = Field(default=None, max_length=120)


class RetrievalFollowReferenceRequest(BaseModel):
    postprocess_job_id: int
    title: str = Field(default="", max_length=300)
    section: str = Field(default="", max_length=100)
    query: str = Field(default="", max_length=1000)
    top_k: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def require_reference(self):
        if not self.title.strip() and not self.section.strip():
            raise ValueError("Reference title or section is required")
        return self


def _docling_page_of(item: dict) -> int | None:
    prov = item.get("prov") or []
    if prov and isinstance(prov[0], dict):
        value = prov[0].get("page_no")
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _docling_review_context(zip_path: Path, source_index: int, expected_page: int | None, window: int = 3) -> dict:
    """Return raw Docling text immediately above/at/below one text item.

    This is deliberately independent of Pi5/OnePlus verification.  The review
    page uses it only to show immutable Docling reading-order context so a
    person can reconstruct a damaged OCR span safely.
    """
    if source_index < 0:
        raise IndexError("Docling source index must be non-negative")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("Converted output is not a ZIP archive")
    with zipfile.ZipFile(zip_path) as archive:
        document, json_member = select_docling_document(archive)
    texts = document.get("texts") or []
    if source_index >= len(texts):
        raise IndexError(f"Text index {source_index} is outside the Docling document")

    target_item = texts[source_index]
    target_page = _docling_page_of(target_item)
    page = target_page if target_page is not None else expected_page

    def row(index: int, item: dict) -> dict:
        return {
            "index": index,
            "page": _docling_page_of(item),
            "label": str(item.get("label") or "text"),
            "text": str(item.get("text") or ""),
        }

    above: list[dict] = []
    idx = source_index - 1
    while idx >= 0 and len(above) < window:
        item = texts[idx]
        if page is not None and _docling_page_of(item) != page:
            idx -= 1
            continue
        if str(item.get("text") or "").strip():
            above.append(row(idx, item))
        idx -= 1
    above.reverse()

    below: list[dict] = []
    idx = source_index + 1
    while idx < len(texts) and len(below) < window:
        item = texts[idx]
        if page is not None and _docling_page_of(item) != page:
            idx += 1
            continue
        if str(item.get("text") or "").strip():
            below.append(row(idx, item))
        idx += 1

    return {
        "schema": "docling-human-review-context/v1",
        "source": "raw_docling_json",
        "docling_json_member": json_member,
        "page": page,
        "source_index": source_index,
        "above": above,
        "target": row(source_index, target_item),
        "below": below,
    }


def _docling_table_review_context(zip_path: Path, table_index: int, cell_index: int) -> dict:
    """Return immutable Docling table context for one table-cell correction."""
    if table_index < 0 or cell_index < 0:
        raise IndexError("Docling table/cell index must be non-negative")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("Converted output is not a ZIP archive")
    with zipfile.ZipFile(zip_path) as archive:
        document, json_member = select_docling_document(archive)
    tables = document.get("tables") or []
    if table_index >= len(tables):
        raise IndexError(f"Table index {table_index} is outside the Docling document")
    table = tables[table_index]
    cells = ((table.get("data") or {}).get("table_cells") or [])
    if cell_index >= len(cells):
        raise IndexError(f"Cell index {cell_index} is outside table {table_index}")
    target = cells[cell_index]

    def number(cell: dict, key: str, default: int = 0) -> int:
        try:
            return int(cell.get(key))
        except (TypeError, ValueError):
            return default

    def span(cell: dict) -> tuple[int, int, int, int]:
        return (
            number(cell, "start_row_offset_idx"),
            number(cell, "end_row_offset_idx", number(cell, "start_row_offset_idx") + 1),
            number(cell, "start_col_offset_idx"),
            number(cell, "end_col_offset_idx", number(cell, "start_col_offset_idx") + 1),
        )

    rs, re_, cs, ce = span(target)
    page = _docling_page_of(table) or _docling_page_of(target)

    def overlaps(a0: int, a1: int, b0: int, b1: int) -> bool:
        return max(a0, b0) < min(a1, b1)

    def cell_row(index: int, cell: dict) -> dict:
        a, b, x, y = span(cell)
        return {
            "index": index,
            "page": _docling_page_of(cell) or page,
            "label": "column header" if cell.get("column_header") else "row header" if cell.get("row_header") else "table cell",
            "text": str(cell.get("text") or ""),
            "row_start": a, "row_end": b, "col_start": x, "col_end": y,
        }

    header_cells: list[dict] = []
    row_cells: list[dict] = []
    for idx, cell in enumerate(cells):
        if idx == cell_index:
            continue
        a, b, x, y = span(cell)
        if cell.get("column_header") and overlaps(x, y, cs, ce):
            header_cells.append(cell_row(idx, cell))
        if overlaps(a, b, rs, re_):
            row_cells.append(cell_row(idx, cell))
    header_cells.sort(key=lambda item: (item["row_start"], item["col_start"], item["index"]))
    row_cells.sort(key=lambda item: (item["col_start"], item["row_start"], item["index"]))
    return {
        "schema": "docling-human-table-review-context/v1",
        "source": "raw_docling_json",
        "docling_json_member": json_member,
        "page": page,
        "source_type": "table_cell",
        "table_index": table_index,
        "cell_index": cell_index,
        "row_start": rs, "row_end": re_, "col_start": cs, "col_end": ce,
        "headers": header_cells,
        "target": cell_row(cell_index, target),
        "row_cells": row_cells,
    }


def _artifact_inventory_for_book(job: dict, verification_rows: list[dict], processed_dir: Path, output_dir: Path) -> list[dict]:
    if not job.get("result_dir"):
        return []
    result_dir = processed_dir / Path(str(job["result_dir"])).name
    manifest = _load_json_file(result_dir / "source_manifest.json")
    ledger = _load_json_file(result_dir / "correction_ledger.json")
    converted_name = Path(str(manifest.get("converted_zip") or job.get("output_filename") or "")).name
    zip_path = output_dir / converted_name
    if not converted_name or not zip_path.is_file():
        return []
    try:
        pictures = _docling_pictures(zip_path)
    except (OSError, ValueError, zipfile.BadZipFile):
        return []

    verification_by_index: dict[int, dict] = {}
    for row in verification_rows:
        if row.get("target") not in {"pi5", "oneplus"}:
            continue
        source = _audit_json(row.get("source_json"))
        if str(source.get("type") or "") != "picture":
            continue
        source_index_value = source.get("index")
        if source_index_value is None:
            source_index_value = source.get("picture_index")
        if source_index_value is None:
            source_index_value = source.get("source_index")
        try:
            picture_index = int(source_index_value)
        except (TypeError, ValueError):
            continue
        parsed_result = _audit_json(row.get("result_json")).get("parsed")
        parsed = parsed_result if isinstance(parsed_result, dict) else {}
        # A normal picture route is authoritative when legacy data still
        # contains an overlapping FULL_TECHNICAL_VISUAL row. New releases
        # suppress those overlaps at queue preparation time; this preference is
        # an additional read-side guard for upgraded databases.
        existing = verification_by_index.get(picture_index)
        if existing and existing.get("route_code") != "FULL_TECHNICAL_VISUAL" and row.get("code") == "FULL_TECHNICAL_VISUAL":
            continue
        verification_by_index[picture_index] = {
            "job_id": int(row.get("id") or 0),
            "route_id": row.get("route_id"),
            "route_code": row.get("code"),
            "route_reason": row.get("reason"),
            "status": row.get("status"),
            "provider": (_audit_json(row.get("result_json")).get("vision_provider") or _audit_json(row.get("request_json")).get("vision_provider")),
            "verdict": parsed.get("verdict") or row.get("verdict"),
            "diagram_category": parsed.get("diagram_category"),
            "summary": parsed.get("summary"),
            "visible_text": parsed.get("visible_text") or [],
            "error_type": row.get("error_type"),
            "error_message": row.get("error_message"),
            "completed_at": row.get("completed_at"),
            "processing_seconds": row.get("processing_seconds"),
        }

    ledger_by_index: dict[int, dict] = {}
    current_visual_indexes: set[int] = set()
    for entry in ledger.get("entries") or []:
        if (not isinstance(entry, dict) or entry.get("entry_type") != "vision_enrichment"
                or str(entry.get("status") or "") == "superseded"):
            continue
        try:
            current_visual_indexes.add(int(entry.get("source_index")))
        except (TypeError, ValueError):
            continue
    for source_index in current_visual_indexes:
        authoritative = _authoritative_visual_entry(ledger, source_index=source_index)
        if authoritative is not None:
            ledger_by_index[source_index] = authoritative

    book_name = str(job.get("source_filename") or job.get("output_filename") or result_dir.name)
    items: list[dict] = []
    for picture_index, picture in enumerate(pictures):
        class_name, confidence = _picture_top_class(picture)
        prov = picture.get("prov") or []
        page = None
        if prov and isinstance(prov[0], dict):
            page_value = prov[0].get("page_no")
            if isinstance(page_value, (int, float)):
                page = int(page_value)
        image = picture.get("image") or {}
        verification = verification_by_index.get(picture_index)
        downstream = ledger_by_index.get(picture_index)
        visual_rag = normalize_visual_entry(
            downstream,
            postprocess_job_id=int(job.get("id") or 0),
            source_filename=book_name,
            result_dir_name=result_dir.name,
        ) if downstream else None
        technical = _is_technical_picture_class(class_name) or bool(verification) or bool(downstream)
        items.append({
            "postprocess_job_id": int(job.get("id") or 0),
            "book": book_name,
            "result_dir": result_dir.name,
            "picture_index": picture_index,
            "page": page,
            "artifact": image.get("uri"),
            "docling_picture_class": class_name,
            "docling_picture_confidence": confidence,
            "technical_candidate": technical,
            "image_url": f"/api/postprocess/jobs/{int(job.get('id') or 0)}/picture/{picture_index}",
            "page_url": f"/api/postprocess/jobs/{int(job.get('id') or 0)}/source-page/{int(page)}" if page else None,
            "verification": verification,
            "downstream": {
                "status": downstream.get("status") if downstream else None,
                "status_reason": downstream.get("status_reason") if downstream else None,
                "verification_verdict": downstream.get("verification_verdict") if downstream else None,
                "diagram_category": downstream.get("diagram_category") if downstream else None,
                "generated_summary": downstream.get("generated_summary") if downstream else None,
                "unresolved": downstream.get("unresolved") if downstream else None,
                "visible_text": downstream.get("visible_text") if downstream else None,
                "visible_objects": downstream.get("visible_objects") if downstream else None,
                "entry_id": downstream.get("entry_id") if downstream else None,
                "human_visual_decision": downstream.get("human_visual_decision") if downstream else None,
                "human_verified": bool(downstream.get("human_verified")) if downstream else False,
                "current_authoritative": True,
                "human_evidence_recovery_required": bool(downstream.get("human_evidence_recovery_required")) if downstream else False,
                "rag_eligible": visual_rag.get("rag_eligible") if visual_rag else False,
                "rag_eligibility_reason": visual_rag.get("rag_eligibility_reason") if visual_rag else None,
                "visual_evidence_id": visual_rag.get("visual_evidence_id") if visual_rag else None,
            } if downstream else None,
            "captions": [ref.get("$ref") for ref in (picture.get("captions") or []) if isinstance(ref, dict) and ref.get("$ref")],
            "references": [ref.get("$ref") for ref in (picture.get("references") or []) if isinstance(ref, dict) and ref.get("$ref")],
        })
    return items


class Runtime:
    def __init__(self) -> None:
        self.config_file = config_path()
        self.config: AppConfig = load_config(self.config_file)
        self.store, self.events = JobStore(self.config.database_path), EventBroker()
        self.groq_quota = GroqQuotaGuard(lambda: self.config)
        self.client = DoclingClient(lambda: self.config)
        self.worker = ConversionWorker(lambda: self.config, self.store, self.client, self.events)
        self.postprocess_store = PostprocessStore(self.config.database_path)
        self.postprocess_worker = PostprocessWorker(
            lambda: self.config, self.postprocess_store, self.events, self.groq_quota
        )
        self.stage2b_store = Stage2BStore(self.config.database_path)
        self.oneplus_controller = OnePlusController(lambda: self.config)
        self.stage2b_worker = Stage2BWorker(
            lambda: self.config, self.stage2b_store, self.postprocess_store, self.events,
            self.groq_quota, self.oneplus_controller.restart,
        )
        self.stage3_builder = Stage3ChunkBuilder(
            lambda: self.config, self.postprocess_store, self.client, self.events, self.stage2b_store
        )
        self.telegram_bot = TelegramBotService(
            lambda: self.config, self.telegram_command, self.events.stream, self.telegram_audit
        )
        self.pipeline_sequence_task: asyncio.Task | None = None
        self.pipeline_sequence_state: dict = {
            "status": "idle", "current_book": None, "current_stage": None,
            "last_error": None, "last_run_at_epoch": None, "advanced": 0,
        }
        self._pipeline_embedding_retry_after: dict[str, float] = {}
        self.safety_refresh_task: asyncio.Task | None = None
        self.safety_refresh_state: dict = {
            "status": "idle", "total_books": 0, "processed_books": 0,
            "current_book": None, "current_stage": None, "results": [], "errors": 0,
        }

    async def update_settings(self, update: SettingsUpdate) -> AppConfig:
        # Watcher formats are an explicit multi-select. The exact selected
        # combination is persisted and new queue jobs snapshot that list.
        if update.output_formats is not None:
            selected_formats = list(update.output_formats)
        else:
            # Backward-compatible single-format API: keep any existing
            # secondary watcher formats and only move the chosen one first.
            assert update.output_format is not None
            selected_formats = [update.output_format] + [
                item for item in self.config.to_formats if item != update.output_format
            ]
        revised = AppConfig(**{
            **self.config.__dict__,
            "docling_url": update.docling_url,
            "input_dir": update.input_dir,
            "output_dir": update.output_dir,
            "to_formats": selected_formats,
        })
        revised.validate()
        Path(revised.input_dir).mkdir(parents=True, exist_ok=True)
        Path(revised.output_dir).mkdir(parents=True, exist_ok=True)
        save_config(self.config_file, revised)
        self.config = revised
        self.events.notify("settings_updated")
        return revised

    async def set_watcher_auto_run(self, enabled: bool) -> AppConfig:
        revised = AppConfig(**{**self.config.__dict__, "watcher_auto_run": bool(enabled)})
        revised.validate()
        save_config(self.config_file, revised)
        self.config = revised
        await self.worker.set_auto_run(bool(enabled))
        return revised

    async def set_stage2b_paused(self, target: str, paused: bool) -> AppConfig:
        if target not in {"pi5", "oneplus"}:
            raise ValueError("Unknown Stage 2B target")
        paused_field = "stage2b_pi5_paused" if target == "pi5" else "stage2b_oneplus_paused"
        auto_field = "stage2b_pi5_auto_run" if target == "pi5" else "stage2b_oneplus_auto_run"
        revised = AppConfig(**{**self.config.__dict__, paused_field: bool(paused), auto_field: False if paused else getattr(self.config, auto_field)})
        revised.validate()
        save_config(self.config_file, revised)
        self.config = revised
        if paused:
            await self.stage2b_store.clear_manual_authorizations(target)
        self.events.notify("stage2b_mode_updated")
        return revised

    async def set_stage2b_auto_run(self, target: str, enabled: bool) -> AppConfig:
        if target not in {"pi5", "oneplus"}:
            raise ValueError("Unknown Stage 2B target")
        field = "stage2b_pi5_auto_run" if target == "pi5" else "stage2b_oneplus_auto_run"
        paused_field = "stage2b_pi5_paused" if target == "pi5" else "stage2b_oneplus_paused"
        revised = AppConfig(**{**self.config.__dict__, field: bool(enabled), paused_field: False if enabled else getattr(self.config, paused_field)})
        revised.validate()
        save_config(self.config_file, revised)
        self.config = revised
        if enabled:
            # Auto mode owns all pending work. Clear manual-batch snapshots so
            # switching Auto Run off later pauses cleanly after the in-flight job.
            await self.stage2b_store.clear_manual_authorizations(target)
        self.events.notify("stage2b_mode_updated")
        return revised

    async def set_verifier_provider(self, kind: str, provider: str) -> AppConfig:
        if kind not in {"text", "vision"}:
            raise ValueError("Verifier kind must be text or vision")
        if provider not in {"pi5", "oneplus", "groq"}:
            raise ValueError("Verifier provider must be pi5, oneplus, or groq")
        target = "pi5" if kind == "text" else "oneplus"
        if self.stage2b_worker.worker_state.get(target, {}).get("active_job_id"):
            raise ValueError("Stop the active verifier request before changing provider")
        field = "text_verifier_provider" if kind == "text" else "vision_verifier_provider"
        revised = AppConfig(**{**self.config.__dict__, field: provider})
        revised.validate()
        save_config(self.config_file, revised)
        self.config = revised
        self.stage2b_worker._model_cache.pop(target, None)
        self.events.notify("stage2b_provider_updated")
        return revised

    async def set_stage2b_auto_run_all(self, enabled: bool) -> AppConfig:
        revised = AppConfig(**{
            **self.config.__dict__,
            "stage2b_pi5_auto_run": bool(enabled),
            "stage2b_oneplus_auto_run": bool(enabled),
            "stage2b_pi5_paused": False if enabled else self.config.stage2b_pi5_paused,
            "stage2b_oneplus_paused": False if enabled else self.config.stage2b_oneplus_paused,
        })
        revised.validate()
        save_config(self.config_file, revised)
        self.config = revised
        if enabled:
            await self.stage2b_store.clear_manual_authorizations("pi5")
            await self.stage2b_store.clear_manual_authorizations("oneplus")
        self.events.notify("stage2b_mode_updated")
        return revised


    async def start_safety_refresh_all(self) -> dict:
        if self.safety_refresh_task and not self.safety_refresh_task.done():
            return {"accepted": False, "reason": "already_running", **self.safety_refresh_state}
        books = await self.stage2b_store.list_books()
        self.safety_refresh_state = {
            "status": "queued",
            "total_books": len(books),
            "processed_books": 0,
            "current_book": None,
            "current_stage": None,
            "results": [],
            "errors": 0,
            "started_at_epoch": time.time(),
            "completed_at_epoch": None,
        }
        self.safety_refresh_task = asyncio.create_task(
            self._run_safety_refresh_all(books), name="safety-refresh-all-books"
        )
        return {"accepted": True, **self.safety_refresh_state}

    async def _wait_stage2c_refresh(self, postprocess_job_id: int) -> dict:
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            state = self.stage2b_worker.stage2c_state_for(postprocess_job_id) or {}
            status = str(state.get("status") or "")
            if status == "completed":
                return state
            if status == "partial":
                raise RuntimeError(str(state.get("error_message") or "Stage 2C rebuild completed only partially; Stage 3 is blocked until Stage 2C completes successfully"))
            if status == "failed":
                raise RuntimeError(str(state.get("error") or state.get("error_message") or "Stage 2C rebuild failed"))
            await asyncio.sleep(0.5)
        raise TimeoutError("Stage 2C rebuild timed out")

    async def _wait_stage3_refresh(self, postprocess_job_id: int) -> dict:
        deadline = time.monotonic() + max(1800, int(self.config.stage3_timeout_minutes) * 60 + 300)
        while time.monotonic() < deadline:
            state = self.stage3_builder.state_for(postprocess_job_id) or {}
            status = str(state.get("status") or "")
            if status == "completed":
                return state
            if status == "failed":
                raise RuntimeError(str(state.get("error") or "Stage 3 rebuild failed"))
            await asyncio.sleep(1.0)
        raise TimeoutError("Stage 3 rebuild timed out")

    async def _run_safety_refresh_all(self, books: list[dict]) -> None:
        state = self.safety_refresh_state
        state["status"] = "running"
        try:
            for book in books:
                job_id = int(book.get("postprocess_job_id") or 0)
                name = str(book.get("output_filename") or book.get("result_dir") or job_id)
                item = {"postprocess_job_id": job_id, "book": name, "status": "running"}
                state["results"].append(item)
                state["current_book"] = name
                terminal_blockers = sum(int(book.get(k) or 0) for k in (
                    "pi5_pending", "pi5_processing", "pi5_failed",
                    "oneplus_pending", "oneplus_processing", "oneplus_failed",
                ))
                if terminal_blockers:
                    item.update({"status": "skipped", "reason": "Stage 2B still has pending, processing, or failed routes"})
                    state["processed_books"] += 1
                    continue
                try:
                    state["current_stage"] = "revalidate_saved_pi5"
                    item["revalidation"] = await self.stage2b_worker.revalidate_saved_pi5_results(job_id)

                    state["current_stage"] = "stage2c"
                    started2c = await self.stage2b_worker.start_stage2c_backfill(job_id)
                    if not started2c.get("accepted") and started2c.get("reason") != "already_running":
                        raise RuntimeError(str(started2c.get("reason") or "Could not start Stage 2C"))
                    item["stage2c"] = await self._wait_stage2c_refresh(job_id)

                    state["current_stage"] = "stage3"
                    started3 = await self.stage3_builder.start(job_id)
                    if not started3.get("accepted") and started3.get("reason") != "already_running":
                        raise RuntimeError(str(started3.get("reason") or "Could not start Stage 3"))
                    item["stage3"] = await self._wait_stage3_refresh(job_id)
                    item["status"] = "completed"
                except Exception as exc:
                    item.update({"status": "failed", "error": str(exc)})
                    state["errors"] += 1
                state["processed_books"] += 1
                self.events.notify("safety_refresh_progress")
            state["status"] = "completed" if not state["errors"] else "partial"
        except asyncio.CancelledError:
            state["status"] = "cancelled"
            raise
        finally:
            state["current_book"] = None
            state["current_stage"] = None
            state["completed_at_epoch"] = time.time()
            self.events.notify("safety_refresh_finished")

    async def start_pipeline_sequence(self) -> None:
        if self.pipeline_sequence_task and not self.pipeline_sequence_task.done():
            return
        self.pipeline_sequence_task = asyncio.create_task(
            self._pipeline_sequence_loop(), name="strict-pipeline-sequence"
        )

    async def stop_pipeline_sequence(self) -> None:
        if self.pipeline_sequence_task and not self.pipeline_sequence_task.done():
            self.pipeline_sequence_task.cancel()
            await asyncio.gather(self.pipeline_sequence_task, return_exceptions=True)
        self.pipeline_sequence_task = None

    async def _pipeline_sequence_loop(self) -> None:
        self.pipeline_sequence_state["status"] = "running"
        while True:
            try:
                await self._advance_pipeline_sequence_once()
                self.pipeline_sequence_state["last_error"] = None
            except asyncio.CancelledError:
                self.pipeline_sequence_state["status"] = "stopped"
                raise
            except Exception as exc:
                self.pipeline_sequence_state["last_error"] = str(exc)[:1000]
                self.events.notify(
                    "pipeline_sequence_error",
                    postprocess_job_id=self.pipeline_sequence_state.get("current_book"),
                    stage=self.pipeline_sequence_state.get("current_stage"),
                    error=f"{type(exc).__name__}: {exc}",
                )
            self.pipeline_sequence_state["last_run_at_epoch"] = time.time()
            await asyncio.sleep(max(3, int(self.config.postprocess_poll_interval_seconds)))

    async def _advance_pipeline_sequence_once(self) -> None:
        """Advance downstream stages only when the immediately prior stage is current.

        Verification remains controlled by its existing manual/auto setting. Once all
        current Stage 2B routes finish successfully, Stage 2C -> Stage 3 -> machine
        embeddings advance in strict order. Any newer upstream output makes the
        downstream signature stale and therefore rebuildable before RAG can use it.
        """
        jobs = [row for row in await self.postprocess_store.list_jobs(limit=-1) if row.get("status") == "completed" and row.get("result_dir")]
        verification_summary = {int(row.get("postprocess_job_id") or 0): row for row in await self.stage2b_store.list_books()}
        book_catalog: list[dict] = []
        advanced = 0
        for job in jobs:
            job_id = int(job.get("id") or 0)
            result_dir = Path(self.config.processed_dir) / Path(str(job.get("result_dir") or "")).name
            await asyncio.to_thread(repair_identity_metadata, result_dir, job_id)
            summary = verification_summary.get(job_id) or {}
            total = int(summary.get("total") or 0)
            current_stage3 = False
            if total:
                blockers = sum(int(summary.get(key) or 0) for key in (
                    "pi5_pending", "pi5_processing", "pi5_failed",
                    "oneplus_pending", "oneplus_processing", "oneplus_failed",
                ))
                if not blockers:
                    rows = await self.stage2b_store.list_book_jobs_raw(job_id)
                    stage2c = stage2c_freshness(result_dir, rows, rule_version=STAGE2C_RULE_VERSION, artifact_sweep_required=bool(getattr(runtime.config, "stage2b_artifact_sweep_required_for_finalize", True)))
                    if not stage2c.get("ready"):
                        running = self.stage2b_worker.stage2c_state_for(job_id) or {}
                        if str(running.get("status") or "") not in {"queued", "running"} and bool(self.config.stage2c_auto_finalize_after_stage2b):
                            self.pipeline_sequence_state.update({"current_book": job_id, "current_stage": "stage2c"})
                            try:
                                started = await self.stage2b_worker.start_stage2c_backfill(job_id)
                                if started.get("accepted"):
                                    advanced += 1
                            except ValueError:
                                pass
                    else:
                        audit_gate = await asyncio.to_thread(
                            verifier_audit_summary, result_dir,
                            text_require_human=bool(self.config.stage2c_require_human_review),
                        )
                        if audit_gate.get("blocking_review_required", 0):
                            book_catalog.append({"postprocess_job_id": job_id, "result_dir": result_dir, "stage3_current": False, "audit_waiting": audit_gate.get("review_required", 0)})
                            self.pipeline_sequence_state.update({"current_book": job_id, "current_stage": "verifier_audit"})
                            continue
                        stage3 = stage3_freshness(result_dir, stage2c, stage3_rule_version=STAGE3_RULE_VERSION, retrieval_rule_version=RETRIEVAL_RULE_VERSION)
                        current_stage3 = bool(stage3.get("ready"))
                        if not current_stage3:
                            # Retrieval-rule upgrades do not require Docling or Stage 3
                            # chunk regeneration. Rebuild only derived retrieval artifacts
                            # from the existing canonical chunks, then machine embeddings
                            # become stale naturally because the retrieval-index signature changed.
                            if stage3.get("reason") == "retrieval_rules_stale" and stage3.get("canonical_ready"):
                                self.pipeline_sequence_state.update({"current_book": job_id, "current_stage": "retrieval_refresh"})
                                try:
                                    await asyncio.to_thread(
                                        refresh_retrieval_artifacts, result_dir,
                                        max_tokens=self.config.stage3_chunk_max_tokens,
                                    )
                                    advanced += 1
                                    current_stage3 = True
                                    self.events.notify("pipeline_retrieval_refresh_completed")
                                except (OSError, ValueError, json.JSONDecodeError):
                                    current_stage3 = False
                            else:
                                running = self.stage3_builder.state_for(job_id) or {}
                                if str(running.get("status") or "") not in {"queued", "running"}:
                                    self.pipeline_sequence_state.update({"current_book": job_id, "current_stage": "stage3"})
                                    try:
                                        started = await self.stage3_builder.start(job_id)
                                        if started.get("accepted"):
                                            advanced += 1
                                    except ValueError:
                                        pass
            book_catalog.append({
                "postprocess_job_id": job_id,
                "source_filename": str(job.get("source_filename") or job.get("output_filename") or result_dir.name),
                "result_dir": result_dir.name,
                "index_ready": bool(current_stage3 and (result_dir / "retrieval_index.jsonl").is_file()),
            })

        # Embeddings are one persisted index per physical machine. Build/rebuild only
        # after every assigned manual has a current Stage 3 retrieval index.
        if self.config.retrieval_hybrid_enabled and book_catalog:
            catalog = equipment_catalog(Path(self.config.processed_dir), book_catalog)
            for equipment in catalog.get("equipment") or []:
                equipment_id = str(equipment.get("equipment_id") or "")
                selected = resolve_equipment_books(Path(self.config.processed_dir), book_catalog, equipment_id)
                if not selected or not all(bool(row.get("index_ready")) for row in selected):
                    continue
                index_paths = [Path(self.config.processed_dir) / str(row["result_dir"]) / "retrieval_index.jsonl" for row in selected]
                manual_types = {
                    int(item.get("postprocess_job_id") or 0): str(item.get("manual_type") or "other")
                    for item in equipment.get("manuals") or [] if int(item.get("postprocess_job_id") or 0) > 0 and item.get("active_for_rag", True)
                }
                status = equipment_hybrid_index_status(
                    Path(self.config.processed_dir), equipment_id, index_paths,
                    model=self.config.retrieval_embedding_model,
                    document_prefix=self.config.retrieval_embedding_document_prefix,
                    manual_types=manual_types,
                )
                if status.get("ready"):
                    continue
                if time.time() < float(self._pipeline_embedding_retry_after.get(equipment_id, 0)):
                    continue
                self.pipeline_sequence_state.update({"current_book": equipment_id, "current_stage": "machine_embedding"})
                try:
                    await asyncio.to_thread(
                        build_equipment_embedding_index,
                        Path(self.config.processed_dir), equipment_id, index_paths,
                        base_url=self.config.retrieval_embedding_url,
                        model=self.config.retrieval_embedding_model,
                        document_prefix=self.config.retrieval_embedding_document_prefix,
                        manual_types=manual_types,
                        batch_size=self.config.retrieval_embedding_batch_size,
                        timeout_seconds=self.config.retrieval_embedding_timeout_seconds,
                    )
                    self._pipeline_embedding_retry_after.pop(equipment_id, None)
                    advanced += 1
                    self.events.notify("pipeline_machine_embedding_completed")
                except (EmbeddingServiceError, OSError, RuntimeError, ValueError) as exc:
                    self._pipeline_embedding_retry_after[equipment_id] = time.time() + 60
                    self.pipeline_sequence_state["last_error"] = f"{equipment_id}: {exc}"

        self.pipeline_sequence_state.update({
            "current_book": None, "current_stage": None,
            "advanced": int(self.pipeline_sequence_state.get("advanced") or 0) + advanced,
        })
        if advanced:
            self.events.notify("pipeline_sequence_advanced")

    @staticmethod
    def _telegram_trim(value: object, limit: int = 240) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"

    @staticmethod
    def _telegram_code(value: object, limit: int = 240) -> str:
        """Render a dynamic value as a Markdown code span without trusting its contents."""
        text = Runtime._telegram_trim(value, limit) or "—"
        longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
        fence = "`" * max(1, longest + 1)
        return f"{fence}{text}{fence}"

    async def _telegram_current_audit_books(self) -> list[dict]:
        """Current completed post-process books; historical runs are never review authority."""
        if hasattr(self, "postprocess_store"):
            rows = await self.postprocess_store.list_jobs(limit=5000)
            return [row for row in rows if str(row.get("status") or "") == "completed" and row.get("result_dir")]
        # Lightweight compatibility for tests/upgrades that expose only the
        # verification store. Production always uses PostprocessStore above.
        raw = (
            await self.stage2b_store.list_results_raw("pi5", limit=5000)
            + await self.stage2b_store.list_results_raw("oneplus", limit=5000)
        )
        books: dict[tuple[int, str], dict] = {}
        for row in raw:
            result_dir = Path(str(row.get("result_dir") or "")).name
            if not result_dir:
                continue
            jid = int(row.get("postprocess_job_id") or 0)
            books[(jid, result_dir)] = {
                "id": jid, "status": "completed", "result_dir": result_dir,
                "output_filename": row.get("output_filename"),
                "source_filename": row.get("output_filename"),
            }
        return list(books.values())

    async def _telegram_text_audit_candidates(self) -> list[dict]:
        """Build the Telegram Text queue from current Stage 2C ledgers first.

        Verification rows are evidence only. Human-review authority comes from
        the current correction ledger, so reruns cannot hide or resurrect work.
        """
        candidates: list[dict] = []
        for job in await self._telegram_current_audit_books():
            result_dir_name = Path(str(job.get("result_dir") or "")).name
            result_dir = Path(self.config.processed_dir) / result_dir_name
            ledger = await asyncio.to_thread(_load_json_file, result_dir / "correction_ledger.json")
            entries = [
                item for item in (ledger.get("entries") or [])
                if isinstance(item, dict)
                and item.get("entry_type") in {"text_correction", "table_cell_correction"}
                and str(item.get("status") or "") != "superseded"
            ]
            groups: dict[tuple, list[dict]] = {}
            for entry in entries:
                groups.setdefault(_entry_source_identity(entry), []).append(entry)
            for group in groups.values():
                reviewed = [item for item in group if bool(item.get("human_verified"))]
                if reviewed:
                    continue
                entry = max(
                    group,
                    key=lambda item: float(
                        item.get("updated_at_epoch") or item.get("created_at_epoch") or 0
                    ),
                )
                verdict = str(entry.get("verification_verdict") or "").upper()
                if verdict not in {"LIKELY_CORRUPT", "UNCERTAIN"}:
                    continue
                if not bool(getattr(self.config, "stage2c_require_human_review", False)) and str(entry.get("status") or "").lower() not in {"pending", "proposed"}:
                    continue
                row_id = int(entry.get("verification_job_id") or 0)
                row = await self.stage2b_store.get_job(row_id) if row_id else None
                request = _audit_json((row or {}).get("request_json"))
                result = _audit_json((row or {}).get("result_json"))
                reconstruction = result.get("source_reconstruction") if isinstance(result.get("source_reconstruction"), dict) else {}
                correction = result.get("correction") if isinstance(result.get("correction"), dict) else {}
                original = str(entry.get("original_text") or request.get("suspect_text") or "").strip()
                proposed = str(
                    entry.get("proposed_text")
                    or correction.get("proposed_text")
                    or reconstruction.get("corrected_text")
                    or ""
                ).strip()
                if not original:
                    continue
                candidates.append({
                    "row_id": row_id,
                    "result_dir": result_dir_name,
                    "entry_id": str(entry.get("entry_id") or ""),
                    "original": original,
                    "proposed": proposed,
                    "page": entry.get("page") or request.get("page"),
                    "reason": entry.get("status_reason") or entry.get("reason") or (row or {}).get("reason"),
                    "verdict": verdict,
                    "book": job.get("source_filename") or job.get("output_filename") or result_dir_name or "Unknown book",
                    "postprocess_job_id": int(job.get("id") or 0),
                })
        candidates.sort(key=lambda item: (str(item.get("book") or "").casefold(), int(item.get("page") or 0), str(item.get("entry_id") or "")))
        return candidates

    async def _telegram_next_text_audit(self) -> dict:
        candidates = await self._telegram_text_audit_candidates()
        if not candidates:
            return {"done": True, "remaining": 0}
        item = candidates[0]
        row_id = int(item.get("row_id") or 0)
        try:
            image, mime, _ = await self.stage2b_worker.text_audit_image(row_id) if row_id else (b"", "image/png", "")
        except (ValueError, FileNotFoundError, IndexError, KeyError):
            image, mime = b"", "image/png"
        proposed = item["proposed"]
        options = []
        if proposed and proposed != item["original"]:
            options.append({"label": "✅ Apply", "value": "apply"})
        options.append({"label": "🛡 Keep original", "value": "keep_original"})
        caption = (
            "📝 **Text review**\n"
            f"📘 {self._telegram_code(item['book'], 90)} · 📄 page {self._telegram_code(item['page'] or '—', 20)}\n\n"
            "🟡 **Decision needed**\n"
            f"🧭 {self._telegram_code(item['reason'] or item['verdict'] or 'Needs review', 110)}\n\n"
            f"**Original**\n{self._telegram_code(item['original'], 260)}\n"
        )
        if proposed:
            caption += f"\n**Suggested correction**\n{self._telegram_code(proposed, 260)}\n"
        caption += (
            f"\n🔎 Remaining `{len(candidates)}`\n"
            "Choose a decision below — the next review opens automatically."
        )
        return {
            "done": False,
            "remaining": len(candidates),
            "key": {
                "row_id": row_id,
                "postprocess_job_id": int(item.get("postprocess_job_id") or 0),
                "result_dir": item["result_dir"],
                "entry_id": item["entry_id"],
            },
            "image": image,
            "mime_type": mime,
            "caption": caption,
            "options": options,
        }

    @staticmethod
    def _telegram_is_artifact_sweep(row: dict) -> bool:
        return str(row.get("code") or "") == "FULL_TECHNICAL_VISUAL" or bool(re.fullmatch(r"AV\d{6}", str(row.get("route_id") or "")))

    @staticmethod
    def _telegram_visual_requires_human(entry: dict) -> bool:
        if bool(entry.get("human_verified")) or str(entry.get("human_visual_decision") or "") in {"technical", "decorative", "useful", "not_useful"}:
            return False
        verdict = str(entry.get("verification_verdict") or "").upper()
        return verdict == "UNCERTAIN" or bool(entry.get("unresolved")) or str(entry.get("status") or "").lower() == "pending"

    async def _telegram_visual_audit_candidates(self, audit_type: str) -> list[dict]:
        candidates: list[dict] = []
        for job in await self._telegram_current_audit_books():
            result_dir_name = Path(str(job.get("result_dir") or "")).name
            result_dir = Path(self.config.processed_dir) / result_dir_name
            ledger = await asyncio.to_thread(_load_json_file, result_dir / "correction_ledger.json")
            entries = [
                item for item in (ledger.get("entries") or [])
                if isinstance(item, dict)
                and item.get("entry_type") == "vision_enrichment"
                and str(item.get("status") or "") != "superseded"
            ]
            groups: dict[tuple[str, object], list[dict]] = {}
            for entry in entries:
                source_index = entry.get("source_index")
                key = ("source", source_index) if source_index is not None else ("entry", str(entry.get("entry_id") or ""))
                groups.setdefault(key, []).append(entry)
            for group in groups.values():
                if any(
                    bool(item.get("human_verified"))
                    and str(item.get("human_visual_decision") or "") in {"technical", "decorative", "useful", "not_useful"}
                    for item in group
                ):
                    continue
                unresolved = [item for item in group if self._telegram_visual_requires_human(item)]
                if not unresolved:
                    continue
                normal = [item for item in unresolved if not self._telegram_is_artifact_sweep(item)]
                subject_type = "vision" if normal else "artifact"
                if subject_type != audit_type:
                    continue
                pool = normal or unresolved
                entry = max(pool, key=lambda item: float(item.get("updated_at_epoch") or item.get("created_at_epoch") or 0))
                row_id = int(entry.get("verification_job_id") or 0)
                row = await self.stage2b_store.get_job(row_id) if row_id else None
                result = _audit_json((row or {}).get("result_json"))
                parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
                candidates.append({
                    "row_id": row_id,
                    "result_dir": result_dir_name,
                    "entry_id": str(entry.get("entry_id") or ""),
                    "book": job.get("source_filename") or job.get("output_filename") or result_dir_name or "Unknown book",
                    "page": entry.get("page") or _audit_json((row or {}).get("request_json")).get("page"),
                    "verdict": str(entry.get("verification_verdict") or parsed.get("verdict") or "UNCERTAIN").upper(),
                    "confidence": entry.get("confidence") if entry.get("confidence") is not None else parsed.get("confidence"),
                    "category": entry.get("diagram_category") or parsed.get("diagram_category") or "unknown",
                    "summary": entry.get("generated_summary") or parsed.get("summary") or "",
                    "labels": entry.get("visible_text") or parsed.get("visible_text") or [],
                    "reason": entry.get("status_reason") or entry.get("reason") or (row or {}).get("reason") or "",
                    "postprocess_job_id": int(job.get("id") or 0),
                })
        candidates.sort(key=lambda item: (str(item.get("book") or "").casefold(), int(item.get("page") or 0), str(item.get("entry_id") or "")))
        return candidates

    async def _telegram_next_visual_audit(self, audit_type: str) -> dict:
        candidates = await self._telegram_visual_audit_candidates(audit_type)
        if not candidates:
            return {"done": True, "remaining": 0}
        item = candidates[0]
        row_id = int(item.get("row_id") or 0)
        try:
            image, mime, _ = await self.stage2b_worker.vision_audit_image(row_id, "full") if row_id else (b"", "image/png", "")
        except (ValueError, FileNotFoundError, IndexError, KeyError):
            image, mime = b"", "image/png"
        confidence = item["confidence"]
        confidence_text = f"{float(confidence) * 100:.0f}%" if isinstance(confidence, (int, float)) else "—"
        labels = [self._telegram_trim(value, 55) for value in item["labels"][:6] if str(value).strip()]
        title = "🔬 **Artifact review**" if audit_type == "artifact" else "🖼 **Vision review**"
        caption = (
            f"{title}\n"
            f"📘 {self._telegram_code(item['book'], 90)} · 📄 page {self._telegram_code(item['page'] or '—', 20)}\n\n"
            "🟡 **Decision needed**\n"
        )
        if item["summary"]:
            caption += f"{self._telegram_code(item['summary'], 200)}\n\n"
        caption += (
            f"🧭 Category {self._telegram_code(item['category'], 45)} · 🎯 {self._telegram_code(confidence_text, 20)}\n"
            f"ℹ️ {self._telegram_code(item['reason'] or item['verdict'] or 'Needs review', 100)}\n"
        )
        if labels:
            preview = " · ".join(labels[:3])
            extra = max(0, len(item["labels"]) - len(labels[:3]))
            caption += f"🏷 {self._telegram_code(preview, 130)}" + (f" · +{extra} more" if extra else "") + "\n"
        caption += (
            f"\n🔎 Remaining `{len(candidates)}`\n"
            "Choose a decision below — the next image opens automatically."
        )
        options = (
            [{"label": "✅ Technical", "value": "technical"}, {"label": "🚫 Decorative", "value": "decorative"}]
            if audit_type == "artifact"
            else [{"label": "✅ Useful", "value": "useful"}, {"label": "🚫 Not useful", "value": "not_useful"}]
        )
        return {
            "done": False,
            "remaining": len(candidates),
            "key": {
                "row_id": row_id,
                "postprocess_job_id": int(item.get("postprocess_job_id") or 0),
                "result_dir": item["result_dir"],
                "entry_id": item["entry_id"],
            },
            "image": image,
            "mime_type": mime,
            "caption": caption,
            "options": options,
        }

    async def _telegram_apply_text_audit(self, key: dict, decision: str) -> dict:
        if decision not in {"apply", "keep_original"}:
            return {"ok": False, "error": "Unsupported text decision."}
        result_dir = Path(self.config.processed_dir) / Path(str(key.get("result_dir") or "")).name
        entry_id = str(key.get("entry_id") or "")
        async with self.stage2b_worker._stage2c_ledger_lock:
            ledger = await asyncio.to_thread(_load_json_file, result_dir / "correction_ledger.json")
            entry = next((item for item in (ledger.get("entries") or []) if str(item.get("entry_id") or "") == entry_id), None)
            if not isinstance(entry, dict):
                return {"ok": False, "error": "Text audit entry no longer exists."}
            if entry.get("human_verified"):
                return {"ok": True, "message": "Already human reviewed."}
            original = str(entry.get("original_text") or "").strip()
            proposed = str(entry.get("proposed_text") or "").strip()
            if decision == "apply" and not proposed:
                row = await self.stage2b_store.get_job(int(key.get("row_id") or 0))
                result = _audit_json((row or {}).get("result_json"))
                correction = result.get("correction") if isinstance(result.get("correction"), dict) else {}
                reconstruction = result.get("source_reconstruction") if isinstance(result.get("source_reconstruction"), dict) else {}
                proposed = str(correction.get("proposed_text") or reconstruction.get("corrected_text") or "").strip()
            if decision == "apply":
                if not proposed:
                    return {"ok": False, "error": "No verifier correction is available to apply."}
                apply_human_correction_to_entry(entry, text=proposed, action="apply")
                label = "Verifier correction accepted."
            else:
                if not original:
                    return {"ok": False, "error": "Original text is unavailable."}
                apply_human_correction_to_entry(entry, text=original, action="reject")
                label = "Original text kept."
            await asyncio.to_thread(
                upsert_ledger_entry, result_dir, str(ledger.get("source_zip_sha256") or ""), entry
            )
        self.events.notify("stage2c_human_correction")
        return {"ok": True, "message": label}

    async def _telegram_apply_visual_audit(self, key: dict, decision: str) -> dict:
        allowed = {"technical", "decorative", "useful", "not_useful"}
        if decision not in allowed:
            return {"ok": False, "error": "Unsupported visual decision."}
        result_dir = Path(self.config.processed_dir) / Path(str(key.get("result_dir") or "")).name
        entry_id = str(key.get("entry_id") or "")
        try:
            async with self.stage2b_worker._stage2c_ledger_lock:
                entry = await asyncio.to_thread(apply_human_visual_decision, result_dir, entry_id, decision)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        recovery = None
        if bool(entry.get("human_evidence_recovery_required")) and str(entry.get("human_visual_decision") or "") in {"technical", "useful"}:
            verification_job_id = entry.get("verification_job_id") or key.get("row_id")
            if verification_job_id is not None:
                try:
                    recovery = await self.stage2b_worker.start_human_visual_evidence_recovery(
                        int(verification_job_id), str(entry.get("entry_id") or entry_id)
                    )
                except ValueError:
                    recovery = None
        self.events.notify("verifier_audit_decision")
        suffix = " Evidence recovery queued." if isinstance(recovery, dict) and recovery.get("status") in {"queued", "pending", "started"} else ""
        return {"ok": True, "message": f"Decision saved: {decision.replace('_', ' ')}.{suffix}"}

    async def telegram_audit(self, action: str, audit_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Application-owned Telegram human audit workflow.

        Telegram itself is only the transport. Every decision is committed by
        the same Stage 2C functions used by the web audit, preserving human
        authority, ledger reconciliation, downstream staleness and evidence
        recovery semantics.
        """
        kind = str(audit_type or "").strip().lower()
        if kind not in {"text", "vision", "artifact"}:
            return {"done": True, "remaining": 0}
        if action == "next":
            return await (self._telegram_next_text_audit() if kind == "text" else self._telegram_next_visual_audit(kind))
        if action == "decide":
            key = payload.get("key") if isinstance(payload.get("key"), dict) else {}
            decision = str(payload.get("decision") or "").strip().lower()
            if kind == "text":
                return await self._telegram_apply_text_audit(key, decision)
            return await self._telegram_apply_visual_audit(key, decision)
        return {"ok": False, "error": "Unsupported audit action."}

    async def telegram_command(self, command: str, args: list[str]) -> str:
        """Build Markdown source for the Telegram transport.

        The transport converts this source to Telegram entities, so dynamic
        values are kept in code spans and aligned data lives in fenced blocks.
        """

        def header(title: str = "⚓ **Docling Auto-Convert**") -> list[str]:
            # Telegram already timestamps every message. Repeating wall-clock time
            # and build metadata at the top wastes the most valuable mobile space.
            return [title, ""]

        def footer(commands: str = "", *, show_version: bool = False) -> list[str]:
            lines: list[str] = []
            if commands:
                lines += ["", commands]
            if show_version:
                lines.append(f"`v{APP_VERSION}`")
            return lines

        def row(label: str, value: object, width: int = 18) -> str:
            return f"{label:<{width}}{value}"

        def code_block(title: str, rows: list[tuple[str, object]], width: int = 18) -> list[str]:
            body = [title] + [row(label, value, width) for label, value in rows]
            longest = max((len(match.group(0)) for line in body for match in re.finditer(r"`+", str(line))), default=0)
            fence = "`" * max(3, longest + 1)
            return [f"{fence}text", *body, fence, ""]

        def seconds_label(value: object) -> str:
            try:
                seconds = max(0, int(float(value or 0)))
            except (TypeError, ValueError):
                return "—"
            if seconds >= 3600:
                return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
            if seconds >= 60:
                return f"{seconds // 60}m {seconds % 60}s"
            return f"{seconds}s"

        def age_from_epoch(value: object) -> str:
            try:
                epoch = float(value or 0)
            except (TypeError, ValueError):
                return "—"
            return seconds_label(time.time() - epoch) if epoch > 0 else "—"

        def requested_page() -> int:
            if not args:
                return 1
            try:
                return max(1, int(args[0]))
            except (TypeError, ValueError):
                return 1

        start_lines = header("⚓ **Docling Auto-Convert**") + [
            "Monitor the pipeline and clear human-review work from your phone.",
            "",
            "📊 `/status`  Current state",
            "🔎 `/audit`  Human-review backlog",
            "🚨 `/errors`  Problems needing action",
            "⚙️ `/workers`  Pi5 + OnePlus",
            "",
            "Use **Menu** for all commands.",
            *footer("ℹ️ `/help` shows the full command list.", show_version=True),
        ]
        help_lines = header("ℹ️ **Bot commands**") + [
            "📊 **Monitor**",
            "`/status` · `/books` · `/workers` · `/errors`",
            "",
            "🔎 **Human review**",
            "`/audit` · `/textaudit` · `/visionaudit` · `/artifactaudit`",
            "`/stopaudit` stops the current review session.",
            *footer("Use **Menu** instead of typing commands.", show_version=True),
        ]
        if command == "/start":
            return "\n".join(start_lines)
        if command == "/help":
            return "\n".join(help_lines)

        legacy_controls = {
            "/startall", "/retryfailed", "/pause_pi5", "/resume_pi5",
            "/pause_oneplus", "/resume_oneplus",
        }
        if command in legacy_controls:
            return "\n".join(header("ℹ️ **Monitoring only**") + [
                "🔒 Telegram pipeline controls are disabled.",
                "Use the **Docling Auto-Convert** web app for pipeline actions.",
            ])

        books = await self.stage2b_store.list_books()
        jobs = await asyncio.to_thread(enrich_postprocess_jobs, await self.postprocess_store.list_jobs(limit=-1))
        audits: dict[int, dict] = {}
        for job in jobs:
            if job.get("status") != "completed" or not job.get("result_dir"):
                continue
            jid = int(job.get("id") or job.get("job_id") or job.get("postprocess_job_id") or 0)
            audits[jid] = await asyncio.to_thread(
                verifier_audit_summary,
                Path(self.config.processed_dir) / Path(str(job["result_dir"])).name,
                text_require_human=bool(self.config.stage2c_require_human_review),
            )

        verification_failed = sum(
            int(book.get(key) or 0)
            for book in books
            for key in ("text_failed", "vision_failed", "artifact_failed")
        )
        conversion_failed_rows = await self.store.list_jobs(limit=200, failures_only=True)
        stage2a_failed = [job for job in jobs if str(job.get("status") or "") == "failed"]
        stage2c_failed = [job for job in jobs if str(job.get("stage2c_status") or "") == "failed"]
        stage3_failed = [job for job in jobs if str(job.get("stage3_status") or "") == "failed"]

        def audit_review_counts(audit: dict | None) -> dict[str, int]:
            audit = audit or {}
            text = audit.get("text") if isinstance(audit.get("text"), dict) else {}
            text_review = int(text.get("review_required") or 0)
            text_blocking = int(text.get("blocking_review_required") or 0)
            vision = int(audit.get("vision_route_review_required") if audit.get("vision_route_review_required") is not None else audit.get("vision_review_required") or 0)
            artifact = int(audit.get("artifact_review_required") or 0)
            recovery = int(audit.get("vision_evidence_recovery_required") or 0)
            return {
                "text": text_review,
                "vision": vision,
                "artifact": artifact,
                "recovery": recovery,
                "human_total": text_review + vision + artifact,
                "human_blocking": text_blocking + vision + artifact,
                "blocking_total": text_blocking + vision + artifact + recovery,
            }

        review_counts = {jid: audit_review_counts(audit) for jid, audit in audits.items()}
        review_queue_total = sum(item["human_total"] for item in review_counts.values())
        human_blocking_total = sum(item["human_blocking"] for item in review_counts.values())
        recovery_blocking_total = sum(item["recovery"] for item in review_counts.values())
        audit_required = human_blocking_total + recovery_blocking_total
        bypassed = sum(1 for audit in audits.values() if audit.get("bypassed_for_testing"))
        worker_states = self.stage2b_worker.worker_state
        quota = await self.groq_quota.snapshot()
        uses_groq = str(getattr(self.config, "text_verifier_provider", "")) == "groq" or str(getattr(self.config, "vision_verifier_provider", "")) == "groq"
        route_deferred_total = sum(int(job.get("route_deferred") or 0) for job in jobs if job.get("status") == "completed")

        critical_items: list[str] = []
        attention_items: list[str] = []
        if conversion_failed_rows:
            critical_items.append(f"📥 {len(conversion_failed_rows)} conversion failure(s) — `/errors`")
        if stage2a_failed:
            critical_items.append(f"🧭 {len(stage2a_failed)} Stage 2A failure(s) — `/errors`")
        if verification_failed:
            critical_items.append(f"🤖 {verification_failed} verifier failure(s) — `/errors`")
        if stage2c_failed:
            critical_items.append(f"🧾 {len(stage2c_failed)} Stage 2C failure(s) — `/errors`")
        if stage3_failed:
            critical_items.append(f"🧩 {len(stage3_failed)} Stage 3 failure(s) — `/errors`")
        if uses_groq and quota.get("paused"):
            critical_items.append(f"☁️ Cloud verifier quota paused — {self._telegram_code(quota.get('message') or 'quota guard active', 120)}")
        for target, label in (("pi5", "Pi5"), ("oneplus", "OnePlus")):
            circuit = (worker_states.get(target) or {}).get("endpoint_circuit") or {}
            if circuit.get("open"):
                critical_items.append(
                    f"🔌 {label} verifier circuit open — {self._telegram_code(age_from_epoch(circuit.get('opened_at_epoch')), 30)}"
                )
        sequence_error = str(self.pipeline_sequence_state.get("last_error") or "").strip()
        if sequence_error:
            critical_items.append(f"🔗 Pipeline sequence error — {self._telegram_code(sequence_error, 120)}")
        if route_deferred_total:
            attention_items.append(f"🧭 {route_deferred_total} Stage 2A route(s) deferred by the safety ceiling")
        if review_queue_total:
            attention_items.append(f"🔎 {review_queue_total} human decision(s) waiting — `/audit`")
        if recovery_blocking_total:
            attention_items.append(f"🛠 {recovery_blocking_total} accepted visual(s) still need evidence recovery")
        if bypassed:
            attention_items.append(f"🧪 {bypassed} book(s) have testing bypass active")
        oneplus_workload = (worker_states.get("oneplus") or {}).get("workload") or {}
        if oneplus_workload.get("cooldown_active"):
            attention_items.append(
                f"🌡 OnePlus cooling down — {self._telegram_code(seconds_label(oneplus_workload.get('cooldown_remaining_seconds')), 30)} remaining"
            )

        def attention_block() -> list[str]:
            lines: list[str] = []
            if critical_items:
                lines += ["🔴 **Action needed**", *[f"↳ {item}" for item in critical_items], ""]
            if attention_items:
                lines += ["🟡 **Review needed**", *[f"↳ {item}" for item in attention_items], ""]
            if not lines:
                lines += ["🟢 **All clear**", ""]
            return lines

        def worker_status(target: str) -> tuple[str, str]:
            state = worker_states.get(target) or {}
            circuit = state.get("endpoint_circuit") or {}
            workload = state.get("workload") or {}
            paused = bool(getattr(self.config, f"stage2b_{target}_paused", False))
            active = state.get("active_job_id")
            if circuit.get("open"):
                return "🔴", "Circuit open"
            if paused:
                return "🟡", "Paused"
            if target == "oneplus" and workload.get("cooldown_active"):
                return "🟡", f"Cooling · {seconds_label(workload.get('cooldown_remaining_seconds'))}"
            if active:
                return "🔵", f"Working · #{active}"
            return "🟢", "Ready"

        if command == "/status":
            keys = (
                "text_completed", "text_pending", "text_processing", "text_failed",
                "vision_completed", "vision_pending", "vision_processing", "vision_failed",
                "artifact_completed", "artifact_pending", "artifact_processing", "artifact_failed",
            )
            totals = {key: sum(int(book.get(key) or 0) for book in books) for key in keys}
            ready_audit = 0
            still_verifying = 0
            for book in books:
                normal_open = sum(int(book.get(key) or 0) for key in (
                    "text_pending", "text_processing", "text_failed",
                    "vision_pending", "vision_processing", "vision_failed",
                ))
                artifact_open = sum(int(book.get(key) or 0) for key in (
                    "artifact_pending", "artifact_processing", "artifact_failed",
                ))
                jid = int(book.get("postprocess_job_id") or 0)
                counts = review_counts.get(jid) or audit_review_counts(audits.get(jid))
                unresolved = int(counts.get("human_total") or 0)
                if normal_open or artifact_open:
                    still_verifying += 1
                elif unresolved:
                    ready_audit += 1
            artifact_total = sum(totals[key] for key in ("artifact_completed", "artifact_pending", "artifact_processing", "artifact_failed"))
            text_total = sum(totals[key] for key in ("text_completed", "text_pending", "text_processing", "text_failed"))
            vision_total = sum(totals[key] for key in ("vision_completed", "vision_pending", "vision_processing", "vision_failed"))
            route_created = sum(int(job.get("route_count") or 0) for job in jobs if job.get("status") == "completed")
            route_candidates = sum(int(job.get("route_candidates_detected") or job.get("route_count") or 0) for job in jobs if job.get("status") == "completed")
            failure_total = len(conversion_failed_rows) + len(stage2a_failed) + verification_failed + len(stage2c_failed) + len(stage3_failed)
            lines = header("📊 **Status**") + attention_block()
            lines += [
                f"📚 `{len(books)}` books  ·  🔄 `{still_verifying}` active  ·  🔎 `{review_queue_total}` review  ·  🔴 `{failure_total}` failed",
                "",
                f"🧭 2A `{route_created}/{route_candidates}`" + (f" · ⚠️ `{route_deferred_total}` deferred" if route_deferred_total else ""),
                f"📝 Text `{totals['text_completed']}/{text_total}`  ·  🖼 Vision `{totals['vision_completed']}/{vision_total}`",
                f"🔬 Artifacts `{totals['artifact_completed']}/{artifact_total}`",
                "",
                "**Workers**",
            ]
            for target, label, icon in (("pi5", "Pi5", "🧠"), ("oneplus", "OnePlus", "📱")):
                state_icon, state_text = worker_status(target)
                lines.append(f"{icon} {label}  {state_icon} **{state_text}**")
            if ready_audit:
                lines += ["", f"🟡 `{ready_audit}` book(s) are ready for human review."]
            lines += ["", *footer("📚 `/books` · 🔎 `/audit` · 🚨 `/errors`")]
            return "\n".join(lines).rstrip()

        if command == "/books":
            entries: list[dict] = []
            for book in books:
                name = str(book.get("output_filename") or book.get("result_dir") or book.get("postprocess_job_id"))
                name = name[:-4] if name.lower().endswith(".zip") else name
                jid = int(book.get("postprocess_job_id") or 0)
                audit = audits.get(jid) or {}
                counts = review_counts.get(jid) or audit_review_counts(audit)
                unresolved = int(counts.get("human_total") or 0)
                recovery = int(counts.get("recovery") or 0)
                pending = sum(int(book.get(key) or 0) for key in ("text_pending", "text_processing", "vision_pending", "vision_processing", "artifact_pending", "artifact_processing"))
                failed = sum(int(book.get(key) or 0) for key in ("text_failed", "vision_failed", "artifact_failed"))
                post_job = next((job for job in jobs if int(job.get("id") or 0) == jid), {})
                failed += int(str(post_job.get("status") or "") == "failed")
                failed += int(str(post_job.get("stage2c_status") or "") == "failed")
                failed += int(str(post_job.get("stage3_status") or "") == "failed")
                if failed:
                    rank, state, icon = 0, "Failed", "🔴"
                elif unresolved or recovery or audit.get("bypassed_for_testing"):
                    if audit.get("bypassed_for_testing"):
                        state = "Audit bypassed"
                    elif unresolved:
                        state = "Human review"
                    else:
                        state = "Evidence recovery"
                    rank, icon = 1, "🟡"
                elif pending:
                    rank, state, icon = 2, "Verifying", "🔵"
                else:
                    rank, state, icon = 3, "Complete", "🟢"
                entries.append({"rank": rank, "name": name, "jid": jid, "audit": audit, "unresolved": unresolved, "recovery": recovery, "post_job": post_job, "state": state, "icon": icon, "book": book})
            entries.sort(key=lambda item: (item["rank"], str(item["name"]).casefold()))
            per_page = 8
            max_page = max(1, (len(entries) + per_page - 1) // per_page)
            page = min(requested_page(), max_page)
            start = (page - 1) * per_page
            shown_entries = entries[start:start + per_page]
            lines = header("📚 **Books**") + [f"Page `{page}/{max_page}` · problems first", ""]
            for item in shown_entries:
                book = item["book"]
                post_job = item["post_job"]
                route_created = int(post_job.get("route_count") or 0)
                route_candidates = int(post_job.get("route_candidates_detected") or route_created)
                art_done = int(book.get("artifact_completed") or 0)
                art_total = art_done + sum(int(book.get(key) or 0) for key in ("artifact_pending", "artifact_processing", "artifact_failed"))
                deferred = int(post_job.get("route_deferred") or 0)
                lines += [
                    f"{item['icon']} {self._telegram_code(item['name'], 120)}",
                    f"**{item['state']}** · 🧭 `{route_created}/{route_candidates}` · 🔬 `{art_done}/{art_total}` · 🔎 `{item['unresolved']}`" + (f" · 🛠 `{item['recovery']}`" if item.get("recovery") else "") + (f" · ⚠️ `{deferred}` deferred" if deferred else ""),
                    "",
                ]
            remaining = max(0, len(entries) - (start + len(shown_entries)))
            if remaining:
                lines += [f"➡️ `{remaining}` more · `/books {page + 1}`", ""]
            if not entries:
                lines += ["🟢 No books are currently registered.", ""]
            lines += footer("🔎 `/audit` human review · 📊 `/status` overview")
            return "\n".join(lines).rstrip()

        if command == "/workers":
            lines = header("⚙️ **Worker health**") + attention_block()
            oneplus_control = await self.oneplus_controller.status()
            for target, label, icon in (("pi5", "Pi5", "🧠"), ("oneplus", "OnePlus", "📱")):
                state = worker_states.get(target) or {}
                circuit = state.get("endpoint_circuit") or {}
                workload = state.get("workload") or {}
                active = state.get("active_job_id")
                task = str(state.get("active_label") or state.get("active_task") or ("Artifact sweep" if active else "Idle"))
                state_icon, state_text = worker_status(target)
                lines += [f"{icon} **{label}**  {state_icon} {state_text}"]
                if active:
                    lines.append(f"└ Job `#{active}` · {self._telegram_code(task, 70)}")
                else:
                    lines.append(f"└ {self._telegram_code(task, 70)}")
                if circuit.get("open") or int(circuit.get("failure_count") or 0):
                    lines.append(f"🔌 Circuit: **{'Open' if circuit.get('open') else 'Closed'}** · failures `{int(circuit.get('failure_count') or 0)}`")
                if circuit.get("open"):
                    lines.append(f"⏱ Outage: `{age_from_epoch(circuit.get('opened_at_epoch'))}`")
                if circuit.get("last_error"):
                    lines.append(f"⚠️ {self._telegram_code(self._telegram_trim(circuit.get('last_error'), 110), 110)}")
                if target == "oneplus":
                    speed = f"{float(workload.get('last_speed_tps')):.2f} tok/s" if workload.get("last_speed_tps") is not None else "—"
                    lines.append(f"⚡ `{speed}` · work `{float(workload.get('busy_minutes') or 0):.1f}/{float(workload.get('budget_minutes') or 90):.0f} min`")
                    ssh = oneplus_control.get("ssh") or {}
                    llama = oneplus_control.get("llama") or {}
                    ssh_icon = "🟢" if ssh.get("reachable") else "🔴"
                    llama_icon = "🟢" if llama.get("running") else "🔴"
                    lines.append(f"🔌 SSH {ssh_icon} · llama.cpp {llama_icon}")
                    if ssh.get("error"):
                        lines.append(f"⚠️ SSH: {self._telegram_code(self._telegram_trim(ssh.get('error'), 100), 100)}")
                lines.append("")
            lines += footer("🚨 `/errors` details · 📊 `/status` overview")
            return "\n".join(lines).rstrip()

        if command == "/audit":
            entries: list[dict] = []
            for job in jobs:
                jid = int(job.get("id") or job.get("job_id") or job.get("postprocess_job_id") or 0)
                audit = audits.get(jid)
                if not audit:
                    continue
                counts = review_counts.get(jid) or audit_review_counts(audit)
                if not counts["human_total"] and not counts["recovery"] and not audit.get("bypassed_for_testing"):
                    continue
                name = str(job.get("output_filename") or job.get("source_filename") or job.get("result_dir") or jid)
                name = name[:-4] if name.lower().endswith(".zip") else name
                entries.append({
                    "rank": 0 if counts["human_total"] else 1,
                    "name": name,
                    **counts,
                    "bypassed": bool(audit.get("bypassed_for_testing")),
                })
            entries.sort(key=lambda item: (item["rank"], -(item["human_total"] + item["recovery"]), str(item["name"]).casefold()))
            per_page = 10
            max_page = max(1, (len(entries) + per_page - 1) // per_page)
            page = min(requested_page(), max_page)
            start = (page - 1) * per_page
            shown_entries = entries[start:start + per_page]
            lines = header("🔎 **Human review**") + [f"Page `{page}/{max_page}` · largest backlog first", ""]
            if not shown_entries:
                lines += ["🟢 **No unresolved human-review items.**", ""]
            for item in shown_entries:
                icon = "🟡" if item["human_total"] else "🛠"
                lines += [
                    f"{icon} {self._telegram_code(item['name'], 120)}",
                    f"📝 Text `{item['text']}` · 🖼 Vision `{item['vision']}` · 🔬 Artifact `{item['artifact']}`"
                    + (f" · 🛠 Recovery `{item['recovery']}`" if item["recovery"] else "")
                    + (" · 🧪 bypass" if item["bypassed"] else ""),
                    "",
                ]
            remaining = max(0, len(entries) - (start + len(shown_entries)))
            if remaining:
                lines += [f"➡️ `{remaining}` more · `/audit {page + 1}`", ""]
            lines += footer("📝 `/textaudit` · 🖼 `/visionaudit` · 🔬 `/artifactaudit`")
            return "\n".join(lines).rstrip()

        if command == "/errors":
            lines = header("🚨 **Errors & blockers**") + attention_block()
            failures = [
                ("📥", "Conversion", len(conversion_failed_rows)),
                ("🧭", "Stage 2A", len(stage2a_failed)),
                ("🤖", "Verification", verification_failed),
                ("🧾", "Stage 2C", len(stage2c_failed)),
                ("🧩", "Stage 3", len(stage3_failed)),
                ("🔎", "Human decisions", human_blocking_total),
                ("🛠", "Evidence recovery", recovery_blocking_total),
            ]
            nonzero = [(icon, label, count) for icon, label, count in failures if count]
            if nonzero:
                lines += ["**Current blockers**"]
                lines += [f"{icon} {label}: `{count}`" for icon, label, count in nonzero]
                lines.append("")
            else:
                lines += ["🟢 No pipeline failures or audit blockers are currently recorded.", ""]
            for target, label, icon in (("pi5", "Pi5", "🧠"), ("oneplus", "OnePlus", "📱")):
                circuit = (worker_states.get(target) or {}).get("endpoint_circuit") or {}
                if not circuit.get("open") and not int(circuit.get("failure_count") or 0) and not circuit.get("last_error"):
                    continue
                lines += [f"{icon} **{label} verifier**"]
                lines.append(f"🔌 Circuit **{'Open' if circuit.get('open') else 'Closed'}** · failures `{int(circuit.get('failure_count') or 0)}`")
                if circuit.get("open"):
                    lines.append(f"⏱ Outage `{age_from_epoch(circuit.get('opened_at_epoch'))}`")
                if circuit.get("last_error"):
                    lines.append(f"⚠️ {self._telegram_code(self._telegram_trim(circuit.get('last_error'), 120), 120)}")
                lines.append("")
            lines += footer("🌐 Use the web **Errors & diagnostics** page for row-level retry actions.")
            return "\n".join(lines).rstrip()


        return "\n".join(help_lines)

    async def stop_safety_refresh(self) -> None:
        if self.safety_refresh_task and not self.safety_refresh_task.done():
            self.safety_refresh_task.cancel()
            await asyncio.gather(self.safety_refresh_task, return_exceptions=True)


runtime = Runtime()
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.worker.start()
    await runtime.postprocess_worker.start()
    await runtime.stage2b_worker.start()
    await runtime.start_pipeline_sequence()
    await runtime.telegram_bot.start()
    yield
    await runtime.telegram_bot.stop()
    await runtime.stop_pipeline_sequence()
    await runtime.stop_safety_refresh()
    await runtime.stage3_builder.stop()
    await runtime.stage2b_worker.stop()
    await runtime.postprocess_worker.stop()
    await runtime.worker.stop()


app = FastAPI(title="Docling Auto-Convert", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)

# Managed Add-book ingestion writes into the same /input directory used by the
# watcher. Serializing filename selection prevents two browser uploads from
# racing to claim the same collision-safe destination.
_add_book_lock = asyncio.Lock()
_generation_jobs_lock = asyncio.Lock()
_generation_jobs: dict[str, dict] = {}


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_source_filename(value: str, content_type: str | None = None) -> str:
    name = Path(unquote(str(value or "").replace("\\", "/"))).name.strip().replace("\x00", "")
    if not name:
        name = "document.pdf" if str(content_type or "").lower().startswith("application/pdf") else "document.bin"
    # Keep names practical for SMB/Linux/Windows while preserving useful manual titles.
    if len(name) > 220:
        suffix = Path(name).suffix
        name = f"{Path(name).stem[: max(1, 220-len(suffix))]}{suffix}"
    return name


def _validate_supported_source_name(name: str) -> None:
    supported = {str(item).lower() for item in runtime.config.supported_extensions}
    if Path(name).suffix.lower() not in supported:
        allowed = ", ".join(sorted(supported))
        raise ValueError(f"Unsupported document type. Allowed extensions: {allowed}")


def _choose_input_destination(input_dir: Path, requested_name: str, incoming_sha256: str) -> tuple[Path, bool]:
    requested_name = _safe_source_filename(requested_name)
    _validate_supported_source_name(requested_name)
    candidate = input_dir / requested_name
    if candidate.is_file():
        try:
            if _sha256_path(candidate) == incoming_sha256:
                return candidate, True
        except OSError:
            pass
    if not candidate.exists():
        return candidate, False
    stem, suffix = Path(requested_name).stem, Path(requested_name).suffix
    for index in range(2, 10000):
        candidate = input_dir / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate, False
    raise OSError("Could not allocate a unique filename in the input folder")


async def _register_managed_input(temp_path: Path, requested_name: str, incoming_sha256: str) -> dict:
    input_dir = Path(runtime.config.input_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    async with _add_book_lock:
        destination, identical_existing = await asyncio.to_thread(
            _choose_input_destination, input_dir, requested_name, incoming_sha256
        )
        if identical_existing:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            await asyncio.to_thread(temp_path.replace, destination)
        stat = await asyncio.to_thread(destination.stat)
        job_id, created = await runtime.store.create_pending_once(
            destination.name,
            list(runtime.config.to_formats),
            source_size=int(stat.st_size),
            source_mtime_ns=int(stat.st_mtime_ns),
            source_sha256=incoming_sha256,
        )
    runtime.events.notify("file_discovered")
    watcher = await runtime.worker.control_status()
    return {
        "accepted": True,
        "job_id": int(job_id),
        "created": bool(created),
        "filename": destination.name,
        "duplicate": bool(identical_existing or not created),
        "auto_run": bool(runtime.config.watcher_auto_run),
        "watcher": watcher,
        "next_action": "automatic" if runtime.config.watcher_auto_run else "start_queue",
    }
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

@app.get("/review")
async def review_page():
    return FileResponse(STATIC_DIR / "review.html")


def enrich_jobs(rows: list[dict]) -> list[dict]:
    output_dir = Path(runtime.config.output_dir)
    for row in rows:
        filename = row.get("output_filename")
        row["output_available"] = bool(filename and (output_dir / filename).is_file())
        raw_formats = row.get("output_formats")
        if isinstance(raw_formats, str):
            try:
                parsed = json.loads(raw_formats)
            except json.JSONDecodeError:
                parsed = []
        elif isinstance(raw_formats, list):
            parsed = raw_formats
        else:
            parsed = []
        if not parsed:
            parsed = [row.get("output_format") or "md"]
        row["output_formats"] = parsed

        row["long_running"] = False
        row["elapsed_seconds"] = row.get("processing_seconds")
        if row.get("status") == "processing" and row.get("submitted_at"):
            try:
                submitted = datetime.fromisoformat(str(row["submitted_at"]))
                if submitted.tzinfo is None:
                    submitted = submitted.replace(tzinfo=UTC)
                elapsed = max(0.0, (datetime.now(UTC) - submitted).total_seconds())
                row["elapsed_seconds"] = elapsed
                row["long_running"] = elapsed >= runtime.config.document_timeout_minutes * 60
            except (TypeError, ValueError):
                pass
    return rows


@lru_cache(maxsize=128)
def _result_counts(path: str, modified: int, size: int) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = [e for e in payload.get("entries", []) if e.get("status") != "superseded"]
    return {"pending": sum(e.get("status") in {"pending", "proposed"} for e in entries),
            "applied": sum(e.get("status") == "applied" for e in entries)}


def enrich_postprocess_jobs(rows: list[dict]) -> list[dict]:
    """Add human-facing quality labels without changing machine status fields."""
    processed_dir = Path(runtime.config.processed_dir)
    for row in rows:
        row["quality_status"] = None
        row["quality_display_label"] = None
        row["integrity_status"] = None
        row["integrity_display_label"] = None
        row["ledger_available"] = False
        row["overlays_available"] = False
        row["stage2c_status"] = "not_built"
        row["stage2c_progress"] = None
        row["chunks_available"] = False
        row["stage3_status"] = "not_built"
        row["stage3_progress"] = None
        row["text_verifier_label"] = _verifier_provider_label(runtime.config.text_verifier_provider, "text")
        row["human_review_mandatory"] = bool(runtime.config.stage2c_require_human_review)
        row["stage2c_auto_finalize"] = bool(runtime.config.stage2c_auto_finalize_after_stage2b)
        result_dir = row.get("result_dir")
        if row.get("status") != "completed" or not result_dir:
            continue
        result_path = processed_dir / Path(result_dir).name
        row["ledger_available"] = (result_path / "correction_ledger.json").is_file()
        row["overlays_available"] = (result_path / "chunk_overlays.jsonl").is_file()
        row["chunks_available"] = (result_path / "chunks.jsonl").is_file()
        try:
            ledger_path = result_path / "correction_ledger.json"
            stat = ledger_path.stat()
            row["result_counts"] = _result_counts(str(ledger_path), stat.st_mtime_ns, stat.st_size)
        except (OSError, ValueError, TypeError, AttributeError):
            row["result_counts"] = None
        review = human_review_summary(result_path, require_human=bool(runtime.config.stage2c_require_human_review))
        row.update(review)
        state = runtime.stage2b_worker.stage2c_state_for(int(row.get("id") or 0))
        if state:
            row["stage2c_status"] = state.get("status") or "not_built"
            row["stage2c_progress"] = state
        else:
            stage2c_path = result_path / "stage2c_backfill.json"
            if stage2c_path.is_file():
                try:
                    persisted_stage2c = json.loads(stage2c_path.read_text(encoding="utf-8"))
                    row["stage2c_status"] = persisted_stage2c.get("status") or "not_built"
                    row["stage2c_progress"] = persisted_stage2c
                except (OSError, json.JSONDecodeError, TypeError):
                    row["stage2c_status"] = "not_built"
        stage3_state = runtime.stage3_builder.state_for(int(row.get("id") or 0))
        if stage3_state:
            row["stage3_status"] = stage3_state.get("status") or "not_built"
            row["stage3_progress"] = stage3_state
        elif row["chunks_available"]:
            row["stage3_status"] = "ready"
        summary_path = result_path / "summary.json"
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        coverage = summary.get("coverage") or {}
        integrity = summary.get("integrity") or {}
        route_summary = summary.get("routes") or {}
        row["quality_status"] = coverage.get("status")
        row["quality_display_label"] = coverage.get("display_label")
        row["integrity_status"] = integrity.get("status")
        row["integrity_display_label"] = integrity.get("display_label")
        row["route_candidates_detected"] = int(route_summary.get("total_candidates_detected") or row.get("route_count") or 0)
        row["route_deferred"] = int(route_summary.get("deferred") or 0)
        row["route_safety_valve_triggered"] = bool(route_summary.get("safety_valve_triggered") or row["route_deferred"])
    return rows


def attach_stage2(rows: list[dict], postprocess_rows: list[dict]) -> list[dict]:
    by_conversion = {row.get("conversion_job_id"): row for row in postprocess_rows}
    for row in rows:
        stage2 = by_conversion.get(row.get("id"))
        row["stage2_job_id"] = stage2.get("id") if stage2 else None
        row["stage2_status"] = stage2.get("status") if stage2 else None
    return rows


@app.get("/api/version")
async def version():
    return {"version": APP_VERSION}


@app.middleware("http")
async def refresh_frontend(request, call_next):
    response = await call_next(request)
    if "text/html" in response.headers.get("content-type", "") or request.url.path == "/api/version":
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/status")
async def status() -> dict:
    jobs = enrich_jobs(await runtime.store.list_jobs(limit=50))
    postprocess_rows = await runtime.postprocess_store.list_jobs(limit=500)
    attach_stage2(jobs, postprocess_rows)
    counts = await runtime.store.counts()
    watcher = await runtime.worker.control_status()
    return {
        "counts": counts,
        "jobs": jobs,
        "docling": runtime.worker.health_status,
        "watcher": watcher,
        "settings": runtime.config.public_settings(),
        "pipeline_sequence": dict(runtime.pipeline_sequence_state),
    }


@app.post("/api/watcher/start")
async def start_watcher_batch() -> dict:
    result = await runtime.worker.start_batch()
    if result.get("reason") == "auto_run_enabled":
        raise HTTPException(status_code=409, detail="Auto Run is enabled. Turn it off to start a manual batch.")
    return result


@app.put("/api/watcher/auto-run")
async def set_watcher_auto_run(update: WatcherAutoRunUpdate) -> dict:
    revised = await runtime.set_watcher_auto_run(update.enabled)
    return {
        "enabled": revised.watcher_auto_run,
        "watcher": await runtime.worker.control_status(),
    }


def _audit_diagnostic_row(result_dir: Path) -> dict:
    try:
        return verifier_audit_summary(result_dir, text_require_human=bool(runtime.config.stage2c_require_human_review))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"review_required": 0, "vision_review_required": 0, "vision_evidence_recovery_required": 0, "gate_status": "unknown"}


@app.get("/api/errors")
async def errors() -> dict:
    conversion_jobs = enrich_jobs(await runtime.store.list_jobs(limit=200, failures_only=True))
    raw_postprocess = await runtime.postprocess_store.list_jobs(limit=500)
    postprocess_rows = await asyncio.to_thread(enrich_postprocess_jobs, raw_postprocess)
    current_verification = await runtime.stage2b_store.list_jobs(limit=10000, current_only=True)
    verification_failed = [row for row in current_verification if str(row.get("status") or "") == "failed"]
    stage2a_failed = [row for row in postprocess_rows if str(row.get("status") or "") == "failed"]
    stage2c_failed = [row for row in postprocess_rows if str(row.get("stage2c_status") or "") == "failed"]
    stage3_failed = [row for row in postprocess_rows if str(row.get("stage3_status") or "") == "failed"]

    audit_rows: list[dict] = []
    for row in postprocess_rows:
        if row.get("status") != "completed" or not row.get("result_dir"):
            continue
        result_dir = Path(runtime.config.processed_dir) / Path(str(row["result_dir"])).name
        audit = await asyncio.to_thread(_audit_diagnostic_row, result_dir)
        if int(audit.get("review_required") or 0) > 0:
            audit_rows.append({
                "postprocess_job_id": int(row.get("id") or 0),
                "book": row.get("source_filename") or row.get("output_filename"),
                "review_required": int(audit.get("review_required") or 0),
                "vision_review_required": int(audit.get("vision_review_required") or 0),
                "evidence_recovery_required": int(audit.get("vision_evidence_recovery_required") or 0),
                "gate_status": audit.get("gate_status"),
            })

    workers = runtime.stage2b_worker.worker_state
    circuits = {
        name: dict((workers.get(name) or {}).get("endpoint_circuit") or {})
        for name in ("pi5", "oneplus")
    }
    summary = {
        "conversion_failed": len(conversion_jobs),
        "stage2a_failed": len(stage2a_failed),
        "verification_failed": len(verification_failed),
        "stage2c_failed": len(stage2c_failed),
        "stage3_failed": len(stage3_failed),
        "audit_review_required": sum(int(row.get("review_required") or 0) for row in audit_rows),
        "books_needing_audit": len(audit_rows),
        "open_verifier_circuits": sum(1 for state in circuits.values() if state.get("open")),
    }
    return {
        "jobs": conversion_jobs,
        "summary": summary,
        "stage2a_failed": stage2a_failed,
        "verification_failed": verification_failed[:200],
        "stage2c_failed": stage2c_failed,
        "stage3_failed": stage3_failed,
        "audit": audit_rows,
        "circuits": circuits,
    }


@app.get("/api/settings")
async def settings() -> dict:
    return runtime.config.public_settings()


@app.put("/api/settings")
async def update_settings(update: SettingsUpdate) -> dict:
    try:
        return (await runtime.update_settings(update)).public_settings()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: int) -> dict:
    if not await runtime.store.retry(job_id):
        raise HTTPException(status_code=404, detail="A failed job with this identifier was not found.")
    runtime.events.notify("job_retried")
    return {"accepted": True}


@app.get("/api/outputs/{filename}")
async def download_output(filename: str):
    safe_filename = Path(filename).name
    path = Path(runtime.config.output_dir) / safe_filename
    if safe_filename != filename or not path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found.")
    return FileResponse(path, filename=safe_filename)


@app.post("/api/books/add/file")
async def add_book_file(file: UploadFile = File(...)) -> dict:
    input_dir = Path(runtime.config.input_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    requested_name = _safe_source_filename(file.filename or "document", file.content_type)
    try:
        _validate_supported_source_name(requested_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    temp_path = input_dir / f"._incoming_{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    total = 0
    max_bytes = 1024 * 1024 * 1024  # 1 GiB safety ceiling; ordinary manuals are far smaller.
    try:
        with temp_path.open("wb") as handle:
            while True:
                chunk = await file.read(4 * 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail="Document is larger than the 1 GiB managed-upload safety limit.")
                digest.update(chunk)
                handle.write(chunk)
        if total <= 0:
            raise HTTPException(status_code=422, detail="The uploaded file was empty.")
        return await _register_managed_input(temp_path, requested_name, digest.hexdigest())
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _url_download_name(url: str, content_disposition: str | None, content_type: str | None) -> str:
    header = str(content_disposition or "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", header, re.I)
    if not match:
        match = re.search(r'filename="?([^";]+)', header, re.I)
    if match:
        return _safe_source_filename(match.group(1), content_type)
    path_name = Path(unquote(urlparse(url).path)).name
    if path_name and Path(path_name).suffix:
        return _safe_source_filename(path_name, content_type)
    if str(content_type or "").lower().startswith("application/pdf"):
        return "document.pdf"
    return _safe_source_filename(path_name or "document.bin", content_type)


async def _download_book_url_to_temp(url: str) -> tuple[Path, str, str]:
    input_dir = Path(runtime.config.input_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    temp_path = input_dir / f"._incoming_{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    total = 0
    max_bytes = 1024 * 1024 * 1024
    timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            async with client.stream("GET", url, headers={"User-Agent": "Docling-Auto-Convert/managed-ingest"}) as response:
                response.raise_for_status()
                name = _url_download_name(
                    str(response.url), response.headers.get("content-disposition"), response.headers.get("content-type")
                )
                _validate_supported_source_name(name)
                with temp_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes(4 * 1024 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("Remote document is larger than the 1 GiB managed-download safety limit.")
                        digest.update(chunk)
                        handle.write(chunk)
        if total <= 0:
            raise ValueError("The remote document was empty.")
        return temp_path, name, digest.hexdigest()
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


@app.post("/api/books/add/url")
async def add_book_url(payload: AddBookUrlRequest) -> dict:
    try:
        temp_path, name, sha256 = await _download_book_url_to_temp(payload.url)
        return await _register_managed_input(temp_path, name, sha256)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Remote server returned HTTP {exc.response.status_code} while downloading the document.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not download the document URL: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not store the document in the managed input folder: {exc}") from exc


@app.post("/api/convert/file")
async def convert_file(file: UploadFile = File(...), options: str = Form(...)) -> dict:
    try:
        parsed_options = ManualConvertOptions.model_validate_json(options)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="The uploaded file was empty.")
    try:
        task_id = await runtime.client.submit_manual_file(file.filename or "document", content, parsed_options)
    except DoclingApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"task_id": task_id}


@app.post("/api/convert/url")
async def convert_url(payload: ConvertUrlRequest) -> dict:
    try:
        task_id = await runtime.client.submit_manual_url(payload.url, payload.options)
    except DoclingApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"task_id": task_id}


@app.get("/api/convert/status/{task_id}")
async def convert_status(task_id: str) -> dict:
    try:
        return await runtime.client.poll(task_id)
    except DoclingApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/convert/result/{task_id}")
async def convert_result(task_id: str, filename: str | None = None) -> Response:
    try:
        payload = await runtime.client.result(task_id)
    except DoclingApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    download_name = "converted_docs.zip"
    if filename:
        # Use the original document's name (as the browser sent it before
        # upload) instead of the generic default, so the manual Convert
        # page's download matches the source file like the auto pipeline
        # already does. The manual endpoint always bundles a zip.
        stem = Path(filename).stem.strip() or "converted_docs"
        download_name = f"{stem}.zip"
    return Response(content=payload.content, media_type=payload.content_type, headers={"Content-Disposition": f'attachment; filename="{download_name}"'})


@app.get("/api/postprocess/status")
async def postprocess_status() -> dict:
    return {
        "enabled": runtime.config.postprocess_enabled,
        "counts": await runtime.postprocess_store.counts(),
        "jobs": await asyncio.to_thread(enrich_postprocess_jobs, await runtime.postprocess_store.list_jobs(limit=50)),
        "processed_dir": runtime.config.processed_dir,
        "external_verifiers_enabled": runtime.config.external_verifiers_enabled,
        "verifiers": runtime.postprocess_worker.verifier_status,
    }


@app.get("/api/documents")
async def documents() -> dict:
    """Unified document library with strict sequential-pipeline readiness."""
    rows = await asyncio.to_thread(enrich_postprocess_jobs, await runtime.postprocess_store.list_jobs(limit=500))
    verification_books = {
        int(item["postprocess_job_id"]): item
        for item in await runtime.stage2b_store.list_books()
    }
    registry = await asyncio.to_thread(load_registry, Path(runtime.config.processed_dir))
    owner_by_job: dict[int, dict] = {}
    for equipment in registry.get("equipment") or []:
        for manual in equipment.get("manuals") or []:
            try:
                owner_by_job[int(manual.get("postprocess_job_id") or 0)] = equipment
            except (TypeError, ValueError):
                continue
    for row in rows:
        job_id = int(row.get("id") or 0)
        book = verification_books.get(job_id, {})
        verification = {
            "pi5_completed": int(book.get("pi5_completed") or 0),
            "pi5_pending": int(book.get("pi5_pending") or 0),
            "pi5_processing": int(book.get("pi5_processing") or 0),
            "pi5_failed": int(book.get("pi5_failed") or 0),
            "oneplus_completed": int(book.get("oneplus_completed") or 0),
            "oneplus_pending": int(book.get("oneplus_pending") or 0),
            "oneplus_processing": int(book.get("oneplus_processing") or 0),
            "oneplus_failed": int(book.get("oneplus_failed") or 0),
            "total": int(book.get("total") or 0),
        }
        row["verification"] = verification
        pipeline = {
            "stage2a_ready": row.get("status") == "completed",
            "stage2b_ready": False,
            "stage2c_ready": False,
            "stage3_ready": False,
            "machine_assigned": False,
            "machine_id": None,
            "machine_name": None,
            "next_stage": "stage2a",
            "blocked_reason": None,
        }
        if row.get("status") == "completed" and row.get("result_dir"):
            total = verification["total"]
            blockers = sum(verification[key] for key in (
                "pi5_pending", "pi5_processing", "pi5_failed",
                "oneplus_pending", "oneplus_processing", "oneplus_failed",
            ))
            pipeline["stage2b_ready"] = bool(total and blockers == 0 and (verification["pi5_completed"] + verification["oneplus_completed"] == total))
            result_dir = Path(runtime.config.processed_dir) / Path(str(row.get("result_dir"))).name
            verification_rows = await runtime.stage2b_store.list_book_jobs_raw(job_id) if total else []
            s2c = stage2c_freshness(result_dir, verification_rows, rule_version=STAGE2C_RULE_VERSION, artifact_sweep_required=bool(getattr(runtime.config, "stage2b_artifact_sweep_required_for_finalize", True)))
            s3 = stage3_freshness(result_dir, s2c, stage3_rule_version=STAGE3_RULE_VERSION, retrieval_rule_version=RETRIEVAL_RULE_VERSION)
            pipeline["stage2c_ready"] = bool(s2c.get("ready"))
            pipeline["stage3_ready"] = bool(s3.get("ready"))
            row["stage2c_status"] = s2c.get("status") or row.get("stage2c_status")
            row["stage3_status"] = s3.get("status") or row.get("stage3_status")
            row["chunks_available"] = bool(s3.get("ready"))
            if not pipeline["stage2b_ready"]:
                pipeline["next_stage"] = "stage2b"
                if verification["pi5_failed"] + verification["oneplus_failed"]:
                    pipeline["blocked_reason"] = "Verification has failed routes; retry them before finalization."
                elif total == 0:
                    pipeline["blocked_reason"] = "Verification has not been prepared for this book yet."
                else:
                    pipeline["blocked_reason"] = "Verification must finish before Stage 2C."
            elif not pipeline["stage2c_ready"]:
                pipeline["next_stage"] = "stage2c"
                pipeline["blocked_reason"] = s2c.get("reason")
            elif not pipeline["stage3_ready"]:
                pipeline["next_stage"] = "stage3"
                pipeline["blocked_reason"] = s3.get("reason")
            else:
                owner = owner_by_job.get(job_id)
                if owner:
                    pipeline["machine_assigned"] = True
                    pipeline["machine_id"] = owner.get("equipment_id")
                    pipeline["machine_name"] = owner.get("name")
                    pipeline["next_stage"] = "machine_embedding"
                else:
                    pipeline["next_stage"] = "assign_machine"
                    pipeline["blocked_reason"] = "Assign this manual to its physical machine before hybrid retrieval."
        row["pipeline"] = pipeline

    # Final machine-level readiness is evaluated only after every book has its
    # current Stage 3 state.  A machine embedding is one persisted corpus made
    # from all assigned manuals, so one missing/stale manual keeps the whole
    # machine downstream stage blocked instead of silently searching a subset.
    row_by_job = {int(row.get("id") or 0): row for row in rows if int(row.get("id") or 0) > 0}
    equipment_embedding_status: dict[str, dict] = {}
    for equipment in registry.get("equipment") or []:
        equipment_id = str(equipment.get("equipment_id") or "").strip()
        if not equipment_id:
            continue
        manual_items = list(equipment.get("manuals") or [])
        index_paths: list[Path] = []
        manual_types: dict[int, str] = {}
        complete = bool(manual_items)
        for manual in manual_items:
            try:
                manual_job_id = int(manual.get("postprocess_job_id") or 0)
            except (TypeError, ValueError):
                complete = False
                continue
            manual_types[manual_job_id] = str(manual.get("manual_type") or "other")
            book_row = row_by_job.get(manual_job_id)
            if not book_row or not bool((book_row.get("pipeline") or {}).get("stage3_ready")) or not book_row.get("result_dir"):
                complete = False
                continue
            index_path = Path(runtime.config.processed_dir) / Path(str(book_row["result_dir"])).name / "retrieval_index.jsonl"
            if not index_path.is_file():
                complete = False
                continue
            index_paths.append(index_path)
        if not complete or len(index_paths) != len(manual_items):
            equipment_embedding_status[equipment_id] = {
                "ready": False,
                "reason": "machine_waiting_for_all_manual_stage3",
                "manual_count": len(manual_items),
                "ready_manual_count": len(index_paths),
            }
        else:
            equipment_embedding_status[equipment_id] = equipment_hybrid_index_status(
                Path(runtime.config.processed_dir), equipment_id, index_paths,
                model=runtime.config.retrieval_embedding_model,
                document_prefix=runtime.config.retrieval_embedding_document_prefix,
                manual_types=manual_types,
            )

    for row in rows:
        job_id = int(row.get("id") or 0)
        pipeline = row.get("pipeline") or {}
        owner = owner_by_job.get(job_id)
        if owner:
            equipment_id = str(owner.get("equipment_id") or "")
            pipeline["machine_assigned"] = True
            pipeline["machine_id"] = equipment_id
            pipeline["machine_name"] = owner.get("name")
            machine_status = equipment_embedding_status.get(equipment_id, {"ready": False, "reason": "machine_embedding_not_built"})
            pipeline["machine_embedding_ready"] = bool(machine_status.get("ready"))
            pipeline["machine_embedding_reason"] = machine_status.get("reason")
            pipeline["machine_embedding_rows"] = int(machine_status.get("rows") or 0)
        else:
            pipeline["machine_embedding_ready"] = False
            pipeline["machine_embedding_reason"] = "manual_not_assigned_to_machine"
            pipeline["machine_embedding_rows"] = 0

        if pipeline.get("stage3_ready"):
            if not owner:
                pipeline["next_stage"] = "assign_machine"
                pipeline["blocked_reason"] = "Assign this manual to its physical machine before hybrid retrieval."
            elif pipeline.get("machine_embedding_ready"):
                pipeline["next_stage"] = "rag_ready"
                pipeline["blocked_reason"] = None
            else:
                pipeline["next_stage"] = "machine_embedding"
                pipeline["blocked_reason"] = "Machine embeddings are waiting for all assigned manuals to finish Stage 3, or are rebuilding after an upstream change."
        row["pipeline"] = pipeline

    return {
        "documents": rows,
        "pipeline_sequence": dict(runtime.pipeline_sequence_state),
        "equipment_embedding_status": equipment_embedding_status,
    }


@app.post("/api/stage2c/books/{postprocess_job_id}/correction-suggestions")
async def stage2c_correction_suggestions(postprocess_job_id: int) -> dict:
    try:
        result = await runtime.stage2b_worker.start_correction_suggestion_backfill(postprocess_job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.get("accepted") and result.get("reason") == "already_running":
        raise HTTPException(status_code=409, detail="Pi5 correction suggestions are already running for this book.")
    return result


@app.get("/api/stage2c/books/{postprocess_job_id}/correction-suggestions")
async def stage2c_correction_suggestions_status(postprocess_job_id: int) -> dict:
    state = runtime.stage2b_worker.correction_suggestion_state_for(postprocess_job_id)
    return state or {
        "postprocess_job_id": postprocess_job_id,
        "status": "not_run",
        "total": 0,
        "processed": 0,
        "suggestions_ready": 0,
        "unchanged_or_rejected": 0,
        "errors": 0,
    }


@app.post("/api/stage2c/books/{postprocess_job_id}/build")
async def stage2c_build_book(postprocess_job_id: int) -> dict:
    try:
        result = await runtime.stage2b_worker.start_stage2c_backfill(postprocess_job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.get("accepted") and result.get("reason") == "already_running":
        raise HTTPException(status_code=409, detail="Stage 2C is already building for this book.")
    return result


@app.get("/api/stage2c/books/{postprocess_job_id}/status")
async def stage2c_book_status(postprocess_job_id: int) -> dict:
    job = await runtime.postprocess_store.get_job(postprocess_job_id)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
    state = runtime.stage2b_worker.stage2c_state_for(postprocess_job_id)
    if state is None:
        status_path = result_dir / "stage2c_backfill.json"
        if status_path.is_file():
            try:
                state = await asyncio.to_thread(_load_json_file, status_path)
            except (OSError, json.JSONDecodeError):
                state = None
    return {
        "postprocess_job_id": postprocess_job_id,
        "status": (state or {}).get("status") or ("ready" if (result_dir / "correction_ledger.json").is_file() and (result_dir / "chunk_overlays.jsonl").is_file() else "not_built"),
        "state": state,
        "ledger_available": (result_dir / "correction_ledger.json").is_file(),
        "overlays_available": (result_dir / "chunk_overlays.jsonl").is_file(),
    }


@app.post("/api/postprocess/jobs/{job_id}/retry")
async def retry_postprocess_job(job_id: int) -> dict:
    if not await runtime.postprocess_store.retry(job_id):
        raise HTTPException(status_code=404, detail="A failed post-process job with this identifier was not found.")
    runtime.events.notify("postprocess_retried")
    return {"accepted": True}


@app.post("/api/postprocess/jobs/{job_id}/rerun")
async def rerun_postprocess_job(job_id: int) -> dict:
    job = await runtime.postprocess_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    if job.get("status") in {"pending", "processing"}:
        raise HTTPException(status_code=409, detail="This Stage 2 analysis is already queued or running.")
    output_path = Path(runtime.config.output_dir) / str(job.get("output_filename") or "")
    if not output_path.is_file():
        raise HTTPException(status_code=409, detail="The converted ZIP is missing. Restore it to the converted folder before rerunning.")
    if not await runtime.postprocess_store.rerun(job_id):
        raise HTTPException(status_code=409, detail="Stage 2 could not be queued for rerun.")
    runtime.events.notify("postprocess_rerun")
    return {"accepted": True, "stage": "quality_routing", "docling_reconversion": False}


@app.post("/api/jobs/{job_id}/delete")
async def delete_conversion_queue_item(job_id: int, request: DeleteBookRequest) -> dict:
    """Remove a terminal conversion row that never became a managed book.

    Typical use: an input manual was renamed after discovery, leaving a failed
    FileMissing queue row. Existing source/output files are quarantined when
    present, while already-missing files do not block cleanup.
    """
    if not request.confirm:
        raise HTTPException(status_code=422, detail="Explicit delete confirmation is required.")
    job = await runtime.store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Queue item not found.")
    if str(job.get("status") or "") not in {"failed", "completed"}:
        raise HTTPException(status_code=409, detail="Only failed or completed queue items can be removed. Wait for active conversion work to finish first.")

    try:
        quarantine = await asyncio.to_thread(quarantine_conversion_job, runtime.config, job)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not quarantine the queue item files: {exc}") from exc

    try:
        deleted = await runtime.store.delete_terminal_job(job_id)
    except RuntimeError as exc:
        rollback_errors = await asyncio.to_thread(restore_quarantined_artifacts, quarantine)
        detail = str(exc)
        if rollback_errors:
            detail += " File rollback also reported: " + "; ".join(rollback_errors)
        raise HTTPException(status_code=409, detail=detail) from exc
    except Exception as exc:
        rollback_errors = await asyncio.to_thread(restore_quarantined_artifacts, quarantine)
        detail = f"Queue deletion failed: {exc}"
        if rollback_errors:
            detail += " File rollback also reported: " + "; ".join(rollback_errors)
        raise HTTPException(status_code=500, detail=detail) from exc
    if deleted is None:
        await asyncio.to_thread(restore_quarantined_artifacts, quarantine)
        raise HTTPException(status_code=404, detail="Queue item disappeared before it could be deleted.")

    runtime.events.notify("queue_item_deleted", filename=job.get("filename"), job_id=job_id)
    return {
        "deleted": True,
        "conversion_job_id": job_id,
        "filename": job.get("filename"),
        "quarantine": quarantine,
        "message": "Queue item removed. Existing source/output files were quarantined; already-missing files were simply cleared from history.",
    }


@app.post("/api/postprocess/jobs/{job_id}/delete")
async def delete_book(job_id: int, request: DeleteBookRequest) -> dict:
    """Remove one book from the active pipeline without destroying source files.

    The original input, converted Docling ZIP and processed result directory are
    moved into per-root ``_deleted_books`` quarantine folders before their DB
    rows are removed.  Because the watcher/importer scan only the root folders,
    the deleted book stays deleted until the operator explicitly restores it.
    """
    if not request.confirm:
        raise HTTPException(status_code=422, detail="Explicit delete confirmation is required.")
    job = await runtime.postprocess_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    if str(job.get("status") or "") in {"pending", "processing"}:
        raise HTTPException(status_code=409, detail="Stage 2A is queued or processing this book. Wait for it to finish before deleting.")

    conversion = await runtime.postprocess_store.get_conversion_job(int(job.get("conversion_job_id") or 0))
    if conversion and str(conversion.get("status") or "") in {"pending", "processing"}:
        raise HTTPException(status_code=409, detail="Docling conversion is queued or processing this book. Wait for it to finish before deleting.")

    verification_rows = await runtime.stage2b_store.list_book_jobs_raw(job_id)
    if any(str(row.get("status") or "") == "processing" for row in verification_rows):
        raise HTTPException(status_code=409, detail="Verification is currently processing this book. Stop/wait for it before deleting.")

    stage2c_state = runtime.stage2b_worker.stage2c_state_for(job_id) or {}
    if str(stage2c_state.get("status") or "") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Stage 2C is currently building this book. Wait for it to finish before deleting.")
    stage3_state = runtime.stage3_builder.state_for(job_id) or {}
    if str(stage3_state.get("status") or "") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Stage 3 is currently building this book. Wait for it to finish before deleting.")

    delete_row = {
        **job,
        "conversion_filename": (conversion or {}).get("filename"),
        "conversion_output_filename": (conversion or {}).get("output_filename"),
        "conversion_source_kind": (conversion or {}).get("source_kind") or job.get("source_kind"),
    }
    try:
        quarantine = await asyncio.to_thread(quarantine_book_artifacts, runtime.config, delete_row)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not quarantine the book files: {exc}") from exc

    try:
        deleted = await runtime.postprocess_store.delete_book_records(job_id)
    except RuntimeError as exc:
        rollback_errors = await asyncio.to_thread(restore_quarantined_artifacts, quarantine)
        detail = str(exc)
        if rollback_errors:
            detail += " File rollback also reported: " + "; ".join(rollback_errors)
        raise HTTPException(status_code=409, detail=detail) from exc
    except Exception as exc:
        rollback_errors = await asyncio.to_thread(restore_quarantined_artifacts, quarantine)
        detail = f"Database deletion failed: {exc}"
        if rollback_errors:
            detail += " File rollback also reported: " + "; ".join(rollback_errors)
        raise HTTPException(status_code=500, detail=detail) from exc
    if deleted is None:
        await asyncio.to_thread(restore_quarantined_artifacts, quarantine)
        raise HTTPException(status_code=404, detail="Book disappeared before it could be deleted.")

    equipment = await asyncio.to_thread(remove_manual_from_equipment, Path(runtime.config.processed_dir), job_id)
    runtime.events.notify("book_deleted")
    return {
        "deleted": True,
        "postprocess_job_id": job_id,
        "source_filename": job.get("source_filename"),
        "quarantine": quarantine,
        "equipment": equipment,
        "message": "Book removed from the active pipeline. Source files were quarantined, not destroyed.",
    }


def _verifier_provider_label(provider: str, kind: str) -> str:
    if provider == "pi5":
        return "Pi5 Vision · text reconstruction" if kind == "text" else "Pi5 Vision"
    if provider == "oneplus":
        return "OnePlus Vision · text reconstruction" if kind == "text" else "OnePlus Vision"
    return "Groq Vision · text reconstruction" if kind == "text" else "Groq Vision"


def _verifier_provider_model(config: AppConfig, provider: str) -> str | None:
    return config.vision_cloud_model if provider == "groq" else None


@app.get("/api/stage2b/status")
async def stage2b_status() -> dict:
    cloud_selected = runtime.config.text_verifier_provider == "groq" or runtime.config.vision_verifier_provider == "groq"
    quota = await runtime.groq_quota.snapshot() if cloud_selected else None
    key_ready = bool(__import__("os").environ.get(runtime.config.text_cloud_api_key_env, "").strip())
    return {
        "enabled": runtime.config.stage2b_enabled,
        "text_provider": {
            "provider": runtime.config.text_verifier_provider,
            "mode": "cloud" if runtime.config.text_verifier_provider == "groq" else "offline",
            "label": _verifier_provider_label(runtime.config.text_verifier_provider, "text"),
            "primary_model": _verifier_provider_model(runtime.config, runtime.config.text_verifier_provider),
            "api_key_configured": key_ready if runtime.config.text_verifier_provider == "groq" else None,
            "quota": quota if runtime.config.text_verifier_provider == "groq" else None,
            "automatic_fallback": False,
        },
        "vision_provider": {
            "provider": runtime.config.vision_verifier_provider,
            "mode": "cloud" if runtime.config.vision_verifier_provider == "groq" else "offline",
            "label": _verifier_provider_label(runtime.config.vision_verifier_provider, "vision"),
            "primary_model": _verifier_provider_model(runtime.config, runtime.config.vision_verifier_provider),
            "api_key_configured": key_ready if runtime.config.vision_verifier_provider == "groq" else None,
            "quota": quota if runtime.config.vision_verifier_provider == "groq" else None,
            "automatic_fallback": False,
        },
        "modes": {
            "pi5": {"auto_run": runtime.config.stage2b_pi5_auto_run, "paused": runtime.config.stage2b_pi5_paused},
            "oneplus": {"auto_run": runtime.config.stage2b_oneplus_auto_run, "paused": runtime.config.stage2b_oneplus_paused},
        },
        "artifact_sweep": {
            "enabled": bool(getattr(runtime.config, "stage2b_artifact_sweep_enabled", True)),
            "required_for_finalize": bool(getattr(runtime.config, "stage2b_artifact_sweep_required_for_finalize", True)),
            "sequence": "after_normal_text_vision",
        },
        "counts": await runtime.stage2b_store.counts(),
        "workers": runtime.stage2b_worker.worker_state,
        "manual_crosschecks": {str(k): v for k, v in runtime.stage2b_worker.manual_crosscheck_state.items()},
        "jobs": await runtime.stage2b_store.list_jobs(limit=100),
    }


@app.get("/api/stage2b/queue/{target}")
async def stage2b_queue(target: str) -> dict:
    if target not in {"pi5", "oneplus"}:
        raise HTTPException(status_code=404, detail="Unknown verification device.")
    return {
        "target": target,
        "jobs": await runtime.stage2b_store.list_remaining(target, limit=5000),
    }


@app.get("/api/stage2b/results/{target}")
async def stage2b_results(target: str) -> dict:
    if target not in {"pi5", "oneplus"}:
        raise HTTPException(status_code=404, detail="Unknown verification device.")
    return {"target": target, "jobs": await runtime.stage2b_store.list_results(target, limit=5000)}


def _audit_json(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


@app.get("/api/stage2b/text-audit")
async def stage2b_text_audit(
    postprocess_job_id: int | None = None,
    limit: int = 1000,
    offset: int = 0,
    book: str = "",
    outcome: str = "",
    query: str = "",
    verification_job_id: int | None = None,
) -> dict:
    """Read-only audit of completed/failed Text verifier source transcriptions."""
    rows = await runtime.stage2b_store.list_results_raw("pi5", limit=5000)
    rows = [row for row in rows if str(_audit_json(row.get("source_json")).get("type") or "") != "picture"]
    if postprocess_job_id is not None:
        rows = [row for row in rows if int(row.get("postprocess_job_id") or 0) == int(postprocess_job_id)]

    current_result_dirs = await _current_audit_result_dirs()
    ledger_cache: dict[str, dict] = {}
    jobs: list[dict] = []
    for row in rows:
        source = _audit_json(row.get("source_json"))
        request = _audit_json(row.get("request_json"))
        result = _audit_json(row.get("result_json"))
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        correction = result.get("correction") if isinstance(result.get("correction"), dict) else {}
        reconstruction = result.get("source_reconstruction") if isinstance(result.get("source_reconstruction"), dict) else {}
        scope_guard = correction.get("scope_guard") if isinstance(correction.get("scope_guard"), dict) else {}
        if not scope_guard and isinstance(reconstruction.get("scope_guard"), dict):
            scope_guard = reconstruction.get("scope_guard") or {}

        postprocess_id = int(row.get("postprocess_job_id") or 0)
        result_dir_name = current_result_dirs.get(postprocess_id) or Path(str(row.get("result_dir") or "")).name
        downstream = None
        if result_dir_name:
            if result_dir_name not in ledger_cache:
                ledger_path = Path(runtime.config.processed_dir) / result_dir_name / "correction_ledger.json"
                ledger_cache[result_dir_name] = await asyncio.to_thread(_load_json_file, ledger_path)
            entry_id = f"{row.get('generation')}:text:{row.get('route_id')}"
            entry = _authoritative_text_entry(ledger_cache[result_dir_name], entry_id)
            if isinstance(entry, dict):
                downstream = {
                    "status": entry.get("status"),
                    "status_reason": entry.get("status_reason"),
                    "entry_type": entry.get("entry_type"),
                    "human_verified": bool(entry.get("human_verified")),
                    "proposed_text": entry.get("proposed_text"),
                    "raw_docling_immutable": entry.get("raw_docling_immutable", True),
                    "entry_id": entry.get("entry_id"),
                    "verification_verdict": entry.get("verification_verdict"),
                    "current_authoritative": True,
                }

        disposition = "failed" if row.get("status") == "failed" else str(correction.get("status") or "verified_original")
        human_review_required = bool(
            downstream
            and not downstream.get("human_verified")
            and str(downstream.get("verification_verdict") or "").upper() in {"LIKELY_CORRUPT", "UNCERTAIN"}
        )
        jobs.append({
            "id": int(row.get("id") or 0),
            "postprocess_job_id": int(row.get("postprocess_job_id") or 0),
            "generation": row.get("generation"),
            "book": row.get("output_filename") or row.get("result_dir"),
            "route_id": row.get("route_id"),
            "code": row.get("code"),
            "priority": row.get("priority"),
            "reason": row.get("reason"),
            "status": row.get("status"),
            "verdict": row.get("verdict"),
            "model": row.get("model"),
            "provider": result.get("text_provider") or request.get("selected_processor"),
            "processing_seconds": row.get("processing_seconds"),
            "completed_at": row.get("completed_at"),
            "error_type": row.get("error_type"),
            "error_message": row.get("error_message"),
            "source": source,
            "request": {
                "page": request.get("page"),
                "source_type": request.get("source_type"),
                "text_index": request.get("text_index"),
                "table_index": request.get("table_index"),
                "cell_index": request.get("cell_index"),
                "suspect_text": request.get("suspect_text"),
                "before_anchors": request.get("before_anchors") or [],
                "after_anchors": request.get("after_anchors") or [],
                "instruction": request.get("instruction"),
                "selected_processor": request.get("selected_processor"),
                "target_crop": request.get("target_crop"),
                "source_image_sha256": request.get("source_image_sha256"),
            },
            "reconstruction": reconstruction,
            "parsed": parsed,
            "correction": correction,
            "scope_guard": scope_guard,
            "raw_response": result.get("raw_response"),
            "disposition": disposition,
            "crop_image_url": f"/api/stage2b/jobs/{int(row.get('id') or 0)}/text-image",
            "downstream": downstream,
            "human_review_required": human_review_required,
            "raw_docling_immutable": True,
        })

    summary = {
        "total": len(jobs),
        "failed": sum(1 for j in jobs if j.get("status") == "failed"),
        "applied": sum(1 for j in jobs if j.get("disposition") == "applied"),
        "verified_original": sum(1 for j in jobs if j.get("disposition") == "verified_original"),
        "pending": sum(1 for j in jobs if j.get("disposition") == "pending"),
        "safety_rejected": sum(1 for j in jobs if j.get("scope_guard") and j.get("scope_guard", {}).get("accepted") is False),
        "stage2c_applied": sum(1 for j in jobs if (j.get("downstream") or {}).get("status") == "applied"),
        "human_review_required": sum(1 for j in jobs if j.get("human_review_required")),
        "human_reviewed": sum(1 for j in jobs if (j.get("downstream") or {}).get("human_verified")),
    }
    books = sorted({str(job.get("book") or "") for job in jobs if str(job.get("book") or "")})
    wanted_book = str(book or "").strip()
    wanted_outcome = str(outcome or "").strip().lower()
    wanted_query = str(query or "").strip().lower()
    filtered = []
    for item in jobs:
        if verification_job_id is not None and int(item.get("id") or 0) != int(verification_job_id):
            continue
        if wanted_book and str(item.get("book") or "") != wanted_book:
            continue
        actual = "failed" if item.get("status") == "failed" else str(item.get("disposition") or "")
        if wanted_outcome == "human_review":
            if not item.get("human_review_required"):
                continue
        elif wanted_outcome == "human_reviewed":
            if not (item.get("downstream") or {}).get("human_verified"):
                continue
        elif wanted_outcome and actual != wanted_outcome:
            continue
        if wanted_query:
            request = item.get("request") or {}
            correction = item.get("correction") or {}
            scope = item.get("scope_guard") or {}
            blob = " ".join([
                str(item.get("book") or ""), str(item.get("route_id") or ""), str(item.get("code") or ""),
                str(item.get("reason") or ""), str(request.get("page") or ""), str(request.get("suspect_text") or ""),
                str(correction.get("proposed_text") or ""), " ".join(str(value) for value in (scope.get("reasons") or [])),
            ]).lower()
            if wanted_query not in blob:
                continue
        filtered.append(item)
    offset = max(0, int(offset))
    page_limit = max(1, min(int(limit), 5000))
    return {
        "schema": "docling-text-verifier-audit/v2",
        "summary": summary,
        "books": books,
        "total_filtered": len(filtered),
        "offset": offset,
        "limit": page_limit,
        "jobs": filtered[offset:offset + page_limit],
    }


@app.get("/api/stage2b/jobs/{job_id}/text-image")
async def stage2b_text_audit_image(job_id: int):
    try:
        image_bytes, mime, label = await runtime.stage2b_worker.text_audit_image(job_id)
    except (ValueError, FileNotFoundError, IndexError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=image_bytes, media_type=mime, headers={"X-Text-Audit-Region": str(label)})


@app.get("/api/stage2b/vision-audit")
async def stage2b_vision_audit(postprocess_job_id: int | None = None, limit: int = 1000) -> dict:
    """Read-only audit view of completed Vision verifier work.

    This endpoint intentionally exposes the persisted prompt/raw response used
    for audit, but never mutates the correction ledger or converted Docling ZIP.
    """
    row_limit = max(1, min(int(limit), 5000))
    rows = (
        await runtime.stage2b_store.list_results_raw("pi5", limit=row_limit)
        + await runtime.stage2b_store.list_results_raw("oneplus", limit=row_limit)
    )
    rows = [row for row in rows if str(_audit_json(row.get("source_json")).get("type") or "") == "picture"]
    rows.sort(key=lambda row: int(row.get("id") or 0), reverse=True)
    rows = rows[:row_limit]
    if postprocess_job_id is not None:
        rows = [row for row in rows if int(row.get("postprocess_job_id") or 0) == int(postprocess_job_id)]

    current_result_dirs = await _current_audit_result_dirs()
    ledger_cache: dict[str, dict] = {}
    jobs: list[dict] = []
    for row in rows:
        source = _audit_json(row.get("source_json"))
        request = _audit_json(row.get("request_json"))
        result = _audit_json(row.get("result_json"))
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        crop_audit = result.get("crop_audit") if isinstance(result.get("crop_audit"), list) else []
        postprocess_id = int(row.get("postprocess_job_id") or 0)
        result_dir_name = current_result_dirs.get(postprocess_id) or Path(str(row.get("result_dir") or "")).name
        downstream = None
        if result_dir_name:
            if result_dir_name not in ledger_cache:
                ledger_path = Path(runtime.config.processed_dir) / result_dir_name / "correction_ledger.json"
                ledger_cache[result_dir_name] = await asyncio.to_thread(_load_json_file, ledger_path)
            entry_id = f"{row.get('generation')}:vision:{row.get('route_id')}"
            source_index_value = source.get("index")
            if source_index_value is None:
                source_index_value = source.get("picture_index")
            if source_index_value is None:
                source_index_value = source.get("source_index")
            try:
                source_index_value = int(source_index_value)
            except (TypeError, ValueError):
                source_index_value = None
            entry = _authoritative_visual_entry(
                ledger_cache[result_dir_name], entry_id=entry_id, source_index=source_index_value
            )
            if isinstance(entry, dict):
                downstream = {
                    "status": entry.get("status"),
                    "status_reason": entry.get("status_reason"),
                    "diagram_category": entry.get("diagram_category"),
                    "generated_summary": entry.get("generated_summary"),
                    "visible_text": entry.get("visible_text") or [],
                    "visible_objects": entry.get("visible_objects") or [],
                    "crop_coverage": entry.get("crop_coverage"),
                    "unresolved": entry.get("unresolved"),
                    "raw_docling_immutable": entry.get("raw_docling_immutable", True),
                    "entry_id": entry.get("entry_id"),
                    "human_visual_decision": entry.get("human_visual_decision"),
                    "human_verified": bool(entry.get("human_verified")),
                    "current_authoritative": True,
                    "human_evidence_recovery_required": bool(
                        entry.get("human_evidence_recovery_required")
                        or (
                            str(entry.get("human_visual_decision") or "") in {"technical", "useful"}
                            and not (
                                (entry.get("visible_text") or [])
                                or (entry.get("visible_objects") or [])
                                or str(entry.get("generated_summary") or "").strip()
                            )
                        )
                    ),
                    "human_evidence_recovered_at_epoch": entry.get("human_evidence_recovered_at_epoch"),
                    "human_evidence_recovery_audit_file": entry.get("human_evidence_recovery_audit_file"),
                }

        crop_regions = [str(item.get("region")) for item in crop_audit if isinstance(item, dict) and item.get("region")]
        jobs.append({
            "id": int(row.get("id") or 0),
            "postprocess_job_id": int(row.get("postprocess_job_id") or 0),
            "generation": row.get("generation"),
            "book": row.get("output_filename") or row.get("result_dir"),
            "route_id": row.get("route_id"),
            "code": row.get("code"),
            "priority": row.get("priority"),
            "reason": row.get("reason"),
            "status": row.get("status"),
            "verdict": row.get("verdict"),
            "model": row.get("model"),
            "provider": result.get("vision_provider") or request.get("vision_provider"),
            "processing_seconds": row.get("processing_seconds"),
            "completed_at": row.get("completed_at"),
            "error_type": row.get("error_type"),
            "error_message": row.get("error_message"),
            "source": source,
            "request": {
                "artifact": request.get("artifact"),
                "page": request.get("page"),
                "picture_index": request.get("picture_index"),
                "reason": request.get("reason"),
                "full_image_prompt": request.get("full_image_prompt"),
                "crop_policy": request.get("crop_policy"),
                "crop_settings": request.get("crop_settings"),
                "image_sha256": request.get("image_sha256"),
            },
            "classification": {
                "verdict": parsed.get("verdict") or row.get("verdict"),
                "confidence": parsed.get("confidence"),
                "diagram_category": parsed.get("diagram_category"),
                "summary": parsed.get("summary"),
                "visible_text": parsed.get("visible_text") or [],
                "visible_objects": parsed.get("visible_objects") or [],
                "unresolved": parsed.get("unresolved"),
                "unresolved_reason": parsed.get("unresolved_reason"),
                "deterministic_override": parsed.get("deterministic_override"),
                "structural_image_evidence": parsed.get("structural_image_evidence"),
                "crop_count": parsed.get("crop_count", 0),
                "crop_coverage": parsed.get("crop_coverage"),
                "crop_early_stop": parsed.get("crop_early_stop"),
                "incomplete_crop_count": parsed.get("incomplete_crop_count", 0),
                "full_image_parse_failed": bool(parsed.get("full_image_parse_failed")),
                "full_image_partial_recovery": bool(parsed.get("full_image_partial_recovery")),
            },
            "full_image": {
                "parsed": parsed.get("full_image") if isinstance(parsed.get("full_image"), dict) else None,
                "raw_response": result.get("full_image_raw_response"),
                "attempts": result.get("full_image_attempts") or [],
                "image_url": f"/api/stage2b/jobs/{int(row.get('id') or 0)}/vision-image?region=full",
            },
            "crops": [
                {
                    **item,
                    "image_url": f"/api/stage2b/jobs/{int(row.get('id') or 0)}/vision-image?region={str(item.get('region') or '')}",
                }
                for item in crop_audit if isinstance(item, dict)
            ],
            "crop_regions": crop_regions,
            "downstream": downstream,
            "raw_docling_immutable": True,
        })

    summary = {
        "total": len(jobs),
        "technical_useful": sum(1 for j in jobs if j.get("verdict") == "TECHNICAL_USEFUL"),
        "decorative_or_low_value": sum(1 for j in jobs if j.get("verdict") == "DECORATIVE_OR_LOW_VALUE"),
        "uncertain": sum(1 for j in jobs if j.get("verdict") == "UNCERTAIN"),
        "failed": sum(1 for j in jobs if j.get("status") == "failed"),
        "with_crops": sum(1 for j in jobs if j.get("crop_regions")),
        "applied_enrichment": sum(1 for j in jobs if (j.get("downstream") or {}).get("status") == "applied"),
        "excluded": sum(1 for j in jobs if (j.get("downstream") or {}).get("status") == "excluded"),
        "human_review_required": sum(1 for j in jobs if (j.get("downstream") or {}).get("current_authoritative") and not (j.get("downstream") or {}).get("human_visual_decision") and (str((j.get("classification") or {}).get("verdict") or j.get("verdict") or "").upper() == "UNCERTAIN" or bool((j.get("classification") or {}).get("unresolved")) or (j.get("downstream") or {}).get("status") == "pending")),
        "evidence_recovery_required": sum(1 for j in jobs if (j.get("downstream") or {}).get("current_authoritative") and (j.get("downstream") or {}).get("human_evidence_recovery_required")),
        "human_reviewed": sum(1 for j in jobs if (j.get("downstream") or {}).get("current_authoritative") and (j.get("downstream") or {}).get("human_visual_decision")),
    }
    return {"schema": "docling-vision-verifier-audit/v1", "summary": summary, "jobs": jobs}


@app.post("/api/postprocess/jobs/{job_id}/vision-audit/{entry_id}/decision")
async def vision_audit_human_decision(job_id: int, entry_id: str, request: VisualAuditDecisionRequest) -> dict:
    job = await runtime.postprocess_store.get_job(job_id)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
    try:
        entry = await asyncio.to_thread(apply_human_visual_decision, result_dir, entry_id, request.decision)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    runtime.events.notify("verifier_audit_decision")
    recovery = None
    if bool(entry.get("human_evidence_recovery_required")) and str(entry.get("human_visual_decision") or "") in {"technical", "useful"}:
        verification_job_id = entry.get("verification_job_id")
        if verification_job_id is not None:
            try:
                recovery = await runtime.stage2b_worker.start_human_visual_evidence_recovery(
                    int(verification_job_id), str(entry.get("entry_id") or entry_id)
                )
            except ValueError as exc:
                recovery = {"status": "not_queued", "error": str(exc)}
    audit = await asyncio.to_thread(
        verifier_audit_summary, result_dir, text_require_human=bool(runtime.config.stage2c_require_human_review)
    )
    return {"ok": True, "entry": entry, "audit": audit, "evidence_recovery": recovery}


@app.get("/api/postprocess/jobs/{job_id}/verifier-audit")
async def verifier_audit_gate_status(job_id: int) -> dict:
    job = await runtime.postprocess_store.get_job(job_id)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
    return await asyncio.to_thread(
        verifier_audit_summary, result_dir, text_require_human=bool(runtime.config.stage2c_require_human_review)
    )


@app.post("/api/postprocess/jobs/{job_id}/verifier-audit/bypass")
async def verifier_audit_bypass(job_id: int, request: AuditBypassRequest) -> dict:
    job = await runtime.postprocess_store.get_job(job_id)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    if request.enabled:
        # This is an audit-only testing bypass, never a verification bypass.
        # Keep the audit set stable by requiring every current Stage 2B row
        # (including the required artifact sweep) to finish first.
        rows = await runtime.stage2b_store.list_book_jobs_raw(job_id)
        gate_rows = verification_rows_for_stage2c(
            rows, artifact_sweep_required=bool(getattr(runtime.config, "stage2b_artifact_sweep_required_for_finalize", True))
        )
        unfinished = [row for row in gate_rows if str(row.get("status") or "") != "completed"]
        if unfinished:
            raise HTTPException(
                status_code=409,
                detail=f"Verifier Audit bypass becomes available after verification finishes; {len(unfinished)} current route(s) are still pending/running/failed.",
            )
    result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
    await asyncio.to_thread(set_audit_gate_bypass, result_dir, request.enabled, request.reason)
    runtime.events.notify("verifier_audit_bypass_updated")
    return await asyncio.to_thread(
        verifier_audit_summary, result_dir, text_require_human=bool(runtime.config.stage2c_require_human_review)
    )


@app.get("/api/stage2b/jobs/{job_id}/vision-image")
async def stage2b_vision_audit_image(job_id: int, region: str = "full"):
    try:
        image_bytes, mime, label = await runtime.stage2b_worker.vision_audit_image(job_id, region)
    except (ValueError, FileNotFoundError, IndexError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=image_bytes, media_type=mime, headers={"X-Vision-Audit-Region": str(label)})


@app.post("/api/maintenance/revalidate-all")
async def maintenance_revalidate_all() -> dict:
    """Revalidate saved text results, rebuild Stage 2C, then rebuild Stage 3 for all completed books.

    This makes no Pi5/OnePlus/Groq verification calls. Books with unfinished/failed
    Stage 2B routes are skipped and reported instead of being partially rebuilt.
    """
    return await runtime.start_safety_refresh_all()


@app.get("/api/maintenance/revalidate-all/status")
async def maintenance_revalidate_all_status() -> dict:
    return dict(runtime.safety_refresh_state)


@app.get("/api/maintenance/stale-files")
async def maintenance_stale_files_preview() -> dict:
    return await asyncio.to_thread(
        scan_stale_files,
        Path(runtime.config.processed_dir),
        retrieval_rule_version=RETRIEVAL_RULE_VERSION,
    )


@app.post("/api/maintenance/stale-files/clear")
async def maintenance_stale_files_clear(request: StaleCleanupConfirmRequest) -> dict:
    try:
        result = await asyncio.to_thread(
            clear_stale_files,
            Path(runtime.config.processed_dir),
            retrieval_rule_version=RETRIEVAL_RULE_VERSION,
            confirmation_token=request.confirmation_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    runtime.events.notify("maintenance_stale_files_cleared")
    return result


@app.get("/api/stage2b/books")
async def stage2b_books() -> dict:
    return {"books": await runtime.stage2b_store.list_books()}


@app.post("/api/stage2b/books/{postprocess_job_id}/start")
async def stage2b_start_book(postprocess_job_id: int) -> dict:
    if not runtime.config.stage2b_enabled:
        raise HTTPException(status_code=409, detail="Stage 2B verification is disabled.")
    await runtime.stage2b_worker.sync_routes_once()
    await runtime.set_stage2b_paused("pi5", False)
    await runtime.set_stage2b_paused("oneplus", False)
    count = await runtime.stage2b_store.start_manual_book(postprocess_job_id)
    released = await runtime.stage2b_store.release_ready_artifact_sweeps(postprocess_job_id)
    runtime.events.notify("stage2b_book_manual_started")
    if released:
        runtime.events.notify("stage2b_artifact_sweep_released")
    return {
        "accepted": True,
        "postprocess_job_id": postprocess_job_id,
        "authorized_jobs": count,
        "artifact_sweep_released": int(released),
        "sequence": "normal_text_vision_then_artifact_sweep",
    }


@app.post("/api/stage2b/books/{postprocess_job_id}/revalidate-saved-pi5")
async def stage2b_revalidate_saved_pi5(postprocess_job_id: int) -> dict:
    """Re-apply current deterministic verifier policy to persisted Pi5 responses.

    This performs no Pi5 or OnePlus network calls and never changes a human-verified entry.
    """
    try:
        return await runtime.stage2b_worker.revalidate_saved_pi5_results(postprocess_job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.put("/api/stage2b/providers/{kind}")
async def stage2b_provider_update(kind: str, update: VerifierProviderUpdate) -> dict:
    try:
        revised = await runtime.set_verifier_provider(kind, update.provider)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    provider = revised.text_verifier_provider if kind == "text" else revised.vision_verifier_provider
    label = _verifier_provider_label(provider, kind)
    return {
        "accepted": True, "kind": kind, "provider": provider,
        "mode": "cloud" if provider == "groq" else "offline",
        "label": label, "automatic_fallback": False,
    }


@app.get("/api/groq/usage")
async def groq_usage(limit: int = 100) -> dict:
    return await runtime.groq_quota.usage_summary(limit=max(1, min(int(limit), 500)))


@app.put("/api/stage2b/auto-run-all")
async def stage2b_auto_run_all(update: Stage2BAutoRunUpdate) -> dict:
    revised = await runtime.set_stage2b_auto_run_all(update.enabled)
    return {
        "enabled": bool(update.enabled),
        "pi5": revised.stage2b_pi5_auto_run,
        "oneplus": revised.stage2b_oneplus_auto_run,
    }


@app.post("/api/stage2b/{target}/start")
async def stage2b_start(target: str) -> dict:
    if target not in {"pi5", "oneplus"}:
        raise HTTPException(status_code=404, detail="Unknown verification device.")
    if not runtime.config.stage2b_enabled:
        raise HTTPException(status_code=409, detail="Stage 2B verification is disabled.")
    auto = runtime.config.stage2b_pi5_auto_run if target == "pi5" else runtime.config.stage2b_oneplus_auto_run
    if auto:
        raise HTTPException(status_code=409, detail="Auto Run is enabled for this device. Turn it off to start a manual batch.")
    uses_groq = (target == "pi5" and runtime.config.text_verifier_provider == "groq") or (
        target == "oneplus" and runtime.config.vision_verifier_provider == "groq"
    )
    if uses_groq:
        quota = await runtime.groq_quota.snapshot()
        if quota.get("paused"):
            raise HTTPException(status_code=429, detail=quota.get("message") or "Groq quota safety pause is active.")
        if not __import__("os").environ.get(runtime.config.text_cloud_api_key_env, "").strip():
            raise HTTPException(status_code=409, detail=f"{runtime.config.text_cloud_api_key_env} is not configured.")
    await runtime.stage2b_worker.sync_routes_once()
    await runtime.set_stage2b_paused(target, False)
    count = await runtime.stage2b_worker.start_manual(target)
    released = await runtime.stage2b_store.release_ready_artifact_sweeps()
    runtime.events.notify("stage2b_manual_started")
    if released:
        runtime.events.notify("stage2b_artifact_sweep_released")
    return {
        "accepted": True, "target": target, "authorized_jobs": count,
        "artifact_sweep_released": int(released),
        "sequence": "normal_text_vision_then_artifact_sweep",
    }


@app.post("/api/stage2b/{target}/stop")
async def stage2b_stop_verifier(target: str) -> dict:
    if target not in {"pi5", "oneplus"}:
        raise HTTPException(status_code=404, detail="Unknown verification device.")
    revised = await runtime.set_stage2b_paused(target, True)
    active = runtime.stage2b_worker.worker_state.get(target, {}).get("active_job_id")
    return {
        "accepted": True,
        "target": target,
        "paused": True,
        "active_job_finishing": active,
        "auto_run": revised.stage2b_pi5_auto_run if target == "pi5" else revised.stage2b_oneplus_auto_run,
    }


@app.put("/api/stage2b/{target}/auto-run")
async def stage2b_auto_run(target: str, update: Stage2BAutoRunUpdate) -> dict:
    if target not in {"pi5", "oneplus"}:
        raise HTTPException(status_code=404, detail="Unknown verification device.")
    try:
        revised = await runtime.set_stage2b_auto_run(target, update.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "target": target,
        "enabled": revised.stage2b_pi5_auto_run if target == "pi5" else revised.stage2b_oneplus_auto_run,
    }


@app.post("/api/stage2b/jobs/{job_id}/crosscheck")
async def stage2b_manual_crosscheck(job_id: int) -> dict:
    try:
        return await runtime.stage2b_worker.start_manual_crosscheck(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/stage2b/jobs/{job_id}/crosscheck")
async def stage2b_manual_crosscheck_status(job_id: int) -> dict:
    state = runtime.stage2b_worker.manual_crosscheck_state_for(job_id)
    return state or {"job_id": job_id, "status": "not_run"}


@app.post("/api/stage2b/jobs/{job_id}/rerun")
async def stage2b_rerun(job_id: int) -> dict:
    if not await runtime.stage2b_store.rerun(job_id):
        raise HTTPException(status_code=409, detail="Only a completed or failed current verification job can be rerun.")
    runtime.events.notify("stage2b_rerun")
    return {"accepted": True, "docling_reconversion": False, "stage2a_rerun": False}


@app.post("/api/stage2b/retry-all-failed")
async def stage2b_retry_all_failed() -> dict:
    """Retry every failed current Text/Vision route with one explicit action."""
    if not runtime.config.stage2b_enabled:
        raise HTTPException(status_code=409, detail="Stage 2B verification is disabled.")
    counts = await runtime.stage2b_store.retry_all_failed()
    if counts.get("pi5"):
        await runtime.set_stage2b_paused("pi5", False)
    if counts.get("oneplus"):
        await runtime.set_stage2b_paused("oneplus", False)
    if counts.get("total"):
        runtime.events.notify("stage2b_retry_all_failed")
    return {
        "accepted": bool(counts.get("total")),
        "reason": None if counts.get("total") else "no_failed_jobs",
        "retried": counts,
        "successful_jobs_untouched": True,
        "pending_jobs_untouched": True,
    }


@app.post("/api/stage2b/jobs/{job_id}/retry")
async def stage2b_retry(job_id: int) -> dict:
    if not await runtime.stage2b_store.retry(job_id):
        raise HTTPException(status_code=404, detail="A failed current verification job with this identifier was not found.")
    runtime.events.notify("stage2b_retried")
    return {"accepted": True}


@app.get("/api/stage2b/jobs/{job_id}/result")
async def stage2b_result(job_id: int):
    job = await runtime.stage2b_store.get_job(job_id)
    if not job or not job.get("artifact_path"):
        raise HTTPException(status_code=404, detail="Verification result not found.")
    path = Path(runtime.config.processed_dir) / str(job["artifact_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Verification result artifact is missing.")
    return FileResponse(path, filename=path.name, media_type="application/json")


async def _retrieval_books() -> list[dict]:
    jobs = await runtime.postprocess_store.list_jobs(limit=500)
    output: list[dict] = []
    for job in jobs:
        if job.get("status") != "completed" or not job.get("result_dir"):
            continue
        result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
        identity = await asyncio.to_thread(repair_identity_metadata, result_dir, int(job.get("id") or 0))
        chunks_path = result_dir / "chunks.jsonl"
        quality_path = result_dir / "retrieval_quality.json"
        quality = None
        if quality_path.is_file():
            try:
                quality = await asyncio.to_thread(_load_json_file, quality_path)
            except (OSError, json.JSONDecodeError, TypeError):
                quality = None
        source_name = str(job.get("source_filename") or Path(str(job.get("output_filename") or result_dir.name)).stem)
        if quality and quality.get("source_filename"):
            source_name = str(quality.get("source_filename"))
        verification_rows = await runtime.stage2b_store.list_book_jobs_raw(int(job.get("id") or 0))
        stage2c_info = stage2c_freshness(result_dir, verification_rows, rule_version=STAGE2C_RULE_VERSION, artifact_sweep_required=bool(getattr(runtime.config, "stage2b_artifact_sweep_required_for_finalize", True)))
        stage3_info = stage3_freshness(result_dir, stage2c_info, stage3_rule_version=STAGE3_RULE_VERSION, retrieval_rule_version=RETRIEVAL_RULE_VERSION)
        verification_lookup: dict[int, dict] = {}
        for verification_row in verification_rows:
            source_meta = _audit_json(verification_row.get("source_json"))
            if str(source_meta.get("type") or "") != "picture":
                continue
            request_meta = _audit_json(verification_row.get("request_json"))
            result_meta = _audit_json(verification_row.get("result_json"))
            provider = str(
                result_meta.get("vision_provider")
                or request_meta.get("vision_provider")
                or source_meta.get("processor")
                or verification_row.get("target")
                or ""
            ).strip()
            try:
                verification_lookup[int(verification_row.get("id"))] = {
                    "provider": provider or None,
                    "worker": verification_row.get("target"),
                }
            except (TypeError, ValueError):
                continue
        visual_summary = await asyncio.to_thread(
            ensure_visual_evidence_fresh,
            result_dir,
            postprocess_job_id=int(job.get("id") or 0),
            source_filename=source_name,
            verification_lookup=verification_lookup,
        )
        retrieval_index_path = result_dir / "retrieval_index.jsonl"
        output.append({
            "postprocess_job_id": int(job.get("id") or 0),
            "source_filename": source_name,
            "result_dir": result_dir.name,
            "index_ready": bool(stage3_info.get("ready") and retrieval_index_path.is_file()),
            "stage2c_current": bool(stage2c_info.get("ready")),
            "stage2c_reason": stage2c_info.get("reason"),
            "stage3_current": bool(stage3_info.get("ready")),
            "stage3_reason": stage3_info.get("reason"),
            "identity_integrity": identity,
            "identity_metadata_mismatch": not bool(identity.get("ok")),
            "chunks_available": chunks_path.is_file(),
            # Hybrid embeddings are machine/equipment-scoped from .40.3 onward.
            # Single-book scopes remain available for lexical inspection only.
            "hybrid_index_ready": False,
            "hybrid_index_status": {"ready": False, "scope": "equipment", "reason": "machine_scoped_embeddings"},
            "visual_index_ready": (result_dir / "visual_evidence_index.jsonl").is_file(),
            "quality": quality,
            "visual_quality": visual_summary,
        })
    output.sort(key=lambda row: row["source_filename"].lower())
    return output


def _retrieval_index_paths(books: list[dict], postprocess_job_id: int | None = None) -> list[Path]:
    selected = books
    if postprocess_job_id is not None:
        selected = [row for row in books if int(row.get("postprocess_job_id") or 0) == int(postprocess_job_id)]
    return [
        Path(runtime.config.processed_dir) / str(row["result_dir"]) / "retrieval_index.jsonl"
        for row in selected
        if row.get("index_ready")
    ]


def _visual_index_paths(books: list[dict], postprocess_job_id: int | None = None) -> list[Path]:
    selected = books
    if postprocess_job_id is not None:
        selected = [row for row in books if int(row.get("postprocess_job_id") or 0) == int(postprocess_job_id)]
    return [
        Path(runtime.config.processed_dir) / str(row["result_dir"]) / "visual_evidence_index.jsonl"
        for row in selected
        if row.get("visual_index_ready")
    ]


def _equipment_scope_context(books: list[dict], equipment_id: str) -> tuple[dict, list[dict], list[Path], dict[int, str], dict]:
    catalog = equipment_catalog(Path(runtime.config.processed_dir), books)
    equipment = next((row for row in catalog.get("equipment") or [] if row.get("equipment_id") == equipment_id), None)
    if not equipment:
        raise HTTPException(status_code=404, detail="Machine scope was not found.")
    selected = resolve_equipment_books(Path(runtime.config.processed_dir), books, equipment_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Machine scope has no available manuals.")
    if not all(bool(row.get("index_ready")) for row in selected):
        raise HTTPException(status_code=409, detail="Every manual assigned to this machine must have a Stage 3 text index before machine retrieval can run.")
    index_paths = _retrieval_index_paths(selected)
    manual_types = {
        int(item.get("postprocess_job_id") or 0): str(item.get("manual_type") or "other")
        for item in equipment.get("manuals") or []
        if int(item.get("postprocess_job_id") or 0) > 0 and item.get("active_for_rag", True)
    }
    hybrid_status = equipment_hybrid_index_status(
        Path(runtime.config.processed_dir), equipment_id, index_paths,
        model=runtime.config.retrieval_embedding_model,
        document_prefix=runtime.config.retrieval_embedding_document_prefix,
        manual_types=manual_types,
    )
    return equipment, selected, index_paths, manual_types, hybrid_status


def _equipment_catalog_with_hybrid(books: list[dict]) -> dict:
    catalog = equipment_catalog(Path(runtime.config.processed_dir), books)
    enriched: list[dict] = []
    for raw in catalog.get("equipment") or []:
        row = dict(raw)
        selected = resolve_equipment_books(Path(runtime.config.processed_dir), books, str(row.get("equipment_id") or ""))
        index_paths = _retrieval_index_paths(selected) if selected and all(bool(book.get("index_ready")) for book in selected) else []
        manual_types = {
            int(item.get("postprocess_job_id") or 0): str(item.get("manual_type") or "other")
            for item in row.get("manuals") or []
            if int(item.get("postprocess_job_id") or 0) > 0 and item.get("active_for_rag", True)
        }
        status = equipment_hybrid_index_status(
            Path(runtime.config.processed_dir), str(row.get("equipment_id") or ""), index_paths,
            model=runtime.config.retrieval_embedding_model,
            document_prefix=runtime.config.retrieval_embedding_document_prefix,
            manual_types=manual_types,
        )
        row["hybrid_ready"] = bool(status.get("ready"))
        row["hybrid_status"] = status
        row["hybrid_rows"] = int(status.get("rows") or 0)
        row["hybrid_model"] = runtime.config.retrieval_embedding_model
        enriched.append(row)
    catalog["equipment"] = enriched
    return catalog


def _preferred_pages(results: list[dict]) -> set[int]:
    pages: set[int] = set()
    for row in results[:3]:
        for value in row.get("page_numbers") or []:
            try:
                pages.add(int(value))
            except (TypeError, ValueError):
                continue
    return pages


async def _visual_results_for_question(
    query: str,
    text_results: list[dict],
    postprocess_job_id: int | None,
    top_k: int,
    books: list[dict] | None = None,
    *,
    equipment_scoped: bool = False,
) -> list[dict]:
    cross_book = is_cross_book_query(query)
    anchor_job = postprocess_job_id
    if anchor_job is None and text_results and not cross_book and not equipment_scoped:
        try:
            anchor_job = int(text_results[0].get("postprocess_job_id"))
        except (TypeError, ValueError):
            anchor_job = None

    # Equipment scope deliberately allows visuals from every manual assigned to
    # the same physical machine. Ordinary legacy/global search remains locked
    # to the anchor book unless the user explicitly asks across manuals.
    if equipment_scoped and books is not None:
        paths = _visual_index_paths(books)
    else:
        result_dirs: list[str] = []
        for row in text_results:
            if not cross_book and anchor_job is not None:
                try:
                    if int(row.get("postprocess_job_id")) != int(anchor_job):
                        continue
                except (TypeError, ValueError):
                    continue
            name = str(row.get("result_dir") or "").strip()
            if name and name not in result_dirs:
                result_dirs.append(name)
        paths = [Path(runtime.config.processed_dir) / name / "visual_evidence_index.jsonl" for name in result_dirs]
        paths = [path for path in paths if path.is_file()]

        if not paths:
            if text_results and books is None:
                return []
            if books is None:
                books = await _retrieval_books()
            paths = _visual_index_paths(books, None if cross_book else anchor_job)
    if not paths:
        return []
    return await asyncio.to_thread(
        search_visual_indices,
        paths,
        query,
        top_k=max(1, int(top_k)),
        preferred_pages=_preferred_pages(text_results),
    )


@app.get("/api/retrieval/status")
async def retrieval_status() -> dict:
    books = await _retrieval_books()
    searchable = excluded = oversized = 0
    visual_eligible = visual_excluded = 0
    ready = 0
    for book in books:
        quality = book.get("quality") or {}
        visual_quality = book.get("visual_quality") or {}
        if book.get("index_ready"):
            ready += 1
        searchable += int(quality.get("searchable_chunks") or 0)
        excluded += int(quality.get("excluded_chunks") or 0)
        oversized += int(quality.get("oversized_searchable_chunks") or 0)
        visual_eligible += int(visual_quality.get("rag_eligible_visuals") or 0)
        visual_excluded += int(visual_quality.get("rag_excluded_visuals") or 0)
    equipment = _equipment_catalog_with_hybrid(books)
    machine_rows = equipment.get("equipment") or []
    machine_hybrid_ready = sum(bool(row.get("hybrid_ready")) for row in machine_rows)
    return {
        "books": books,
        "equipment": machine_rows,
        "unassigned_books": equipment.get("unassigned_books") or [],
        "manual_types": equipment.get("manual_types") or [],
        "books_with_stage3": len(books),
        "books_indexed": ready,
        "machines_configured": len(machine_rows),
        "machines_hybrid_indexed": machine_hybrid_ready,
        "hybrid_enabled": bool(runtime.config.retrieval_hybrid_enabled),
        "embedding_model": runtime.config.retrieval_embedding_model,
        "embedding_url": runtime.config.retrieval_embedding_url,
        "hybrid_candidate_depth": int(runtime.config.retrieval_hybrid_candidate_depth),
        "hybrid_rrf_k": int(runtime.config.retrieval_hybrid_rrf_k),
        "searchable_chunks": searchable,
        "excluded_chunks": excluded,
        "oversized_searchable_chunks": oversized,
        "rag_eligible_visuals": visual_eligible,
        "rag_excluded_visuals": visual_excluded,
        "benchmark_cases": len((load_benchmark(Path(runtime.config.processed_dir)).get("items") or [])),
    }


@app.get("/api/retrieval/equipment")
async def retrieval_equipment_get() -> dict:
    books = await _retrieval_books()
    return _equipment_catalog_with_hybrid(books)


@app.post("/api/retrieval/equipment")
async def retrieval_equipment_upsert(update: EquipmentUpsertRequest) -> dict:
    books = await _retrieval_books()
    try:
        row = upsert_equipment(
            Path(runtime.config.processed_dir),
            books,
            equipment_id=update.equipment_id,
            name=update.name,
            manufacturer=update.manufacturer,
            model=update.model,
            notes=update.notes,
            manuals=[item.model_dump() for item in update.manuals],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"saved": True, "equipment": row, "catalog": _equipment_catalog_with_hybrid(books)}


@app.delete("/api/retrieval/equipment/{equipment_id}")
async def retrieval_equipment_delete(equipment_id: str) -> dict:
    if not delete_equipment(Path(runtime.config.processed_dir), equipment_id):
        raise HTTPException(status_code=404, detail="Equipment scope was not found.")
    books = await _retrieval_books()
    return {"deleted": True, "equipment_id": equipment_id, "catalog": _equipment_catalog_with_hybrid(books)}


@app.post("/api/retrieval/reindex-all")
async def retrieval_reindex_all() -> dict:
    books = await _retrieval_books()
    results = []
    for book in books:
        result_dir = Path(runtime.config.processed_dir) / str(book["result_dir"])
        try:
            quality = await asyncio.to_thread(
                refresh_retrieval_artifacts,
                result_dir,
                max_tokens=runtime.config.stage3_chunk_max_tokens,
            )
            results.append({
                "postprocess_job_id": book["postprocess_job_id"],
                "source_filename": book["source_filename"],
                "status": "completed",
                "quality": quality,
            })
        except Exception as exc:
            results.append({
                "postprocess_job_id": book["postprocess_job_id"],
                "source_filename": book["source_filename"],
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
    return {
        "processed": len(results),
        "completed": sum(row.get("status") == "completed" for row in results),
        "failed": sum(row.get("status") == "failed" for row in results),
        "model_calls": 0,
        "docling_calls": 0,
        "raw_docling_immutable": True,
        "results": results,
    }


@app.post("/api/retrieval/hybrid-index")
async def retrieval_hybrid_index_build(update: RetrievalHybridIndexRequest) -> dict:
    if not runtime.config.retrieval_hybrid_enabled:
        raise HTTPException(status_code=409, detail="Hybrid retrieval is disabled in config.yaml.")
    if update.postprocess_job_id is not None:
        raise HTTPException(status_code=409, detail="Hybrid embeddings are machine-scoped. Select a machine/equipment scope; single-book inspection uses Lexical only.")
    if not update.equipment_id:
        raise HTTPException(status_code=422, detail="Choose one machine/equipment scope before building embeddings.")
    books = await _retrieval_books()
    equipment, selected, paths, manual_types, _ = _equipment_scope_context(books, update.equipment_id)
    scope = {
        "mode": "equipment",
        "equipment_id": update.equipment_id,
        "equipment_name": equipment.get("name"),
        "manual_count": len(selected),
    }
    try:
        result = await asyncio.to_thread(
            build_equipment_embedding_index,
            Path(runtime.config.processed_dir),
            update.equipment_id,
            paths,
            base_url=runtime.config.retrieval_embedding_url,
            model=runtime.config.retrieval_embedding_model,
            document_prefix=runtime.config.retrieval_embedding_document_prefix,
            manual_types=manual_types,
            batch_size=runtime.config.retrieval_embedding_batch_size,
            timeout_seconds=runtime.config.retrieval_embedding_timeout_seconds,
        )
    except EmbeddingServiceError as exc:
        raise HTTPException(status_code=503, detail=f"Embedding service unavailable: {exc}") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Machine hybrid index build failed: {exc}") from exc
    return {
        "scope": scope,
        "model": runtime.config.retrieval_embedding_model,
        "manuals": len(selected),
        "rows": int(result.get("rows") or 0),
        "status": result.get("status"),
        "cache_hit": bool(result.get("cache_hit")),
        "incremental_rebuild": bool(result.get("incremental_rebuild")),
        "reused_vectors": int(result.get("reused_vectors") or 0),
        "embedded_vectors": int(result.get("embedded_vectors") or 0),
        "result": result,
        "model_calls": 0,
        "docling_calls": 0,
        "raw_docling_immutable": True,
        "embedding_scope": "machine",
    }


async def _retrieval_results_for_question(
    query: str,
    postprocess_job_id: int | None,
    equipment_id: str | None,
    top_k: int,
    retrieval_mode: str = "hybrid",
) -> tuple[list[dict], int, list[dict], dict]:
    books = await _retrieval_books()
    selected: list[dict] = []
    paths: list[Path] = []
    manual_types: dict[int, str] = {}
    scope: dict = {}
    mode = str(retrieval_mode or "hybrid").lower()

    if postprocess_job_id is not None:
        selected = [row for row in books if int(row.get("postprocess_job_id") or 0) == int(postprocess_job_id)]
        if not selected:
            raise HTTPException(status_code=404, detail="Book with Stage 3 chunks was not found.")
        paths = _retrieval_index_paths(selected)
        if not paths:
            raise HTTPException(status_code=409, detail="Prepare the Stage 3 retrieval index first.")
        if mode == "hybrid":
            raise HTTPException(status_code=409, detail="Hybrid embeddings are machine-scoped. Select the machine that owns this manual, or use Lexical only for single-book inspection.")
        scope = {
            "mode": "single_book",
            "postprocess_job_id": int(postprocess_job_id),
            "retrieval_mode": "lexical",
            "embedding_scope": "machine_only",
        }
        results = await asyncio.to_thread(search_indices, paths, query, top_k=top_k)
        scope["hybrid"] = None
        return results, len(paths), selected, scope

    if equipment_id:
        equipment, selected, paths, manual_types, hybrid_status = _equipment_scope_context(books, equipment_id)
        scope = {
            "mode": "equipment",
            "equipment_id": equipment_id,
            "equipment_name": equipment.get("name"),
            "manual_count": len(selected),
            "embedding_scope": "machine",
            "hybrid_ready": bool(hybrid_status.get("ready")),
        }
    else:
        raise HTTPException(status_code=422, detail="Choose exactly one retrieval scope: one book or one machine/equipment.")

    if mode == "hybrid":
        if not runtime.config.retrieval_hybrid_enabled:
            raise HTTPException(status_code=409, detail="Hybrid retrieval is disabled. Choose Lexical only or enable it in config.yaml.")
        try:
            results, hybrid_meta = await asyncio.to_thread(
                hybrid_search_equipment,
                Path(runtime.config.processed_dir),
                equipment_id,
                paths,
                query,
                base_url=runtime.config.retrieval_embedding_url,
                model=runtime.config.retrieval_embedding_model,
                query_prefix=runtime.config.retrieval_embedding_query_prefix,
                document_prefix=runtime.config.retrieval_embedding_document_prefix,
                timeout_seconds=runtime.config.retrieval_embedding_timeout_seconds,
                manual_types=manual_types,
                candidate_depth=runtime.config.retrieval_hybrid_candidate_depth,
                rrf_k=runtime.config.retrieval_hybrid_rrf_k,
                top_k=top_k,
            )
        except HybridIndexNotReady as exc:
            raise HTTPException(status_code=409, detail=f"Machine hybrid index is not ready. Use ‘Build machine embeddings’ first. {exc}") from exc
        except EmbeddingServiceError as exc:
            raise HTTPException(status_code=503, detail=f"Embedding service unavailable; no automatic lexical fallback was used. {exc}") from exc
        scope["retrieval_mode"] = "hybrid"
        scope["hybrid"] = hybrid_meta
    else:
        results = await asyncio.to_thread(search_indices, paths, query, top_k=top_k)
        scope["retrieval_mode"] = "lexical"
        scope["hybrid"] = None
    return results, len(paths), selected, scope


@app.post("/api/retrieval/search")
async def retrieval_search(update: RetrievalSearchRequest) -> dict:
    results, searched_books, selected_books, scope = await _retrieval_results_for_question(
        update.query, update.postprocess_job_id, update.equipment_id, update.top_k, update.retrieval_mode
    )
    visual_results = await _visual_results_for_question(
        update.query, results, update.postprocess_job_id, min(5, update.top_k),
        books=selected_books, equipment_scoped=scope.get("mode") == "equipment",
    )
    return {
        "query": update.query,
        "top_k": update.top_k,
        "book_filter": update.postprocess_job_id,
        "equipment_filter": update.equipment_id,
        "retrieval_scope": scope,
        "results": results,
        "visual_results": visual_results,
        "searched_books": searched_books,
        "generator_used": False,
        "llm_calls": 0,
    }


@app.post("/api/retrieval/prompt-bundle")
async def retrieval_prompt_bundle(update: RetrievalPromptExportRequest) -> dict:
    top_k = min(int(update.top_k), int(runtime.config.rag_answer_max_sources))
    candidate_k = min(20, max(top_k, top_k * 4))
    results, searched_books, selected_books, scope = await _retrieval_results_for_question(
        update.query, update.postprocess_job_id, update.equipment_id, candidate_k, update.retrieval_mode
    )
    visual_results = await _visual_results_for_question(
        update.query, results, update.postprocess_job_id, min(5, candidate_k),
        books=selected_books, equipment_scoped=scope.get("mode") == "equipment",
    )
    allowed_job_ids = {int(row.get("postprocess_job_id") or 0) for row in selected_books} if scope.get("mode") == "equipment" else None
    sources, evidence_scope = prepare_generation_sources(
        results, update.query, visual_results=visual_results, max_sources=top_k,
        allowed_job_ids=allowed_job_ids, equipment_name=scope.get("equipment_name"),
    )
    if not sources:
        raise HTTPException(status_code=404, detail="No retrieved source chunks are available to export.")
    return {
        "query": update.query,
        "book_filter": update.postprocess_job_id,
        "equipment_filter": update.equipment_id,
        "retrieval_scope": scope,
        "searched_books": searched_books,
        "sources": sources,
        "evidence_scope": evidence_scope,
        "prompt": build_portable_prompt(update.query, sources),
        "generator_used": False,
        "llm_calls": 0,
    }


async def _execute_retrieval_generation(update: RetrievalGenerateRequest) -> dict:
    top_k = min(int(update.top_k), int(runtime.config.rag_answer_max_sources))
    candidate_k = min(20, max(top_k, top_k * 4))
    results, searched_books, selected_books, scope = await _retrieval_results_for_question(
        update.query, update.postprocess_job_id, update.equipment_id, candidate_k, update.retrieval_mode
    )
    visual_results = await _visual_results_for_question(
        update.query, results, update.postprocess_job_id, min(5, candidate_k),
        books=selected_books, equipment_scoped=scope.get("mode") == "equipment",
    )
    allowed_job_ids = {int(row.get("postprocess_job_id") or 0) for row in selected_books} if scope.get("mode") == "equipment" else None
    sources, evidence_scope = prepare_generation_sources(
        results, update.query, visual_results=visual_results, max_sources=top_k,
        allowed_job_ids=allowed_job_ids, equipment_name=scope.get("equipment_name"),
    )
    if not sources:
        raise HTTPException(status_code=404, detail="No retrieved source chunks are available for answer generation.")
    try:
        async with runtime.stage2b_worker.device_lock(update.provider):
            generated = await generate_grounded_answer(
                update.provider, runtime.config, update.query, sources, quota_guard=runtime.groq_quota
            )
    except asyncio.CancelledError:
        # Cancellation is a real backend cancellation. It propagates through
        # httpx/stream readers and releases the physical-provider lock.
        raise
    except CloudQuotaPausedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        if exc.__class__.__module__.startswith("httpx"):
            raise HTTPException(status_code=502, detail=f"Generator request failed: {exc}") from exc
        raise
    return {
        "query": update.query,
        "book_filter": update.postprocess_job_id,
        "equipment_filter": update.equipment_id,
        "retrieval_scope": scope,
        "searched_books": searched_books,
        "sources": sources,
        "evidence_scope": evidence_scope,
        **generated,
        "generator_used": True,
        "llm_calls": 1,
    }


@app.post("/api/retrieval/generate")
async def retrieval_generate(update: RetrievalGenerateRequest) -> dict:
    # Backward-compatible synchronous endpoint used by older clients/tests.
    return await _execute_retrieval_generation(update)


def _prune_generation_jobs() -> None:
    now = time.time()
    removable = [
        key for key, value in _generation_jobs.items()
        if value.get("status") in {"completed", "failed", "cancelled"}
        and now - float(value.get("finished_at_epoch") or value.get("created_at_epoch") or now) > 1800
    ]
    for key in removable:
        _generation_jobs.pop(key, None)
    if len(_generation_jobs) > 100:
        ordered = sorted(_generation_jobs.items(), key=lambda item: float(item[1].get("created_at_epoch") or 0))
        for key, value in ordered:
            if len(_generation_jobs) <= 100:
                break
            if value.get("status") in {"completed", "failed", "cancelled"}:
                _generation_jobs.pop(key, None)


async def _run_generation_job(request_id: str, update: RetrievalGenerateRequest) -> None:
    state = _generation_jobs[request_id]
    state["status"] = "running"
    state["started_at_epoch"] = time.time()
    try:
        state["result"] = await _execute_retrieval_generation(update)
        state["status"] = "completed"
    except asyncio.CancelledError:
        state["status"] = "cancelled"
        state["error"] = "Generation cancelled by user."
    except HTTPException as exc:
        state["status"] = "failed"
        state["status_code"] = int(exc.status_code)
        state["error"] = str(exc.detail)
    except Exception as exc:
        state["status"] = "failed"
        state["status_code"] = 500
        state["error"] = f"Answer generation failed: {exc}"
    finally:
        state["finished_at_epoch"] = time.time()
        state["task"] = None


@app.post("/api/retrieval/generate/start")
async def retrieval_generate_start(update: RetrievalGenerateRequest) -> dict:
    async with _generation_jobs_lock:
        _prune_generation_jobs()
        request_id = uuid.uuid4().hex
        state = {
            "request_id": request_id,
            "status": "queued",
            "created_at_epoch": time.time(),
            "started_at_epoch": None,
            "finished_at_epoch": None,
            "provider": update.provider,
            "query": update.query,
            "result": None,
            "error": None,
            "status_code": None,
            "task": None,
        }
        _generation_jobs[request_id] = state
        task = asyncio.create_task(_run_generation_job(request_id, update), name=f"rag-generation-{request_id[:8]}")
        state["task"] = task
    return {"request_id": request_id, "status": "queued"}


@app.get("/api/retrieval/generate/status/{request_id}")
async def retrieval_generate_status(request_id: str) -> dict:
    state = _generation_jobs.get(request_id)
    if not state:
        raise HTTPException(status_code=404, detail="Generation request was not found or has expired.")
    now = time.time()
    started = float(state.get("started_at_epoch") or state.get("created_at_epoch") or now)
    payload = {
        key: value for key, value in state.items()
        if key not in {"task"}
    }
    payload["elapsed_seconds"] = max(0.0, (float(state.get("finished_at_epoch") or now) - started))
    return payload


@app.post("/api/retrieval/generate/cancel/{request_id}")
async def retrieval_generate_cancel(request_id: str) -> dict:
    state = _generation_jobs.get(request_id)
    if not state:
        raise HTTPException(status_code=404, detail="Generation request was not found or has expired.")
    if state.get("status") in {"completed", "failed", "cancelled"}:
        return {"request_id": request_id, "status": state.get("status"), "cancelled": state.get("status") == "cancelled"}
    task = state.get("task")
    state["status"] = "cancelling"
    if isinstance(task, asyncio.Task) and not task.done():
        task.cancel()
        await asyncio.sleep(0)
    return {"request_id": request_id, "status": state.get("status"), "cancelled": True}



def _chunk_browser_score(row: dict, query: str) -> float:
    query = str(query or "").strip().lower()
    if not query:
        return 1.0
    chunk_id = str(row.get("chunk_id") or "").lower()
    text = str(row.get("text") or "")
    headings = " ".join(str(value) for value in (row.get("headings") or []))
    source = str(row.get("source_filename") or "")
    haystack = f"{chunk_id}\n{headings}\n{source}\n{text}".lower()
    if query == chunk_id:
        return 10000.0
    score = 0.0
    if query in haystack:
        score += 100.0
    tokens = [value for value in re.findall(r"[a-z0-9_+./~:\-]+", query) if len(value) > 1]
    if not tokens:
        return score
    matched = 0
    for token in tokens:
        count = haystack.count(token)
        if count:
            matched += 1
            score += min(12.0, 2.0 + count)
            if token in chunk_id:
                score += 18.0
            if token in headings.lower():
                score += 5.0
    if matched == 0:
        return 0.0
    score += 8.0 * (matched / len(tokens))
    return score


def _chunk_browser_row(row: dict) -> dict:
    pages = []
    for value in row.get("page_numbers") or []:
        try:
            pages.append(int(value))
        except (TypeError, ValueError):
            continue
    pages = sorted(set(pages))
    return {
        "postprocess_job_id": int(row.get("postprocess_job_id") or 0),
        "result_dir": row.get("result_dir"),
        "source_filename": row.get("source_filename"),
        "chunk_id": row.get("chunk_id"),
        "chunk_index": row.get("chunk_index"),
        "text": row.get("text") or "",
        "headings": row.get("headings") or [],
        "page_numbers": pages,
        "doc_items": row.get("doc_items") or [],
        "num_tokens": row.get("num_tokens"),
        "num_tokens_estimated": bool(row.get("num_tokens_estimated")),
        "content_type": row.get("content_type") or "prose",
        "table_related": bool(row.get("table_related")),
        "quality_score": row.get("quality_score"),
        "warnings": row.get("warnings") or [],
        "retrieval_evidence_type": row.get("retrieval_evidence_type") or "canonical_chunk",
        "stitched_table": bool(row.get("stitched_table")),
        "table_ref": row.get("table_ref"),
        "source_chunk_ids": row.get("source_chunk_ids") or [],
        "table_group_chunk_ids": row.get("table_group_chunk_ids") or [],
        "stage2c_correction_count": int(row.get("stage2c_correction_count") or 0),
        "vision_enrichment_count": int(row.get("vision_enrichment_count") or 0),
    }


@app.get("/api/chunks/browse")
async def chunk_browser(
    postprocess_job_id: int | None = None,
    equipment_id: str | None = None,
    query: str = "",
    chunk_id: str = "",
    source_job_id: int | None = None,
    page_number: int | None = None,
    content_type: str = "",
    offset: int = 0,
    limit: int = 40,
) -> dict:
    try:
        _require_exact_retrieval_scope(postprocess_job_id, equipment_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    offset = max(0, int(offset))
    limit = max(1, min(100, int(limit)))
    books = await _retrieval_books()
    selected: list[dict] = []
    scope: dict
    if postprocess_job_id is not None:
        selected = [row for row in books if int(row.get("postprocess_job_id") or 0) == int(postprocess_job_id)]
        if not selected:
            raise HTTPException(status_code=404, detail="Book with Stage 3 chunks was not found.")
        scope = {"mode": "single_book", "postprocess_job_id": int(postprocess_job_id)}
    else:
        equipment, selected, _, _, _ = _equipment_scope_context(books, str(equipment_id))
        scope = {
            "mode": "equipment",
            "equipment_id": str(equipment_id),
            "equipment_name": equipment.get("name"),
            "manual_count": len(selected),
        }
    paths = _retrieval_index_paths(selected)
    if not paths:
        raise HTTPException(status_code=409, detail="Stage 3 retrieval chunks are not available for this scope.")

    wanted_chunk = str(chunk_id or "").strip().lower()
    wanted_type = str(content_type or "").strip().lower()
    q = str(query or "").strip()
    ranked: list[tuple[float, dict]] = []
    for path in paths:
        for row in _load_index(path):
            try:
                row_job = int(row.get("postprocess_job_id") or 0)
            except (TypeError, ValueError):
                row_job = 0
            if source_job_id is not None and row_job != int(source_job_id):
                continue
            if page_number is not None:
                row_pages = set()
                for value in row.get("page_numbers") or []:
                    try:
                        row_pages.add(int(value))
                    except (TypeError, ValueError):
                        pass
                if int(page_number) not in row_pages:
                    continue
            if wanted_type and str(row.get("content_type") or "").strip().lower() != wanted_type:
                continue
            if wanted_chunk and str(row.get("chunk_id") or "").strip().lower() != wanted_chunk:
                continue
            score = _chunk_browser_score(row, q)
            if q and score <= 0:
                continue
            ranked.append((score, row))

    ranked.sort(key=lambda item: (
        -item[0],
        str(item[1].get("source_filename") or "").lower(),
        int(item[1].get("chunk_index") or 0),
    ))
    total = len(ranked)
    rows = [_chunk_browser_row(row) for _, row in ranked[offset:offset + limit]]
    manuals = [{
        "postprocess_job_id": int(row.get("postprocess_job_id") or 0),
        "source_filename": row.get("source_filename"),
        "result_dir": row.get("result_dir"),
    } for row in selected]
    return {
        "scope": scope,
        "query": q,
        "chunk_id": chunk_id or None,
        "total": total,
        "offset": offset,
        "limit": limit,
        "results": rows,
        "manuals": manuals,
        "page_filter": page_number,
        "content_type": wanted_type or None,
        "read_only": True,
        "model_calls": 0,
    }


@app.post("/api/retrieval/follow-reference")
async def retrieval_follow_reference(update: RetrievalFollowReferenceRequest) -> dict:
    books = await _retrieval_books()
    selected = [row for row in books if int(row.get("postprocess_job_id") or 0) == int(update.postprocess_job_id)]
    if not selected:
        raise HTTPException(status_code=404, detail="Referenced book with Stage 3 chunks was not found.")
    paths = _retrieval_index_paths(selected, update.postprocess_job_id)
    if not paths:
        raise HTTPException(status_code=409, detail="Prepare the retrieval index first.")
    results = await asyncio.to_thread(
        follow_reference, paths, title=update.title.strip(), section=update.section.strip(), parent_query=update.query.strip(), top_k=update.top_k
    )
    return {
        "postprocess_job_id": update.postprocess_job_id,
        "title": update.title.strip(),
        "section": update.section.strip(),
        "query": update.query.strip(),
        "scope": "same_book",
        "results": results,
        "generator_used": False,
        "llm_calls": 0,
    }


@app.get("/api/retrieval/benchmark")
async def retrieval_benchmark_get() -> dict:
    payload = load_benchmark(Path(runtime.config.processed_dir))
    payload["last_result"] = load_benchmark_result(Path(runtime.config.processed_dir))
    return payload


@app.post("/api/retrieval/benchmark")
async def retrieval_benchmark_add(update: RetrievalBenchmarkAddRequest) -> dict:
    result = dict(update.result or {})
    if not result.get("chunk_id"):
        raise HTTPException(status_code=422, detail="Choose a retrieval result as the expected source.")
    item = add_benchmark_item(
        Path(runtime.config.processed_dir), query=update.query, result=result, note=update.note,
        expected_equipment_id=update.equipment_id,
    )
    return {"saved": True, "item": item}


@app.delete("/api/retrieval/benchmark/{item_id}")
async def retrieval_benchmark_delete(item_id: str) -> dict:
    if not delete_benchmark_item(Path(runtime.config.processed_dir), item_id):
        raise HTTPException(status_code=404, detail="Benchmark case not found.")
    return {"deleted": True, "id": item_id}



def _chunk_matches_query(row: dict, query: str) -> bool:
    terms = [term for term in re.split(r"\s+", str(query or "").strip().lower()) if term]
    if not terms:
        return True
    haystack = "\n".join([
        str(row.get("chunk_id") or ""),
        str(row.get("source_filename") or ""),
        " ".join(str(item) for item in (row.get("headings") or [])),
        str(row.get("text") or ""),
    ]).lower()
    return all(term in haystack for term in terms)


def _chunk_public_row(row: dict, *, include_text: bool = True) -> dict:
    output = {key: value for key, value in row.items() if not str(key).startswith("_")}
    text = str(output.get("text") or "")
    if not include_text:
        output["text_preview"] = text[:420] + ("…" if len(text) > 420 else "")
        output.pop("text", None)
    return output


@app.get("/api/chunks")
async def chunk_viewer_search(
    equipment_id: str | None = None,
    postprocess_job_id: int | None = None,
    q: str = "",
    page: int | None = None,
    limit: int = 80,
    offset: int = 0,
) -> dict:
    try:
        _require_exact_retrieval_scope(postprocess_job_id, equipment_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    books = await _retrieval_books()
    scope: dict
    selected: list[dict]
    if equipment_id:
        equipment, selected, paths, _manual_types, _hybrid = _equipment_scope_context(books, equipment_id)
        scope = {"mode": "equipment", "equipment_id": equipment_id, "equipment_name": equipment.get("name")}
    else:
        selected = [row for row in books if int(row.get("postprocess_job_id") or 0) == int(postprocess_job_id)]
        if not selected:
            raise HTTPException(status_code=404, detail="Book was not found.")
        if not selected[0].get("index_ready"):
            raise HTTPException(status_code=409, detail="This book does not have a current Stage 3 retrieval index yet.")
        paths = _retrieval_index_paths(selected)
        scope = {"mode": "single_book", "postprocess_job_id": int(postprocess_job_id), "source_filename": selected[0].get("source_filename")}

    rows: list[dict] = []
    for path in paths:
        rows.extend(_load_index(path))
    filtered: list[dict] = []
    for row in rows:
        pages = [int(value) for value in (row.get("page_numbers") or []) if isinstance(value, (int, float)) or str(value).isdigit()]
        if page is not None and int(page) not in pages:
            continue
        if not _chunk_matches_query(row, q):
            continue
        filtered.append(row)
    filtered.sort(key=lambda row: (
        str(row.get("source_filename") or "").lower(),
        int(row.get("chunk_index") or 0),
        str(row.get("chunk_id") or ""),
    ))
    window = filtered[offset: offset + limit]
    return {
        "scope": scope,
        "query": q,
        "page": page,
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "items": [_chunk_public_row(row, include_text=False) for row in window],
    }


@app.get("/api/chunks/{postprocess_job_id}/{chunk_id}")
async def chunk_viewer_detail(postprocess_job_id: int, chunk_id: str) -> dict:
    books = await _retrieval_books()
    book = next((row for row in books if int(row.get("postprocess_job_id") or 0) == int(postprocess_job_id)), None)
    if not book:
        raise HTTPException(status_code=404, detail="Book was not found.")
    if not book.get("index_ready"):
        raise HTTPException(status_code=409, detail="This book does not have a current Stage 3 retrieval index yet.")
    path = Path(runtime.config.processed_dir) / str(book["result_dir"]) / "retrieval_index.jsonl"
    rows = _load_index(path)
    index = next((idx for idx, row in enumerate(rows) if str(row.get("chunk_id") or "") == str(chunk_id)), None)
    if index is None:
        raise HTTPException(status_code=404, detail="Chunk was not found in the current retrieval index.")
    row = _chunk_public_row(rows[index], include_text=True)
    pages = [int(value) for value in (row.get("page_numbers") or []) if isinstance(value, (int, float)) or str(value).isdigit()]
    row["page_urls"] = [f"/api/postprocess/jobs/{postprocess_job_id}/source-page/{page}" for page in pages]
    previous = rows[index - 1] if index > 0 else None
    following = rows[index + 1] if index + 1 < len(rows) else None
    return {
        "chunk": row,
        "previous": {"chunk_id": previous.get("chunk_id"), "postprocess_job_id": postprocess_job_id} if previous else None,
        "next": {"chunk_id": following.get("chunk_id"), "postprocess_job_id": postprocess_job_id} if following else None,
    }


def _benchmark_equipment_paths(books: list[dict]) -> dict[str, list[Path]]:
    catalog = equipment_catalog(Path(runtime.config.processed_dir), books)
    mapping: dict[str, list[Path]] = {}
    for equipment in catalog.get("equipment") or []:
        equipment_id = str(equipment.get("equipment_id") or "")
        selected = resolve_equipment_books(Path(runtime.config.processed_dir), books, equipment_id)
        if not equipment_id or not selected or not all(bool(row.get("index_ready")) for row in selected):
            continue
        mapping[equipment_id] = _retrieval_index_paths(selected)
    return mapping


def _benchmark_case_equipment_id(item: dict, books: list[dict]) -> str | None:
    explicit = str(item.get("expected_equipment_id") or "").strip()
    if explicit:
        return explicit
    expected_jobs: set[int] = set()
    for source in item.get("acceptable_sources") or []:
        try:
            expected_jobs.add(int(source.get("postprocess_job_id") or 0))
        except (TypeError, ValueError):
            pass
    try:
        expected_jobs.add(int(item.get("expected_postprocess_job_id") or 0))
    except (TypeError, ValueError):
        pass
    expected_jobs.discard(0)
    catalog = equipment_catalog(Path(runtime.config.processed_dir), books)
    owners = {
        str(group.get("equipment_id") or "")
        for group in catalog.get("equipment") or []
        if any(int(manual.get("postprocess_job_id") or 0) in expected_jobs and manual.get("active_for_rag", True) for manual in group.get("manuals") or [])
    }
    return next(iter(owners)) if len(owners) == 1 else None


@app.post("/api/retrieval/benchmark/run")
async def retrieval_benchmark_run() -> dict:
    books = await _retrieval_books()
    paths = _retrieval_index_paths(books)
    if not paths:
        raise HTTPException(status_code=409, detail="Prepare the retrieval index first.")
    equipment_paths = _benchmark_equipment_paths(books)
    payload = load_benchmark(Path(runtime.config.processed_dir))
    case_equipment_ids = {
        str(item.get("id") or ""): equipment_id
        for item in (payload.get("items") or [])
        if (equipment_id := _benchmark_case_equipment_id(item, books))
    }
    return await asyncio.to_thread(
        run_benchmark, Path(runtime.config.processed_dir), paths, top_k=5,
        equipment_index_paths=equipment_paths, case_equipment_ids=case_equipment_ids,
    )


@app.post("/api/retrieval/benchmark/run-hybrid")
async def retrieval_benchmark_run_hybrid() -> dict:
    if not runtime.config.retrieval_hybrid_enabled:
        raise HTTPException(status_code=409, detail="Hybrid retrieval is disabled in config.yaml.")
    books = await _retrieval_books()
    payload = load_benchmark(Path(runtime.config.processed_dir))
    items = list(payload.get("items") or [])
    if not items:
        return {"schema":"docling-retrieval-hybrid-benchmark-result/v1", "cases":0, "eligible_cases":0, "skipped_cases":0, "details":[]}
    details: list[dict] = []
    ranks: list[int | None] = []
    skipped = 0
    for item in items:
        equipment_id = _benchmark_case_equipment_id(item, books)
        if not equipment_id:
            skipped += 1
            details.append({"id":item.get("id"), "query":item.get("query"), "rank":None, "hit":False, "skipped":"no_unique_machine_scope"})
            continue
        try:
            _equipment, _selected, paths, manual_types, hybrid_status = _equipment_scope_context(books, equipment_id)
        except HTTPException as exc:
            skipped += 1
            details.append({"id":item.get("id"), "query":item.get("query"), "equipment_id":equipment_id, "rank":None, "hit":False, "skipped":str(exc.detail)})
            continue
        if not hybrid_status.get("ready"):
            skipped += 1
            details.append({"id":item.get("id"), "query":item.get("query"), "equipment_id":equipment_id, "rank":None, "hit":False, "skipped":"machine_embedding_not_ready"})
            continue
        try:
            results, meta = await asyncio.to_thread(
                hybrid_search_equipment, Path(runtime.config.processed_dir), equipment_id, paths, str(item.get("query") or ""),
                base_url=runtime.config.retrieval_embedding_url, model=runtime.config.retrieval_embedding_model,
                query_prefix=runtime.config.retrieval_embedding_query_prefix, document_prefix=runtime.config.retrieval_embedding_document_prefix,
                timeout_seconds=runtime.config.retrieval_embedding_timeout_seconds, manual_types=manual_types,
                candidate_depth=runtime.config.retrieval_hybrid_candidate_depth, rrf_k=runtime.config.retrieval_hybrid_rrf_k, top_k=10,
            )
        except (EmbeddingServiceError, HybridIndexNotReady) as exc:
            raise HTTPException(status_code=503, detail=f"Fresh hybrid benchmark stopped because the embedding runtime/index is unavailable: {exc}") from exc
        rank = benchmark_expected_rank(results, item)
        ranks.append(rank)
        details.append({
            "id":item.get("id"), "query":item.get("query"), "equipment_id":equipment_id,
            "rank":rank, "hit":rank is not None, "top_result":results[0] if results else None,
            "query_embed_ms":meta.get("query_embed_ms"), "semantic_intent":meta.get("semantic_intent"),
        })
    total = len(ranks)
    def rate(limit: int) -> float:
        return round(100.0 * sum(rank is not None and rank <= limit for rank in ranks) / total, 2) if total else 0.0
    result = {
        "schema":"docling-retrieval-hybrid-benchmark-result/v1",
        "cases":len(items), "eligible_cases":total, "skipped_cases":skipped,
        "top1_percent":rate(1), "top3_percent":rate(3), "top5_percent":rate(5), "top10_percent":rate(10),
        "mrr":round(sum((1.0 / rank) if rank else 0.0 for rank in ranks) / total, 5) if total else 0.0,
        "model":runtime.config.retrieval_embedding_model, "generated_at_epoch":time.time(), "details":details,
    }
    out = Path(runtime.config.processed_dir) / "retrieval_benchmark_hybrid_result.json"
    await asyncio.to_thread(_atomic_write_json, out, result)
    return result


@app.post("/api/stage3/books/{postprocess_job_id}/build")
async def stage3_build_book(postprocess_job_id: int) -> dict:
    try:
        return await runtime.stage3_builder.start(postprocess_job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/stage3/books/{postprocess_job_id}/status")
async def stage3_book_status(postprocess_job_id: int) -> dict:
    job = await runtime.postprocess_store.get_job(postprocess_job_id)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
    state = runtime.stage3_builder.state_for(postprocess_job_id)
    if state:
        return {**state, "chunks_available": (result_dir / "chunks.jsonl").is_file()}
    status_path = result_dir / "stage3_chunking.json"
    if status_path.is_file():
        try:
            payload = await asyncio.to_thread(_load_json_file, status_path)
            payload["chunks_available"] = (result_dir / "chunks.jsonl").is_file()
            return payload
        except (OSError, json.JSONDecodeError):
            pass
    return {"postprocess_job_id": postprocess_job_id, "status": "not_built", "chunks_available": False}


@app.get("/api/postprocess/jobs/{job_id}/artifact/{name}")
async def postprocess_artifact(job_id: int, name: str):
    safe_name = Path(name).name
    if safe_name != name or safe_name not in {
        "source_manifest.json", "integrity.json", "coverage.json", "profile.json", "diagnostics.json",
        "routes.json", "correction_ledger.json", "chunk_overlays.jsonl", "stage2c_backfill.json", "summary.json",
        "stage3_chunking.json", "chunks.jsonl", "retrieval_index.jsonl", "table_evidence.jsonl", "retrieval_quality.json"
    }:
        raise HTTPException(status_code=404, detail="Post-process artifact not found.")
    jobs = await runtime.postprocess_store.list_jobs(limit=500)
    job = next((row for row in jobs if row.get("id") == job_id), None)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    path = Path(runtime.config.processed_dir) / Path(job["result_dir"]).name / safe_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Post-process artifact not found.")
    return FileResponse(path, filename=safe_name)


def _human_review_entries(result_dir: Path) -> list[dict]:
    """Return current text-correction candidates using the same ledger state as Stage 2C/Telegram."""
    ledger = _load_json_file(result_dir / "correction_ledger.json")
    entries: list[dict] = []
    for entry in ledger.get("entries") or []:
        if entry.get("status") == "superseded" or entry.get("entry_type") != "text_correction":
            continue
        if str(entry.get("verification_verdict") or "").upper() not in {"LIKELY_CORRUPT", "UNCERTAIN"}:
            continue
        entries.append({
            "entry_id": entry.get("entry_id"),
            "route_id": entry.get("route_id"),
            "page": entry.get("page"),
            "source_index": entry.get("source_index"),
            "source_type": entry.get("source_type") or "text",
            "table_index": entry.get("table_index"),
            "cell_index": entry.get("cell_index"),
            "row_start": entry.get("row_start"),
            "row_end": entry.get("row_end"),
            "col_start": entry.get("col_start"),
            "col_end": entry.get("col_end"),
            "original_text": entry.get("original_text") or "",
            "proposed_text": entry.get("proposed_text") or "",
            "status": entry.get("status"),
            "status_reason": entry.get("status_reason") or entry.get("reason"),
            "human_verified": bool(entry.get("human_verified")),
            "confidence": (entry.get("verification") or {}).get("confidence"),
            "reason_code": (entry.get("verification") or {}).get("reason_code"),
            "verification_verdict": entry.get("verification_verdict"),
            "oneplus_crosscheck": entry.get("oneplus_crosscheck"),
            "manual_crosschecks": entry.get("manual_crosschecks") or [],
        })
    entries.sort(key=lambda item: (bool(item.get("human_verified")), int(item.get("page") or 0), str(item.get("route_id") or "")))
    return entries


def _human_review_state_matches(entry: dict, state: str) -> bool:
    state = str(state or "all").lower()
    status = str(entry.get("status") or "").lower()
    human = bool(entry.get("human_verified"))
    if state in {"", "all"}:
        return True
    if state == "needs_review":
        return not human
    if state == "reviewed":
        return human
    if state == "applied":
        return status == "applied"
    if state == "original_kept":
        return status == "rejected"
    if state == "pending":
        return status in {"pending", "proposed"}
    return True


@app.get("/api/postprocess/human-review")
async def human_review_queue(
    job_id: int | None = None,
    source_type: str | None = None,
    reason: str | None = None,
    state: str = "all",
) -> dict:
    """Global one-by-one Web review queue over the authoritative correction ledgers."""
    jobs = await runtime.postprocess_store.list_jobs(limit=2000)
    all_entries: list[dict] = []
    for job in jobs:
        if not job.get("result_dir"):
            continue
        result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
        book = job.get("source_filename") or job.get("output_filename") or f"Book {job.get('id')}"
        for entry in await asyncio.to_thread(_human_review_entries, result_dir):
            all_entries.append({**entry, "postprocess_job_id": int(job["id"]), "book": book})

    books = []
    seen_books: set[int] = set()
    reasons: set[str] = set()
    types: set[str] = set()
    for entry in all_entries:
        jid = int(entry["postprocess_job_id"])
        if jid not in seen_books:
            books.append({"postprocess_job_id": jid, "book": entry.get("book") or f"Book {jid}"})
            seen_books.add(jid)
        reason_value = str(entry.get("reason_code") or entry.get("status_reason") or entry.get("verification_verdict") or "").strip()
        if reason_value:
            reasons.add(reason_value)
        if entry.get("source_type"):
            types.add(str(entry["source_type"]))

    filtered = []
    for entry in all_entries:
        if job_id is not None and int(entry.get("postprocess_job_id") or 0) != int(job_id):
            continue
        if source_type and str(entry.get("source_type") or "") != source_type:
            continue
        entry_reason = str(entry.get("reason_code") or entry.get("status_reason") or entry.get("verification_verdict") or "")
        if reason and entry_reason != reason:
            continue
        if not _human_review_state_matches(entry, state):
            continue
        filtered.append(entry)

    filtered.sort(key=lambda item: (bool(item.get("human_verified")), str(item.get("book") or "").lower(), int(item.get("page") or 0), str(item.get("route_id") or "")))
    books.sort(key=lambda item: str(item.get("book") or "").lower())
    return {
        "schema": "docling-human-review-queue/v1",
        "total": len(all_entries),
        "total_filtered": len(filtered),
        "entries": filtered,
        "facets": {"books": books, "source_types": sorted(types), "reasons": sorted(reasons)},
    }


@app.get("/api/postprocess/jobs/{job_id}/human-review")
async def human_review_status(job_id: int) -> dict:
    job = await runtime.postprocess_store.get_job(job_id)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
    summary = await asyncio.to_thread(
        human_review_summary, result_dir, require_human=bool(runtime.config.stage2c_require_human_review)
    )
    entries = await asyncio.to_thread(_human_review_entries, result_dir)
    conversion = await runtime.postprocess_store.get_conversion_job(int(job.get("conversion_job_id") or job_id))
    return {**summary, "book": (conversion or {}).get("filename") or job.get("source_filename") or job.get("output_filename"), "entries": entries}


@app.get("/api/postprocess/jobs/{job_id}/corrections/{entry_id}/docling-context")
async def human_correction_docling_context(job_id: int, entry_id: str):
    """Return neighboring raw Docling text for manual OCR reconstruction."""
    job = await runtime.postprocess_store.get_job(job_id)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
    ledger_path = result_dir / "correction_ledger.json"
    manifest_path = result_dir / "source_manifest.json"
    ledger, manifest = await asyncio.gather(
        asyncio.to_thread(_load_json_file, ledger_path),
        asyncio.to_thread(_load_json_file, manifest_path),
    )
    if not ledger or not manifest:
        raise HTTPException(status_code=404, detail="Review source metadata is not available.")

    entry = next((item for item in ledger.get("entries", []) if str(item.get("entry_id")) == entry_id), None)
    if not entry or entry.get("entry_type") != "text_correction":
        raise HTTPException(status_code=404, detail="Text correction entry not found.")
    try:
        expected_page = int(entry.get("page")) if entry.get("page") is not None else None
    except (TypeError, ValueError):
        expected_page = None

    converted_name = Path(str(manifest.get("converted_zip") or job.get("output_filename") or "")).name
    zip_path = Path(runtime.config.output_dir) / converted_name
    if not converted_name or not zip_path.is_file():
        raise HTTPException(status_code=404, detail="Converted Docling ZIP is not available.")
    try:
        if str(entry.get("source_type") or "") == "table_cell":
            try:
                table_index = int(entry.get("table_index"))
                cell_index = int(entry.get("cell_index"))
            except (TypeError, ValueError) as exc:
                raise ValueError("Correction entry has no Docling table/cell index") from exc
            context = await asyncio.to_thread(_docling_table_review_context, zip_path, table_index, cell_index)
        else:
            try:
                source_index = int(entry.get("source_index"))
            except (TypeError, ValueError) as exc:
                raise ValueError("Correction entry has no Docling text index") from exc
            context = await asyncio.to_thread(_docling_review_context, zip_path, source_index, expected_page, 3)
    except (OSError, ValueError, IndexError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=422, detail=f"Could not read Docling review context: {exc}") from exc

    context["entry_id"] = entry_id
    context["ledger_original_text"] = str(entry.get("original_text") or "")
    context["target_matches_ledger"] = (
        context.get("target", {}).get("text", "").strip() == str(entry.get("original_text") or "").strip()
    )
    return context


@app.post("/api/postprocess/jobs/{job_id}/corrections/{entry_id}")
async def update_human_correction(job_id: int, entry_id: str, update: HumanCorrectionUpdate):
    job = await runtime.postprocess_store.get_job(job_id)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    result_dir = Path(runtime.config.processed_dir) / Path(job["result_dir"]).name
    ledger_path = result_dir / "correction_ledger.json"
    ledger = await asyncio.to_thread(_load_json_file, ledger_path)
    if not ledger:
        raise HTTPException(status_code=404, detail="Correction ledger not found.")
    entry = next((item for item in ledger.get("entries", []) if str(item.get("entry_id")) == entry_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Correction entry not found.")
    apply_human_correction_to_entry(entry, text=update.text, action=update.action)
    await asyncio.to_thread(upsert_ledger_entry, result_dir, str(ledger.get("source_zip_sha256") or ""), entry)
    runtime.events.notify("stage2c_human_correction")
    return {"saved": True, "entry_id": entry_id, "status": entry["status"]}


@app.get("/api/postprocess/jobs/{job_id}/source-page/{page}")
async def source_page(job_id: int, page: int, highlight: str | None = None):
    """Render the original PDF page for plain-language human review."""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page number must be positive.")
    job = await runtime.postprocess_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    conversion = await runtime.postprocess_store.get_conversion_job(int(job.get("conversion_job_id") or job_id))
    filename = str((conversion or {}).get("filename") or (conversion or {}).get("source_filename") or "")
    pdf_path = Path(runtime.config.input_dir) / Path(filename).name
    if pdf_path.suffix.lower() != ".pdf" or not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="Original PDF page is not available.")
    refs = [value for value in (highlight or "").split(",") if value.strip()]
    converted_zip: Path | None = None
    if refs and job.get("result_dir"):
        result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
        manifest = await asyncio.to_thread(_load_json_file, result_dir / "source_manifest.json")
        converted_name = Path(str(manifest.get("converted_zip") or job.get("output_filename") or "")).name
        if converted_name:
            converted_zip = Path(runtime.config.output_dir) / converted_name
    try:
        image = await asyncio.to_thread(
            _render_pdf_page_png, pdf_path, page, highlight_refs=refs, converted_zip=converted_zip
        )
        return Response(content=image, media_type="image/png", headers={"Cache-Control": "no-store"})
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="PDF page not found.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not render the PDF page.") from exc


@app.get("/api/oneplus-control/status")
async def oneplus_control_status() -> dict:
    status = await runtime.oneplus_controller.status()
    status["workload"] = await runtime.stage2b_worker.oneplus_workload_status()
    return status


@app.post("/api/oneplus-control/install-script")
async def oneplus_control_install_script() -> dict:
    try:
        return await runtime.oneplus_controller.install_script()
    except OnePlusControlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/oneplus-control/start")
async def oneplus_control_start() -> dict:
    try:
        return await runtime.oneplus_controller.start()
    except OnePlusControlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/oneplus-control/stop")
async def oneplus_control_stop() -> dict:
    try:
        return await runtime.oneplus_controller.stop()
    except OnePlusControlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/oneplus-control/restart")
async def oneplus_control_restart() -> dict:
    try:
        return await runtime.oneplus_controller.restart()
    except OnePlusControlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc




@app.get("/api/postprocess/jobs/{job_id}/picture/{picture_index}")
async def postprocess_picture(job_id: int, picture_index: int):
    if picture_index < 0:
        raise HTTPException(status_code=400, detail="Picture index must be non-negative.")
    job = await runtime.postprocess_store.get_job(job_id)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
    manifest = await asyncio.to_thread(_load_json_file, result_dir / "source_manifest.json")
    converted_name = Path(str(manifest.get("converted_zip") or job.get("output_filename") or "")).name
    zip_path = Path(runtime.config.output_dir) / converted_name
    if not converted_name or not zip_path.is_file():
        raise HTTPException(status_code=404, detail="Converted Docling ZIP is not available.")
    try:
        image_bytes, mime, label = await asyncio.to_thread(_picture_image_from_converted_zip, zip_path, picture_index)
    except (OSError, ValueError, zipfile.BadZipFile, KeyError, IndexError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=image_bytes, media_type=mime, headers={"X-Docling-Picture-Uri": str(label)})


async def _prepare_full_artifact_sweep(postprocess_job_id: int | None = None) -> dict:
    """Prepare sweep rows for completed books without bypassing normal Stage 2B.

    Normal picture routes win. Artifact rows are additive only for technical
    pictures not already covered by a current normal picture-verification route.
    Prepared rows remain unauthorized until the book's normal Text/Vision work
    has completed successfully.
    """
    jobs = await runtime.postprocess_store.list_jobs(limit=500)
    selected = [row for row in jobs if row.get("status") == "completed" and row.get("result_dir")]
    if postprocess_job_id is not None:
        selected = [row for row in selected if int(row.get("id") or 0) == int(postprocess_job_id)]
    created_total = 0
    technical_total = 0
    eligible_total = 0
    skipped_overlap_total = 0
    suppressed_legacy_total = 0
    books = []
    for job in selected:
        result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
        manifest = await asyncio.to_thread(_load_json_file, result_dir / "source_manifest.json")
        converted_name = Path(str(manifest.get("converted_zip") or job.get("output_filename") or "")).name
        zip_path = Path(runtime.config.output_dir) / converted_name
        if not converted_name or not zip_path.is_file():
            books.append({
                "postprocess_job_id": int(job.get("id") or 0),
                "book": job.get("source_filename") or job.get("output_filename"),
                "status": "missing_converted_zip",
                "created": 0,
            })
            continue
        existing_rows = await runtime.stage2b_store.list_book_jobs_raw(int(job.get("id") or 0))
        generation = next((str(row.get("generation") or "") for row in existing_rows if row.get("generation")), "")
        if not generation:
            generation = str(manifest.get("converted_zip_sha256") or f"artifact-sweep-{int(job.get('id') or 0)}")
        prepared = await runtime.stage2b_worker._prepare_artifact_sweep_for_book(job, generation, result_dir, manifest)
        created = int(prepared.get("created") or 0)
        technical = int(prepared.get("technical_visuals") or 0)
        eligible = int(prepared.get("eligible_sweep_jobs") or 0)
        skipped = int(prepared.get("skipped_normal_picture_routes") or 0)
        suppressed = int(prepared.get("suppressed_legacy_overlap_routes") or 0)
        created_total += created
        technical_total += technical
        eligible_total += eligible
        skipped_overlap_total += skipped
        suppressed_legacy_total += suppressed
        books.append({
            "postprocess_job_id": int(job.get("id") or 0),
            "book": job.get("source_filename") or job.get("output_filename"),
            "technical_visuals": technical,
            "eligible_sweep_jobs": eligible,
            "skipped_normal_picture_routes": skipped,
            "suppressed_legacy_overlap_routes": suppressed,
            "created": created,
            "status": "prepared_waiting_for_normal",
        })
    return {
        "technical_visuals": technical_total,
        "eligible_sweep_jobs": eligible_total,
        "skipped_normal_picture_routes": skipped_overlap_total,
        "suppressed_legacy_overlap_routes": suppressed_legacy_total,
        "created": created_total,
        "assigned": {"shared_pool": eligible_total},
        "books": books,
    }


@app.get("/api/stage2b/artifact-audit")
async def stage2b_artifact_audit(postprocess_job_id: int | None = None, limit: int = 5000, technical_only: bool = False) -> dict:
    jobs = await runtime.postprocess_store.list_jobs(limit=500)
    selected = [row for row in jobs if row.get("status") == "completed" and row.get("result_dir")]
    if postprocess_job_id is not None:
        selected = [row for row in selected if int(row.get("id") or 0) == int(postprocess_job_id)]
    items: list[dict] = []
    for job in selected:
        verification_rows = await runtime.stage2b_store.list_book_jobs_raw(int(job.get("id") or 0))
        items.extend(await asyncio.to_thread(
            _artifact_inventory_for_book, job, verification_rows, Path(runtime.config.processed_dir), Path(runtime.config.output_dir)
        ))
    items.sort(key=lambda item: (str(item.get("book") or "").lower(), int(item.get("page") or 0), int(item.get("picture_index") or 0)))
    summary_all = {
        "total_images": len(items),
        "technical_candidates": sum(1 for item in items if item.get("technical_candidate")),
        "routed": sum(1 for item in items if item.get("verification")),
        "verified_completed": sum(1 for item in items if (item.get("verification") or {}).get("status") == "completed"),
        "completed_pi5": sum(1 for item in items if (item.get("verification") or {}).get("status") == "completed" and (item.get("verification") or {}).get("provider") == "pi5"),
        "completed_oneplus": sum(1 for item in items if (item.get("verification") or {}).get("status") == "completed" and (item.get("verification") or {}).get("provider") == "oneplus"),
        "queue_pending": sum(1 for item in items if (item.get("verification") or {}).get("status") == "pending"),
        "queue_processing": sum(1 for item in items if (item.get("verification") or {}).get("status") == "processing"),
        "queue_failed": sum(1 for item in items if (item.get("verification") or {}).get("status") == "failed"),
        "not_routed": sum(1 for item in items if not item.get("verification")),
        "stage2c_applied": sum(1 for item in items if (item.get("downstream") or {}).get("status") == "applied"),
        "stage2c_pending": sum(1 for item in items if (item.get("downstream") or {}).get("status") == "pending"),
        "stage2c_excluded": sum(1 for item in items if (item.get("downstream") or {}).get("status") == "excluded"),
        "rag_eligible_visuals": sum(1 for item in items if (item.get("downstream") or {}).get("rag_eligible") is True),
        "rag_excluded_visuals": sum(1 for item in items if item.get("downstream") and not (item.get("downstream") or {}).get("rag_eligible")),
        "human_review_required": sum(1 for item in items if (
            item.get("downstream")
            and not (item.get("downstream") or {}).get("human_visual_decision")
            and (
                str((item.get("verification") or {}).get("verdict") or "").upper() == "UNCERTAIN"
                or bool((item.get("downstream") or {}).get("unresolved"))
                or str((item.get("downstream") or {}).get("status") or "").lower() == "pending"
            )
        )),
        "human_reviewed": sum(1 for item in items if (item.get("downstream") or {}).get("human_visual_decision")),
    }
    if technical_only:
        items = [item for item in items if item.get("technical_candidate")]
    limited = items[: max(0, min(int(limit), 10000))]
    summary = {**summary_all, "returned": len(limited), "filtered_technical_only": bool(technical_only)}
    return {"schema": "docling-artifact-audit/v1", "summary": summary, "jobs": limited}


@app.post("/api/stage2b/artifact-audit/start-all")
async def stage2b_artifact_audit_start_all(postprocess_job_id: int | None = None) -> dict:
    prepared = await _prepare_full_artifact_sweep(postprocess_job_id)
    # Arm both historical DB lanes. The store-level dependency gate releases
    # only books whose normal Text/Vision routes have all completed. Manual
    # Pause remains authoritative for the physical Pi5/OnePlus workers.
    armed_legacy_pi5 = await runtime.stage2b_store.start_artifact_sweep("pi5")
    armed_vision = await runtime.stage2b_store.start_artifact_sweep("oneplus")
    released = await runtime.stage2b_store.release_ready_artifact_sweeps()
    if armed_legacy_pi5 or armed_vision or released:
        runtime.events.notify("stage2b_artifact_audit_start_all")
    return {
        "accepted": bool(armed_legacy_pi5 or armed_vision or released or prepared.get("created")),
        "prepared": prepared,
        "armed": {
            "total": int(armed_vision + armed_legacy_pi5),
            "historical_oneplus_lane": int(armed_vision),
            "historical_pi5_lane": int(armed_legacy_pi5),
        },
        "authorized": {
            "total": int(released),
            "released_after_normal_complete": int(released),
        },
        "artifact_workers": {
            "pi5": {"paused": bool(runtime.config.stage2b_pi5_paused)},
            "oneplus": {"paused": bool(runtime.config.stage2b_oneplus_paused)},
        },
        "routing": "shared_idle_work_stealing_pi5_oneplus",
        "normal_vision_provider": runtime.config.vision_verifier_provider,
        "raw_docling_immutable": True,
    }


@app.post("/api/stage2b/artifact-audit/retry-failed")
async def stage2b_artifact_audit_retry_failed() -> dict:
    retried_pi5 = await runtime.stage2b_store.retry_failed_artifact_sweep("pi5")
    retried_oneplus = await runtime.stage2b_store.retry_failed_artifact_sweep("oneplus")
    released = await runtime.stage2b_store.release_ready_artifact_sweeps()
    # Keep manual Pause authoritative. Retried rows remain dependency-gated by
    # the normal verification state of their book.
    if retried_pi5 or retried_oneplus or released:
        runtime.events.notify("stage2b_artifact_audit_retry_failed")
    return {
        "accepted": bool(retried_pi5 or retried_oneplus),
        "retried": {"pi5": int(retried_pi5), "oneplus": int(retried_oneplus), "total": int(retried_pi5 + retried_oneplus)},
        "released_after_normal_complete": int(released),
    }


@app.get("/artifact-audit")
async def artifact_audit_page():
    return FileResponse(STATIC_DIR / "artifact-audit.html")


@app.get("/oneplus")
async def oneplus_control_page():
    return FileResponse(STATIC_DIR / "oneplus.html")


@app.get("/verification")
async def verification_page():
    return FileResponse(STATIC_DIR / "verification.html")


@app.get("/text-audit")
async def text_audit_page():
    return FileResponse(STATIC_DIR / "text-audit.html")


@app.get("/vision-audit")
async def vision_audit_page():
    return FileResponse(STATIC_DIR / "vision-audit.html")


@app.get("/retrieval")
async def retrieval_page():
    return FileResponse(STATIC_DIR / "retrieval.html")


@app.get("/chunks")
async def chunks_page():
    return FileResponse(STATIC_DIR / "chunks.html")


@app.get("/quality")
async def quality_page():
    return FileResponse(STATIC_DIR / "quality.html")


@app.get("/events")
async def events() -> StreamingResponse:
    return StreamingResponse(runtime.events.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/queue")
async def conversion_queue():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/book")
async def book_workflow_page():
    return FileResponse(STATIC_DIR / "book.html")


@app.get("/")
async def dashboard():
    return FileResponse(STATIC_DIR / "workflow.html")


@app.get("/errors")
async def error_log():
    return FileResponse(STATIC_DIR / "errors.html")


@app.get("/add-book")
async def add_book_page():
    return FileResponse(STATIC_DIR / "add-book.html")


@app.get("/convert")
async def convert_page():
    return FileResponse(STATIC_DIR / "convert.html")
