from __future__ import annotations

from functools import lru_cache

from contextlib import asynccontextmanager
import asyncio
import json
import re
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
import fitz

from .archive import select_docling_document
from .config import AppConfig, config_path, load_config, save_config
from .database import JobStore
from .docling_client import DoclingApiError, DoclingClient
from .events import EventBroker
from .groq_quota import GroqQuotaGuard
from .manual_options import ConvertUrlRequest, ManualConvertOptions
from .oneplus_control import OnePlusControlError, OnePlusController
from .postprocess import PostprocessWorker
from .postprocess_store import PostprocessStore
from .retrieval import (
    add_benchmark_item,
    delete_benchmark_item,
    load_benchmark,
    load_benchmark_result,
    run_benchmark,
    search_indices,
    follow_reference,
    docling_highlight_rects,
)
from .rag_generation import build_portable_prompt, generate_grounded_answer, prepare_sources
from .groq_quota import CloudQuotaPausedError
from .stage2b import Stage2BWorker
from .stage2c import apply_human_correction_to_entry, human_review_summary, upsert_ledger_entry
from .stage3 import Stage3ChunkBuilder
from .stage2b_store import Stage2BStore
from .worker import ConversionWorker
from .version import APP_VERSION


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




class WatcherAutoRunUpdate(BaseModel):
    enabled: bool


class Stage2BAutoRunUpdate(BaseModel):
    enabled: bool


class VerifierProviderUpdate(BaseModel):
    provider: str = Field(pattern="^(pi5|oneplus|groq)$")





class HumanCorrectionUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    action: str = Field(default="apply", pattern="^(apply|reject|propose)$")




class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    postprocess_job_id: int | None = None


class RetrievalGenerateRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    provider: str = Field(pattern="^(pi5|oneplus|groq)$")
    top_k: int = Field(default=5, ge=1, le=10)
    postprocess_job_id: int | None = None


class RetrievalPromptExportRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=10)
    postprocess_job_id: int | None = None


class RetrievalBenchmarkAddRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    result: dict
    note: str = Field(default="", max_length=1000)


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
        self.stage2b_worker = Stage2BWorker(
            lambda: self.config, self.stage2b_store, self.postprocess_store, self.events, self.groq_quota
        )
        self.oneplus_controller = OnePlusController(lambda: self.config)
        self.stage3_builder = Stage3ChunkBuilder(
            lambda: self.config, self.postprocess_store, self.client, self.events
        )
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
            if status in {"completed", "partial"}:
                return state
            if status == "failed":
                raise RuntimeError(str(state.get("error") or "Stage 2C rebuild failed"))
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
    yield
    await runtime.stop_safety_refresh()
    await runtime.stage3_builder.stop()
    await runtime.stage2b_worker.stop()
    await runtime.postprocess_worker.stop()
    await runtime.worker.stop()


app = FastAPI(title="Docling Auto-Convert", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
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
        row["quality_status"] = coverage.get("status")
        row["quality_display_label"] = coverage.get("display_label")
        row["integrity_status"] = integrity.get("status")
        row["integrity_display_label"] = integrity.get("display_label")
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


@app.get("/api/errors")
async def errors() -> dict:
    return {"jobs": enrich_jobs(await runtime.store.list_jobs(limit=200, failures_only=True))}


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
        "jobs": enrich_postprocess_jobs(await runtime.postprocess_store.list_jobs(limit=50)),
        "processed_dir": runtime.config.processed_dir,
        "external_verifiers_enabled": runtime.config.external_verifiers_enabled,
        "verifiers": runtime.postprocess_worker.verifier_status,
    }


@app.get("/api/documents")
async def documents() -> dict:
    """Unified document library for watcher and converted-folder imports."""
    rows = enrich_postprocess_jobs(await runtime.postprocess_store.list_jobs(limit=500))
    verification_books = {
        int(item["postprocess_job_id"]): item
        for item in await runtime.stage2b_store.list_books()
    }
    for row in rows:
        book = verification_books.get(int(row.get("id") or 0), {})
        row["verification"] = {
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
    return {"documents": rows}


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
                state = json.loads(status_path.read_text(encoding="utf-8"))
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
async def stage2b_text_audit(postprocess_job_id: int | None = None, limit: int = 1000) -> dict:
    """Read-only audit of completed/failed Text verifier source transcriptions."""
    rows = await runtime.stage2b_store.list_results_raw("pi5", limit=max(1, min(int(limit), 5000)))
    if postprocess_job_id is not None:
        rows = [row for row in rows if int(row.get("postprocess_job_id") or 0) == int(postprocess_job_id)]

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

        result_dir_name = Path(str(row.get("result_dir") or "")).name
        downstream = None
        if result_dir_name:
            if result_dir_name not in ledger_cache:
                ledger_path = Path(runtime.config.processed_dir) / result_dir_name / "correction_ledger.json"
                try:
                    ledger_cache[result_dir_name] = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
                except (OSError, json.JSONDecodeError):
                    ledger_cache[result_dir_name] = {}
            entry_id = f"{row.get('generation')}:text:{row.get('route_id')}"
            entry = next((item for item in (ledger_cache[result_dir_name].get("entries") or []) if str(item.get("entry_id")) == entry_id), None)
            if isinstance(entry, dict):
                downstream = {
                    "status": entry.get("status"),
                    "status_reason": entry.get("status_reason"),
                    "entry_type": entry.get("entry_type"),
                    "human_verified": bool(entry.get("human_verified")),
                    "proposed_text": entry.get("proposed_text"),
                    "raw_docling_immutable": entry.get("raw_docling_immutable", True),
                }

        disposition = "failed" if row.get("status") == "failed" else str(correction.get("status") or "verified_original")
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
    }
    return {"schema": "docling-text-verifier-audit/v1", "summary": summary, "jobs": jobs}


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
    rows = await runtime.stage2b_store.list_results_raw("oneplus", limit=max(1, min(int(limit), 5000)))
    if postprocess_job_id is not None:
        rows = [row for row in rows if int(row.get("postprocess_job_id") or 0) == int(postprocess_job_id)]

    ledger_cache: dict[str, dict] = {}
    jobs: list[dict] = []
    for row in rows:
        source = _audit_json(row.get("source_json"))
        request = _audit_json(row.get("request_json"))
        result = _audit_json(row.get("result_json"))
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        crop_audit = result.get("crop_audit") if isinstance(result.get("crop_audit"), list) else []
        result_dir_name = Path(str(row.get("result_dir") or "")).name
        downstream = None
        if result_dir_name:
            if result_dir_name not in ledger_cache:
                ledger_path = Path(runtime.config.processed_dir) / result_dir_name / "correction_ledger.json"
                try:
                    ledger_cache[result_dir_name] = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
                except (OSError, json.JSONDecodeError):
                    ledger_cache[result_dir_name] = {}
            entry_id = f"{row.get('generation')}:vision:{row.get('route_id')}"
            entry = next((item for item in (ledger_cache[result_dir_name].get("entries") or []) if str(item.get("entry_id")) == entry_id), None)
            if isinstance(entry, dict):
                downstream = {
                    "status": entry.get("status"),
                    "status_reason": entry.get("status_reason"),
                    "diagram_category": entry.get("diagram_category"),
                    "generated_summary": entry.get("generated_summary"),
                    "unresolved": entry.get("unresolved"),
                    "raw_docling_immutable": entry.get("raw_docling_immutable", True),
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
    }
    return {"schema": "docling-vision-verifier-audit/v1", "summary": summary, "jobs": jobs}


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
    runtime.events.notify("stage2b_book_manual_started")
    return {"accepted": True, "postprocess_job_id": postprocess_job_id, "authorized_jobs": count}


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
    runtime.events.notify("stage2b_manual_started")
    return {"accepted": True, "target": target, "authorized_jobs": count}


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
        chunks_path = result_dir / "chunks.jsonl"
        if not chunks_path.is_file():
            continue
        quality_path = result_dir / "retrieval_quality.json"
        quality = None
        if quality_path.is_file():
            try:
                quality = json.loads(quality_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                quality = None
        source_name = str(job.get("source_filename") or Path(str(job.get("output_filename") or result_dir.name)).stem)
        if quality and quality.get("source_filename"):
            source_name = str(quality.get("source_filename"))
        output.append({
            "postprocess_job_id": int(job.get("id") or 0),
            "source_filename": source_name,
            "result_dir": result_dir.name,
            "index_ready": (result_dir / "retrieval_index.jsonl").is_file(),
            "quality": quality,
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


@app.get("/api/retrieval/status")
async def retrieval_status() -> dict:
    books = await _retrieval_books()
    searchable = excluded = oversized = 0
    ready = 0
    for book in books:
        quality = book.get("quality") or {}
        if book.get("index_ready"):
            ready += 1
        searchable += int(quality.get("searchable_chunks") or 0)
        excluded += int(quality.get("excluded_chunks") or 0)
        oversized += int(quality.get("oversized_searchable_chunks") or 0)
    return {
        "books": books,
        "books_with_stage3": len(books),
        "books_indexed": ready,
        "searchable_chunks": searchable,
        "excluded_chunks": excluded,
        "oversized_searchable_chunks": oversized,
        "benchmark_cases": len((load_benchmark(Path(runtime.config.processed_dir)).get("items") or [])),
    }


@app.post("/api/retrieval/reindex-all")
async def retrieval_reindex_all() -> dict:
    books = await _retrieval_books()
    results = []
    for book in books:
        result_dir = Path(runtime.config.processed_dir) / str(book["result_dir"])
        try:
            quality = await asyncio.to_thread(
                Stage3ChunkBuilder.refresh_existing_outputs,
                result_dir,
                max_tokens=runtime.config.stage3_chunk_max_tokens,
                repeat_table_header=runtime.config.stage3_table_split_repeat_header,
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


async def _retrieval_results_for_question(query: str, postprocess_job_id: int | None, top_k: int) -> tuple[list[dict], int]:
    books = await _retrieval_books()
    if postprocess_job_id is not None and not any(
        int(row.get("postprocess_job_id") or 0) == int(postprocess_job_id) for row in books
    ):
        raise HTTPException(status_code=404, detail="Book with Stage 3 chunks was not found.")
    paths = _retrieval_index_paths(books, postprocess_job_id)
    if not paths:
        raise HTTPException(status_code=409, detail="Prepare the retrieval index first.")
    results = await asyncio.to_thread(search_indices, paths, query, top_k=top_k)
    return results, len(paths)


@app.post("/api/retrieval/search")
async def retrieval_search(update: RetrievalSearchRequest) -> dict:
    results, searched_books = await _retrieval_results_for_question(update.query, update.postprocess_job_id, update.top_k)
    return {
        "query": update.query,
        "top_k": update.top_k,
        "book_filter": update.postprocess_job_id,
        "results": results,
        "searched_books": searched_books,
        "generator_used": False,
        "llm_calls": 0,
    }


@app.post("/api/retrieval/prompt-bundle")
async def retrieval_prompt_bundle(update: RetrievalPromptExportRequest) -> dict:
    top_k = min(int(update.top_k), int(runtime.config.rag_answer_max_sources))
    results, searched_books = await _retrieval_results_for_question(update.query, update.postprocess_job_id, top_k)
    sources = prepare_sources(results, max_sources=top_k)
    if not sources:
        raise HTTPException(status_code=404, detail="No retrieved source chunks are available to export.")
    return {
        "query": update.query,
        "book_filter": update.postprocess_job_id,
        "searched_books": searched_books,
        "sources": sources,
        "prompt": build_portable_prompt(update.query, sources),
        "generator_used": False,
        "llm_calls": 0,
    }


@app.post("/api/retrieval/generate")
async def retrieval_generate(update: RetrievalGenerateRequest) -> dict:
    top_k = min(int(update.top_k), int(runtime.config.rag_answer_max_sources))
    results, searched_books = await _retrieval_results_for_question(update.query, update.postprocess_job_id, top_k)
    sources = prepare_sources(results, max_sources=top_k)
    if not sources:
        raise HTTPException(status_code=404, detail="No retrieved source chunks are available for answer generation.")
    try:
        async with runtime.stage2b_worker.device_lock(update.provider):
            generated = await generate_grounded_answer(
                update.provider, runtime.config, update.query, sources, quota_guard=runtime.groq_quota
            )
    except CloudQuotaPausedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        # httpx transport/status errors expose a concise useful message without
        # leaking prompts, source text, API keys, or internal stack traces.
        if exc.__class__.__module__.startswith("httpx"):
            raise HTTPException(status_code=502, detail=f"Generator request failed: {exc}") from exc
        raise
    return {
        "query": update.query,
        "book_filter": update.postprocess_job_id,
        "searched_books": searched_books,
        "sources": sources,
        **generated,
        "generator_used": True,
        "llm_calls": 1,
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
        Path(runtime.config.processed_dir), query=update.query, result=result, note=update.note
    )
    return {"saved": True, "item": item}


@app.delete("/api/retrieval/benchmark/{item_id}")
async def retrieval_benchmark_delete(item_id: str) -> dict:
    if not delete_benchmark_item(Path(runtime.config.processed_dir), item_id):
        raise HTTPException(status_code=404, detail="Benchmark case not found.")
    return {"deleted": True, "id": item_id}


@app.post("/api/retrieval/benchmark/run")
async def retrieval_benchmark_run() -> dict:
    books = await _retrieval_books()
    paths = _retrieval_index_paths(books)
    if not paths:
        raise HTTPException(status_code=409, detail="Prepare the retrieval index first.")
    return await asyncio.to_thread(
        run_benchmark, Path(runtime.config.processed_dir), paths, top_k=5
    )


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
            payload = json.loads(status_path.read_text(encoding="utf-8"))
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
        "stage3_chunking.json", "chunks.jsonl", "retrieval_index.jsonl", "retrieval_quality.json"
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


@app.get("/api/postprocess/jobs/{job_id}/human-review")
async def human_review_status(job_id: int) -> dict:
    job = await runtime.postprocess_store.get_job(job_id)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
    summary = human_review_summary(result_dir, require_human=bool(runtime.config.stage2c_require_human_review))
    ledger_path = result_dir / "correction_ledger.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        ledger = {}
    entries = []
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
    return {**summary, "entries": entries}


@app.get("/api/postprocess/jobs/{job_id}/corrections/{entry_id}/docling-context")
async def human_correction_docling_context(job_id: int, entry_id: str):
    """Return neighboring raw Docling text for manual OCR reconstruction."""
    job = await runtime.postprocess_store.get_job(job_id)
    if not job or not job.get("result_dir"):
        raise HTTPException(status_code=404, detail="Post-process job not found.")
    result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
    ledger_path = result_dir / "correction_ledger.json"
    manifest_path = result_dir / "source_manifest.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=404, detail="Review source metadata is not available.")

    entry = next((item for item in ledger.get("entries", []) if str(item.get("entry_id")) == entry_id), None)
    if not entry or entry.get("entry_type") != "text_correction":
        raise HTTPException(status_code=404, detail="Text correction entry not found.")
    try:
        source_index = int(entry.get("source_index"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Correction entry has no Docling text index.")
    try:
        expected_page = int(entry.get("page")) if entry.get("page") is not None else None
    except (TypeError, ValueError):
        expected_page = None

    converted_name = Path(str(manifest.get("converted_zip") or job.get("output_filename") or "")).name
    zip_path = Path(runtime.config.output_dir) / converted_name
    if not converted_name or not zip_path.is_file():
        raise HTTPException(status_code=404, detail="Converted Docling ZIP is not available.")
    try:
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
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=404, detail="Correction ledger not found.")
    entry = next((item for item in ledger.get("entries", []) if str(item.get("entry_id")) == entry_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Correction entry not found.")
    apply_human_correction_to_entry(entry, text=update.text, action=update.action)
    upsert_ledger_entry(result_dir, str(ledger.get("source_zip_sha256") or ""), entry)
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
    try:
        with fitz.open(pdf_path) as pdf:
            if page > len(pdf):
                raise HTTPException(status_code=404, detail="PDF page not found.")
            pdf_page = pdf[page - 1]
            refs = [value for value in (highlight or "").split(",") if value.strip()]
            if refs and job.get("result_dir"):
                result_dir = Path(runtime.config.processed_dir) / Path(str(job["result_dir"])).name
                try:
                    manifest = json.loads((result_dir / "source_manifest.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, TypeError):
                    manifest = {}
                converted_name = Path(str(manifest.get("converted_zip") or job.get("output_filename") or "")).name
                converted_zip = Path(runtime.config.output_dir) / converted_name
                try:
                    rects = await asyncio.to_thread(docling_highlight_rects, converted_zip, refs, page, pdf_page.rect.height)
                except (OSError, ValueError, zipfile.BadZipFile):
                    rects = []
                for rect in rects:
                    clipped = rect & pdf_page.rect
                    if clipped.width > 0.5 and clipped.height > 0.5:
                        pdf_page.draw_rect(clipped, color=(0.86, 0.18, 0.12), width=2.2, overlay=True)
            pix = pdf_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            return Response(content=pix.tobytes("png"), media_type="image/png", headers={"Cache-Control": "no-store"})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not render the PDF page.") from exc


@app.get("/api/oneplus-control/status")
async def oneplus_control_status() -> dict:
    return await runtime.oneplus_controller.status()


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


@app.get("/convert")
async def convert_page():
    return FileResponse(STATIC_DIR / "convert.html")
