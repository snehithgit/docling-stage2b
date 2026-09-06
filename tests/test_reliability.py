import asyncio
import io
import tempfile
import sqlite3
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app.config import AppConfig
from app.database import JobStore
from app.docling_client import DoclingApiError, DoclingClient, ResultPayload
from app.events import EventBroker
from app.worker import ConversionWorker


def zip_bytes(name="converted.md", text="# converted"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


class SuccessfulClient:
    def __init__(self):
        self.submitted = []

    async def health(self):
        return {
            "reachable": True,
            "ready": True,
            "health_detail": "HTTP 200",
            "ready_detail": "HTTP 200",
        }

    async def submit(self, file_path, to_formats=None):
        self.submitted.append(file_path.name)
        return f"task-{file_path.stem}"

    async def poll(self, task_id):
        return {"task_status": "success"}

    async def result(self, task_id):
        return ResultPayload(
            content=zip_bytes(text=task_id),
            content_type="application/zip",
        )


class BlockingClient(SuccessfulClient):
    def __init__(self):
        super().__init__()
        self.poll_started = asyncio.Event()
        self.release = asyncio.Event()

    async def poll(self, task_id):
        self.poll_started.set()
        await self.release.wait()
        return {"task_status": "success"}


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def _stable_discover(self, worker, input_dir):
        for path in input_dir.iterdir():
            if path.is_file():
                stat = path.stat()
                worker._stability[path] = (
                    stat.st_size,
                    stat.st_mtime_ns,
                    time.monotonic() - 2,
                )
        await worker._discover_files()

    async def test_same_filename_with_new_content_creates_new_version_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            config = AppConfig(
                input_dir=str(input_dir),
                output_dir=str(output_dir),
                database_path=str(root / "jobs.db"),
            )
            store = JobStore(config.database_path)
            await store.initialize()
            worker = ConversionWorker(lambda: config, store, SuccessfulClient(), EventBroker())

            source = input_dir / "manual.pdf"
            source.write_bytes(b"version-one")
            await self._stable_discover(worker, input_dir)
            first = await store.list_pending()
            self.assertEqual(len(first), 1)
            first_hash = first[0]["source_sha256"]

            source.write_bytes(b"version-two-with-different-content")
            await self._stable_discover(worker, input_dir)
            pending = await store.list_pending()
            self.assertEqual(len(pending), 2)
            hashes = {row["source_sha256"] for row in pending}
            self.assertEqual(len(hashes), 2)
            self.assertIn(first_hash, hashes)

    async def test_queued_version_replaced_before_processing_is_not_submitted_as_old_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            config = AppConfig(
                input_dir=str(input_dir),
                output_dir=str(output_dir),
                database_path=str(root / "jobs.db"),
            )
            store = JobStore(config.database_path)
            await store.initialize()
            client = SuccessfulClient()
            worker = ConversionWorker(lambda: config, store, client, EventBroker())

            source = input_dir / "manual.pdf"
            source.write_bytes(b"queued-version-one")
            await self._stable_discover(worker, input_dir)

            source.write_bytes(b"replacement-version-two")
            await self._stable_discover(worker, input_dir)
            self.assertEqual((await store.counts())["pending"], 2)

            await worker._process_one()
            self.assertEqual(client.submitted, [])
            failures = await store.list_jobs(failures_only=True)
            self.assertEqual(failures[0]["error_type"], "SourceChanged")

            await worker._process_one()
            self.assertEqual(client.submitted, ["manual.pdf"])
            self.assertEqual((await store.counts())["completed"], 1)

    async def test_second_version_gets_distinct_output_and_preserves_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            config = AppConfig(
                input_dir=str(input_dir),
                output_dir=str(output_dir),
                database_path=str(root / "jobs.db"),
                docling_poll_interval_seconds=1,
            )
            store = JobStore(config.database_path)
            await store.initialize()
            client = SuccessfulClient()
            worker = ConversionWorker(lambda: config, store, client, EventBroker())

            source = input_dir / "manual.pdf"
            source.write_bytes(b"version-one")
            await self._stable_discover(worker, input_dir)
            await worker._process_one()
            first_jobs = await store.list_jobs()
            first_name = next(row["output_filename"] for row in first_jobs if row["status"] == "completed")
            self.assertEqual(first_name, "manual.zip")
            first_bytes = (output_dir / first_name).read_bytes()

            source.write_bytes(b"version-two")
            await self._stable_discover(worker, input_dir)
            await worker._process_one()

            completed = [row for row in await store.list_jobs() if row["status"] == "completed"]
            self.assertEqual(len(completed), 2)
            names = {row["output_filename"] for row in completed}
            self.assertIn("manual.zip", names)
            versioned = next(name for name in names if name != "manual.zip")
            self.assertTrue(versioned.startswith("manual__"))
            self.assertTrue((output_dir / versioned).is_file())
            self.assertEqual((output_dir / "manual.zip").read_bytes(), first_bytes)

    async def test_invalid_zip_never_replaces_existing_completed_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AppConfig(
                input_dir=str(root / "input"),
                output_dir=str(root / "output"),
                database_path=str(root / "jobs.db"),
            )
            source = Path(config.input_dir) / "manual.pdf"
            source.parent.mkdir()
            source.write_bytes(b"source")
            output = Path(config.output_dir)
            output.mkdir()
            final = output / "manual.zip"
            original = zip_bytes(text="old-good-result")
            final.write_bytes(original)

            worker = ConversionWorker(
                lambda: config,
                JobStore(config.database_path),
                SuccessfulClient(),
                EventBroker(),
            )

            with self.assertRaises(DoclingApiError):
                await worker._write_result(
                    source,
                    ResultPayload(content=b"not-a-zip", content_type="application/zip"),
                    config,
                    output_filename="manual.zip",
                )

            self.assertEqual(final.read_bytes(), original)
            self.assertFalse(any(p.suffix == ".part" for p in output.iterdir()))

    async def test_saved_docling_task_is_resumed_without_resubmitting_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            config = AppConfig(
                input_dir=str(input_dir),
                output_dir=str(output_dir),
                database_path=str(root / "jobs.db"),
            )
            store = JobStore(config.database_path)
            await store.initialize()
            job_id = await store.create_pending(
                "resume.pdf",
                "md",
                source_size=123,
                source_mtime_ns=456,
                source_sha256="a" * 64,
            )
            await store.mark_processing(job_id)
            await store.set_task_id(job_id, "remote-task-123")
            await store.recover_interrupted_jobs()

            client = SuccessfulClient()
            worker = ConversionWorker(lambda: config, store, client, EventBroker())
            await worker._process_one()

            self.assertEqual(client.submitted, [])
            jobs = await store.list_jobs()
            self.assertEqual(jobs[0]["status"], "completed")
            self.assertTrue((output_dir / jobs[0]["output_filename"]).is_file())

    async def test_discovery_continues_while_single_conversion_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            config = AppConfig(
                input_dir=str(input_dir),
                output_dir=str(output_dir),
                database_path=str(root / "jobs.db"),
                poll_interval_seconds=1,
                docling_poll_interval_seconds=1,
                watcher_auto_run=True,
            )
            store = JobStore(config.database_path)
            await store.initialize()

            first = input_dir / "first.pdf"
            first.write_bytes(b"first")
            # Create the initial queue row directly so processing can block
            # immediately while the independent discovery loop observes second.pdf.
            await store.create_pending(first.name, "md")

            client = BlockingClient()
            worker = ConversionWorker(lambda: config, store, client, EventBroker())
            await worker.start()
            try:
                await asyncio.wait_for(client.poll_started.wait(), timeout=2)
                second = input_dir / "second.pdf"
                second.write_bytes(b"second")
                # First discovery pass records stability; the next pass queues it.
                await asyncio.sleep(2.4)
                counts = await store.counts()
                self.assertEqual(counts["processing"], 1)
                self.assertEqual(counts["pending"], 1)
            finally:
                client.release.set()
                await asyncio.sleep(0.05)
                await worker.stop()

    async def test_old_jobs_database_is_migrated_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "jobs.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    """CREATE TABLE jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        status TEXT NOT NULL,
                        docling_task_id TEXT,
                        submitted_at TEXT,
                        completed_at TEXT,
                        processing_seconds REAL,
                        error_type TEXT,
                        error_message TEXT,
                        retry_count INTEGER DEFAULT 0,
                        output_format TEXT NOT NULL DEFAULT 'md',
                        output_filename TEXT
                    )"""
                )
                connection.execute(
                    "INSERT INTO jobs (filename, status, output_format) VALUES ('legacy.pdf', 'completed', 'md')"
                )
                connection.commit()
            finally:
                connection.close()

            store = JobStore(str(db_path))
            await store.initialize()
            rows = await store.list_jobs()
            self.assertEqual(rows[0]["filename"], "legacy.pdf")
            self.assertEqual(rows[0]["output_formats"], '["md"]')
            self.assertIn("source_size", rows[0])
            self.assertIn("source_mtime_ns", rows[0])
            self.assertIn("source_sha256", rows[0])

    async def test_auto_submit_does_not_use_path_read_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large.pdf"
            source.write_bytes(b"small test stand-in")
            config = AppConfig(docling_url="http://docling.test")
            client = DoclingClient(lambda: config)

            response = Mock()
            response.is_success = True
            response.json.return_value = {"task_id": "task-stream"}

            with patch.object(Path, "read_bytes", side_effect=AssertionError("read_bytes used")):
                with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
                    task_id = await client.submit(source)

            self.assertEqual(task_id, "task-stream")


if __name__ == "__main__":
    unittest.main()
