from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .config import AppConfig, config_path, load_config, save_config
from .database import JobStore
from .docling_client import DoclingApiError, DoclingClient
from .events import EventBroker
from .manual_options import ConvertUrlRequest, ManualConvertOptions
from .oneplus_control import OnePlusControlError, OnePlusController
from .postprocess import PostprocessWorker
from .postprocess_store import PostprocessStore
from .stage2b import Stage2BWorker
from .stage2b_store import Stage2BStore
from .worker import ConversionWorker


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


class Runtime:
    def __init__(self) -> None:
        self.config_file = config_path()
        self.config: AppConfig = load_config(self.config_file)
        self.store, self.events = JobStore(self.config.database_path), EventBroker()
        self.client = DoclingClient(lambda: self.config)
        self.worker = ConversionWorker(lambda: self.config, self.store, self.client, self.events)
        self.postprocess_store = PostprocessStore(self.config.database_path)
        self.postprocess_worker = PostprocessWorker(lambda: self.config, self.postprocess_store, self.events)
        self.stage2b_store = Stage2BStore(self.config.database_path)
        self.stage2b_worker = Stage2BWorker(
            lambda: self.config, self.stage2b_store, self.postprocess_store, self.events
        )
        self.oneplus_controller = OnePlusController(lambda: self.config)

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


runtime = Runtime()
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.worker.start()
    await runtime.postprocess_worker.start()
    await runtime.stage2b_worker.start()
    yield
    await runtime.stage2b_worker.stop()
    await runtime.postprocess_worker.stop()
    await runtime.worker.stop()


app = FastAPI(title="Docling Auto-Convert", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


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
    return rows


def enrich_postprocess_jobs(rows: list[dict]) -> list[dict]:
    """Add human-facing quality labels without changing machine status fields."""
    processed_dir = Path(runtime.config.processed_dir)
    for row in rows:
        row["quality_status"] = None
        row["quality_display_label"] = None
        row["integrity_status"] = None
        row["integrity_display_label"] = None
        result_dir = row.get("result_dir")
        if row.get("status") != "completed" or not result_dir:
            continue
        summary_path = processed_dir / Path(result_dir).name / "summary.json"
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


@app.get("/api/stage2b/status")
async def stage2b_status() -> dict:
    return {
        "enabled": runtime.config.stage2b_enabled,
        "modes": {
            "pi5": {"auto_run": runtime.config.stage2b_pi5_auto_run, "paused": runtime.config.stage2b_pi5_paused},
            "oneplus": {"auto_run": runtime.config.stage2b_oneplus_auto_run, "paused": runtime.config.stage2b_oneplus_paused},
        },
        "counts": await runtime.stage2b_store.counts(),
        "workers": runtime.stage2b_worker.worker_state,
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


@app.post("/api/stage2b/jobs/{job_id}/rerun")
async def stage2b_rerun(job_id: int) -> dict:
    if not await runtime.stage2b_store.rerun(job_id):
        raise HTTPException(status_code=409, detail="Only a completed or failed current verification job can be rerun.")
    runtime.events.notify("stage2b_rerun")
    return {"accepted": True, "docling_reconversion": False, "stage2a_rerun": False}


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


@app.get("/api/postprocess/jobs/{job_id}/artifact/{name}")
async def postprocess_artifact(job_id: int, name: str):
    safe_name = Path(name).name
    if safe_name != name or safe_name not in {
        "source_manifest.json", "integrity.json", "coverage.json", "profile.json", "diagnostics.json",
        "routes.json", "correction_ledger.json", "summary.json"
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


@app.get("/api/oneplus-control/status")
async def oneplus_control_status() -> dict:
    return await runtime.oneplus_controller.status()


@app.post("/api/oneplus-control/install-script")
async def oneplus_control_install_script() -> dict:
    try:
        return await runtime.oneplus_controller.install_script()
    except OnePlusControlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/oneplus-control/ssh/reconnect")
async def oneplus_control_ssh_reconnect() -> dict:
    try:
        return await runtime.oneplus_controller.reconnect_ssh()
    except OnePlusControlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/oneplus-control/ssh/stop")
async def oneplus_control_ssh_stop() -> dict:
    try:
        return await runtime.oneplus_controller.stop_ssh()
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


@app.get("/quality")
async def quality_page():
    return FileResponse(STATIC_DIR / "quality.html")


@app.get("/events")
async def events() -> StreamingResponse:
    return StreamingResponse(runtime.events.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
async def dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/errors")
async def error_log():
    return FileResponse(STATIC_DIR / "errors.html")


@app.get("/convert")
async def convert_page():
    return FileResponse(STATIC_DIR / "convert.html")
