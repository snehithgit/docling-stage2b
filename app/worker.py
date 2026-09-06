from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .config import AppConfig
from .database import JobStore
from .docling_client import DoclingApiError, DoclingClient, ResultPayload
from .events import EventBroker


class ConversionWorker:
    """Continuously discovers files while a separate single worker converts them."""

    def __init__(self, config_getter: Any, store: JobStore, client: DoclingClient, events: EventBroker) -> None:
        self._config_getter = config_getter
        self._store = store
        self._client = client
        self._events = events
        self._stopping = asyncio.Event()
        self._discovery_task: asyncio.Task[None] | None = None
        self._processing_task: asyncio.Task[None] | None = None
        # Kept as an alias for compatibility with older code/tests that may
        # have inspected the worker's main task.
        self._task: asyncio.Task[None] | None = None
        self._health_task: asyncio.Task[None] | None = None
        # Watcher discovery is always active, but conversion is manual-start.
        # Each Start press snapshots the currently pending job IDs. Files
        # discovered later wait for the next Start press.
        self._authorized_job_ids: set[int] = set()
        self._batch_lock = asyncio.Lock()
        self._batch_wakeup = asyncio.Event()
        self.health_status: dict[str, Any] = {
            "reachable": False,
            "ready": False,
            "health_detail": "Checking Docling Serve…",
            "ready_detail": "Checking Docling Serve…",
        }
        self._stability: dict[Path, tuple[int, int, float]] = {}

    async def start(self) -> None:
        config = self._config_getter()
        Path(config.input_dir).mkdir(parents=True, exist_ok=True)
        Path(config.output_dir).mkdir(parents=True, exist_ok=True)
        await self._store.initialize()
        recovered = await self._store.recover_interrupted_jobs()

        self._discovery_task = asyncio.create_task(
            self._discovery_loop(), name="docling-folder-discovery"
        )
        self._processing_task = asyncio.create_task(
            self._processing_loop(), name="docling-single-conversion-worker"
        )
        self._task = self._processing_task
        self._health_task = asyncio.create_task(
            self._health_loop(), name="docling-health-monitor"
        )
        self._events.notify("recovered_interrupted_jobs" if recovered else "worker_started")

    async def stop(self) -> None:
        self._stopping.set()
        tasks = [
            task
            for task in (
                self._discovery_task,
                self._processing_task,
                self._health_task,
            )
            if task
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _discovery_loop(self) -> None:
        """Keep the SQLite queue current even while a long conversion runs."""
        while not self._stopping.is_set():
            try:
                await self._discover_files()
            except Exception as exc:
                self.health_status = {**self.health_status, "discovery_error": str(exc)}
                self._events.notify("worker_error")
            await self._sleep(self._config_getter().poll_interval_seconds)

    async def _processing_loop(self) -> None:
        """Exactly one conversion is processed at a time.

        Discovery is always active. In manual mode only the current Start
        snapshot is eligible. In Auto Run mode any pending job is eligible.
        A remote Docling task that survived a restart is always resumed first.
        """
        while not self._stopping.is_set():
            try:
                config = self._config_getter()
                processed = await self._process_one(authorized_only=not config.watcher_auto_run)
            except Exception as exc:
                self.health_status = {**self.health_status, "worker_error": str(exc)}
                self._events.notify("worker_error")
                processed = False
            if not processed:
                await self._sleep(self._config_getter().poll_interval_seconds)

    async def start_batch(self) -> dict[str, Any]:
        """Authorize exactly the jobs pending when Start is pressed.

        Files discovered after the snapshot wait for the next Start press.
        Manual Start is disabled while Auto Run is enabled.
        """
        if self._config_getter().watcher_auto_run:
            return {
                "accepted": False,
                "reason": "auto_run_enabled",
                "queued": 0,
                "state": "auto_running",
                "order": "smallest_first",
            }

        # Capture any stable files that arrived since the last discovery pass.
        await self._discover_files()
        pending = await self._store.list_pending()
        async with self._batch_lock:
            # Never replace an active batch; repeated clicks are harmless.
            if self._authorized_job_ids:
                return {
                    "accepted": False,
                    "reason": "batch_already_running",
                    "queued": len(self._authorized_job_ids),
                    "state": "running",
                    "order": "smallest_first",
                }
            self._authorized_job_ids = {int(job["id"]) for job in pending}
            if self._authorized_job_ids:
                self._batch_wakeup.set()
        if self._authorized_job_ids:
            self._events.notify("watcher_batch_started")
        else:
            self._events.notify("watcher_batch_empty")
        total_bytes = sum(int(job.get("source_size") or 0) for job in pending)
        return {
            "accepted": bool(self._authorized_job_ids),
            "queued": len(self._authorized_job_ids),
            "total_bytes": total_bytes,
            "state": "running" if self._authorized_job_ids else "waiting_for_start",
            "order": "smallest_first",
        }

    async def set_auto_run(self, enabled: bool) -> None:
        """Switch execution mode without interrupting an in-flight Docling task.

        Any manual batch authorization is cleared on a mode change. Pending jobs
        remain pending, so turning Auto Run off pauses before the next submit.
        """
        async with self._batch_lock:
            self._authorized_job_ids.clear()
            self._batch_wakeup.clear()
        self._events.notify("watcher_auto_run_enabled" if enabled else "watcher_auto_run_disabled")

    async def control_status(self) -> dict[str, Any]:
        config = self._config_getter()
        counts = await self._store.counts()
        async with self._batch_lock:
            remaining = len(self._authorized_job_ids)
        processing = int(counts.get("processing", 0) or 0)
        pending = int(counts.get("pending", 0) or 0)
        if config.watcher_auto_run:
            state = "auto_running" if processing or pending else "auto_idle"
            mode = "auto_run"
        else:
            state = "running" if remaining or processing else "waiting_for_start"
            mode = "manual_start"
        return {
            "mode": mode,
            "auto_run": bool(config.watcher_auto_run),
            "state": state,
            "batch_remaining": remaining,
            "pending": pending,
            "processing": processing,
            "order": "smallest_first",
        }

    async def _complete_authorized_job(self, job_id: int) -> None:
        notify_complete = False
        async with self._batch_lock:
            self._authorized_job_ids.discard(int(job_id))
            if not self._authorized_job_ids:
                self._batch_wakeup.clear()
                notify_complete = True
        if notify_complete:
            self._events.notify("watcher_batch_completed")

    async def _health_loop(self) -> None:
        while not self._stopping.is_set():
            self.health_status = await self._client.health()
            self._events.notify("docling_health")
            await self._sleep(max(5, self._config_getter().poll_interval_seconds))

    async def _sleep(self, seconds: int | float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _discover_files(self) -> None:
        config: AppConfig = self._config_getter()
        input_dir = Path(config.input_dir)
        output_dir = Path(config.output_dir)
        supported = {item.lower() for item in config.supported_extensions}

        try:
            candidates = sorted(
                (
                    path
                    for path in input_dir.iterdir()
                    if path.is_file() and path.suffix.lower() in supported
                ),
                key=lambda path: (path.stat().st_mtime, path.name.lower()),
            )
        except FileNotFoundError:
            input_dir.mkdir(parents=True, exist_ok=True)
            return

        present = set(candidates)
        for stale_path in set(self._stability) - present:
            self._stability.pop(stale_path, None)

        for path in candidates:
            if not self._is_stable(path):
                continue

            try:
                stat = path.stat()
            except FileNotFoundError:
                continue

            # Cheap fast path: processed files normally remain in /input. Do
            # not SHA-256 every large PDF on every 3-second discovery pass.
            if await self._store.is_tracked(
                path.name, stat.st_size, stat.st_mtime_ns
            ):
                continue

            default_output = output_dir / f"{path.stem}.{config.output_extension}"
            has_any_history = await self._store.is_tracked(path.name)

            # Preserve the old app's behavior when jobs.db is absent but a
            # valid converted output is already present.
            if (
                default_output.exists()
                and not has_any_history
                and self._output_is_valid(default_output, config)
            ):
                continue

            identity = await self._file_identity(path)
            if identity is None:
                continue
            size, mtime_ns, sha256 = identity

            if await self._store.is_tracked(path.name, size, mtime_ns, sha256):
                continue

            # Existing databases from older project versions have filename-only
            # rows. Attach the current source identity to the most recent legacy
            # row once, rather than treating every existing input as a new file
            # immediately after upgrade.
            if has_any_history and await self._store.backfill_legacy_identity(
                path.name, size, mtime_ns, sha256
            ):
                continue

            await self._store.create_pending(
                path.name,
                list(config.to_formats),
                source_size=size,
                source_mtime_ns=mtime_ns,
                source_sha256=sha256,
            )
            self._events.notify("file_discovered")

    def _is_stable(self, path: Path) -> bool:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return False
        signature = (stat.st_size, stat.st_mtime_ns)
        now = time.monotonic()
        previous = self._stability.get(path)
        if previous is None or previous[:2] != signature:
            self._stability[path] = (*signature, now)
            return False
        return now - previous[2] >= 1.0

    async def _file_identity(self, path: Path) -> tuple[int, int, str] | None:
        """Return a stable identity, or None if the file changed while hashing."""
        try:
            before = path.stat()
            digest = await asyncio.to_thread(self._sha256_file, path)
            after = path.stat()
        except (FileNotFoundError, OSError):
            return None

        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            # Copy/write was still in progress. Reset the stability timer and
            # let the next discovery pass try again.
            self._stability[path] = (
                after.st_size,
                after.st_mtime_ns,
                time.monotonic(),
            )
            return None
        return after.st_size, after.st_mtime_ns, digest

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest()

    async def _source_matches_job(self, path: Path, job: dict[str, Any]) -> bool:
        expected_hash = job.get("source_sha256")
        if not expected_hash:
            # Legacy/manual test rows may not have an identity. Discovery will
            # backfill real legacy rows before normal automatic processing.
            return True
        identity = await self._file_identity(path)
        if identity is None:
            return False
        size, _mtime_ns, sha256 = identity
        expected_size = job.get("source_size")
        return sha256 == expected_hash and (
            expected_size is None or int(expected_size) == size
        )

    @staticmethod
    def _job_formats(job: dict[str, Any], config: AppConfig) -> list[str]:
        raw = job.get("output_formats")
        if isinstance(raw, list):
            formats = raw
        elif isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
                formats = parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                formats = []
        else:
            formats = []
        if not formats:
            legacy = job.get("output_format")
            formats = [legacy] if legacy else list(config.to_formats)
        return list(dict.fromkeys(str(item) for item in formats if item))

    async def _process_one(self, authorized_only: bool = False) -> bool:
        # A remote Docling task that survived an app restart gets priority.
        resumable = await self._store.list_resumable()
        if resumable:
            job = min(resumable, key=lambda item: item["id"])
        else:
            pending = await self._store.list_pending()
            if authorized_only:
                async with self._batch_lock:
                    authorized = set(self._authorized_job_ids)
                pending = [job for job in pending if int(job["id"]) in authorized]
            if not pending:
                return False
            config = self._config_getter()
            input_dir = Path(config.input_dir)
            job = sorted(
                pending,
                key=lambda item: self._job_order(
                    item, input_dir / item["filename"]
                ),
            )[0]

        config: AppConfig = self._config_getter()
        input_dir = Path(config.input_dir)
        file_path = input_dir / job["filename"]
        is_resume = job["status"] == "processing" and bool(job.get("docling_task_id"))
        batch_tracked = authorized_only and int(job["id"]) in self._authorized_job_ids

        if not is_resume and not file_path.is_file():
            await self._store.mark_failed(
                job["id"],
                "FileMissing",
                "The input file is no longer present in the configured input directory.",
            )
            self._events.notify("file_missing")
            if batch_tracked:
                await self._complete_authorized_job(job["id"])
            return True

        if not is_resume and not await self._source_matches_job(file_path, job):
            await self._store.mark_failed(
                job["id"],
                "SourceChanged",
                "The input file changed after this queue item was created. "
                "The newer file version will be discovered and processed as a separate job.",
            )
            self._events.notify("source_changed")
            if batch_tracked:
                await self._complete_authorized_job(job["id"])
            return True

        output_filename = job.get("output_filename") or await self._reserve_output_filename(
            job, config
        )

        started = time.monotonic()
        try:
            if is_resume:
                task_id = str(job["docling_task_id"])
                self._events.notify("processing_resumed")
            else:
                await self._store.mark_processing(job["id"])
                self._events.notify("processing_started")
                task_id = await self._client.submit(
                    file_path, to_formats=self._job_formats(job, config)
                )
                await self._store.set_task_id(job["id"], task_id)

            consecutive_poll_errors = 0
            while True:
                if time.monotonic() - started > config.document_timeout_minutes * 60:
                    raise TimeoutError(
                        f"Conversion exceeded the configured {config.document_timeout_minutes}-minute timeout."
                    )
                try:
                    poll_result = await self._client.poll(task_id)
                except DoclingApiError:
                    consecutive_poll_errors += 1
                    if consecutive_poll_errors >= config.poll_max_consecutive_errors:
                        raise
                    self._events.notify("processing_poll_retry")
                    await self._sleep(
                        min(
                            config.docling_poll_interval_seconds
                            * consecutive_poll_errors,
                            30,
                        )
                    )
                    continue

                consecutive_poll_errors = 0
                task_status = str(poll_result.get("task_status", "")).lower()
                if task_status == "success":
                    break
                if task_status == "failure":
                    raise DoclingApiError(self._task_failure_message(poll_result))
                self._events.notify("processing_update")
                await self._sleep(config.docling_poll_interval_seconds)

            payload = await self._client.result(task_id)
            output_filename = await self._write_result(
                file_path,
                payload,
                config,
                output_filename=output_filename,
            )
            await self._store.mark_completed(
                job["id"],
                round(time.monotonic() - started, 3),
                output_filename,
            )
            self._events.notify("processing_completed")
        except TimeoutError as exc:
            await self._store.mark_failed(
                job["id"],
                "Timeout",
                str(exc),
                round(time.monotonic() - started, 3),
            )
            self._events.notify("processing_failed")
        except (DoclingApiError, OSError, KeyError, zipfile.BadZipFile) as exc:
            await self._store.mark_failed(
                job["id"],
                type(exc).__name__,
                str(exc),
                round(time.monotonic() - started, 3),
            )
            self._events.notify("processing_failed")
        if batch_tracked:
            await self._complete_authorized_job(job["id"])
        return True

    async def _reserve_output_filename(self, job: dict[str, Any], config: AppConfig) -> str:
        """Choose a deterministic output name and persist it before conversion.

        The first version keeps the familiar `book.zip` name. A later file
        reusing the same input filename gets `book__<sha8>.zip`, preserving the
        old result and keeping historical job download links truthful.
        """
        input_path = Path(job["filename"])
        extension = config.output_extension
        base_name = f"{input_path.stem}.{extension}"
        base_path = Path(config.output_dir) / base_name

        earlier = await self._store.has_earlier_job(job["filename"], job["id"])
        base_conflicts = base_path.exists() and self._output_is_valid(base_path, config)

        if earlier or base_conflicts:
            token = (job.get("source_sha256") or f"job{job['id']}")[:8]
            output_filename = f"{input_path.stem}__{token}.{extension}"
        else:
            # If an existing base output is corrupt/incomplete, deliberately
            # reuse the normal name so the atomic write replaces it.
            output_filename = base_name

        await self._store.set_output_filename(job["id"], output_filename)
        return output_filename

    @staticmethod
    def _job_order(job: dict[str, Any], path: Path) -> tuple[int, float, str, int]:
        """Smallest source first; ties use age/name for deterministic order."""
        source_size = job.get("source_size")
        if source_size is None:
            try:
                source_size = path.stat().st_size
            except FileNotFoundError:
                source_size = 2**63 - 1
        source_mtime_ns = job.get("source_mtime_ns")
        if source_mtime_ns is not None:
            mtime = float(source_mtime_ns) / 1_000_000_000
        else:
            mtime, _ = ConversionWorker._file_order(path)
        return int(source_size), mtime, path.name.lower(), int(job["id"])

    @staticmethod
    def _file_order(path: Path) -> tuple[float, str]:
        try:
            return path.stat().st_mtime, path.name.lower()
        except FileNotFoundError:
            return float("inf"), path.name.lower()

    @staticmethod
    def _task_failure_message(payload: dict[str, Any]) -> str:
        for key in ("task_meta", "error", "errors", "message", "detail"):
            detail = payload.get(key)
            if detail:
                if isinstance(detail, (dict, list)):
                    return (
                        "Docling Serve reported a task failure: "
                        f"{json.dumps(detail, ensure_ascii=False)}"
                    )
                return f"Docling Serve reported a task failure: {detail}"
        return "Docling Serve reported a task failure without error details."

    @staticmethod
    def _output_is_valid(path: Path, config: AppConfig) -> bool:
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                return False
            if config.target_type != "zip":
                return True
            if not zipfile.is_zipfile(path):
                return False
            with zipfile.ZipFile(path) as archive:
                if not archive.namelist():
                    return False
                return archive.testzip() is None
        except (OSError, zipfile.BadZipFile):
            return False

    async def _write_result(
        self,
        input_path: Path,
        payload: ResultPayload,
        config: AppConfig,
        output_filename: str | None = None,
    ) -> str:
        output_path = Path(config.output_dir) / (
            output_filename or f"{input_path.stem}.{config.output_extension}"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_name(
            f".{output_path.name}.{uuid.uuid4().hex}.part"
        )

        try:
            if config.target_type == "zip":
                if not payload.content:
                    raise DoclingApiError("Docling Serve returned an empty ZIP result.")
                await asyncio.to_thread(self._write_bytes_fsync, temp_path, payload.content)
                if not self._output_is_valid(temp_path, config):
                    raise DoclingApiError(
                        "Docling Serve returned an invalid or incomplete ZIP result; "
                        "the existing completed output was not replaced."
                    )
            else:
                result = payload.json_data
                if not result:
                    raise DoclingApiError(
                        "Expected an in-body JSON result but Docling Serve returned a different response."
                    )
                document = result.get("document") or {}
                field = {
                    "md": "md_content",
                    "json": "json_content",
                    "html": "html_content",
                    "text": "text_content",
                    "doctags": "doctags_content",
                }[config.primary_format]
                content = document.get(field)
                if content is None:
                    raise DoclingApiError(
                        f"The conversion result did not include {field}."
                    )
                rendered = (
                    json.dumps(content, indent=2, ensure_ascii=False)
                    if config.primary_format == "json"
                    else str(content)
                )
                await asyncio.to_thread(
                    self._write_text_fsync, temp_path, rendered
                )
                if temp_path.stat().st_size <= 0:
                    raise DoclingApiError("Docling Serve returned an empty result.")

            # os.replace is atomic on the same filesystem. The dashboard only
            # ever sees the final filename after the complete temp file has
            # been written and validated.
            os.replace(temp_path, output_path)
            return output_path.name
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _write_bytes_fsync(path: Path, content: bytes) -> None:
        with path.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _write_text_fsync(path: Path, content: str) -> None:
        with path.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
