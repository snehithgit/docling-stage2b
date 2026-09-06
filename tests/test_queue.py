import os
import tempfile
import io
import json
import zipfile
import time
import unittest
from pathlib import Path

from app.config import AppConfig
from app.database import JobStore
from app.docling_client import DoclingApiError, ResultPayload
from app.events import EventBroker
from app.worker import ConversionWorker


class FakeDoclingClient:
    def __init__(self, failures=None, transient_poll_errors=0):
        self.submitted = []
        self.submitted_formats = []
        self.failures = failures or set()
        # Number of times poll() should raise a transient DoclingApiError
        # (e.g. a slow-server read timeout) before it starts returning a
        # real status, simulating Docling being too busy to answer.
        self.transient_poll_errors = transient_poll_errors
        self.poll_calls = 0

    async def submit(self, file_path, to_formats=None):
        self.submitted.append(file_path.name)
        self.submitted_formats.append(list(to_formats or []))
        return f"task-{file_path.stem}"

    async def poll(self, task_id):
        self.poll_calls += 1
        if self.transient_poll_errors:
            self.transient_poll_errors -= 1
            raise DoclingApiError("Unable to poll conversion status: timed out")
        if task_id.removeprefix("task-") in self.failures:
            return {"task_status": "failure", "task_meta": {"reason": "Unreadable source"}}
        return {"task_status": "success"}

    async def result(self, task_id):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("converted.md", "# converted")
        return ResultPayload(content=buffer.getvalue(), content_type="application/zip")


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.input_dir, self.output_dir = root / "input", root / "output"
        self.input_dir.mkdir()
        self.config = AppConfig(input_dir=str(self.input_dir), output_dir=str(self.output_dir), database_path=str(root / "jobs.db"), docling_poll_interval_seconds=1, poll_max_consecutive_errors=3)
        self.config.validate()
        self.store = JobStore(self.config.database_path)
        await self.store.initialize()

    async def asyncTearDown(self):
        self.temporary_directory.cleanup()

    async def _discover_as_stable(self, worker):
        for path in self.input_dir.iterdir():
            stat = path.stat()
            worker._stability[path] = (stat.st_size, stat.st_mtime_ns, time.monotonic() - 2)
        await worker._discover_files()

    async def test_processes_only_the_oldest_pending_file_per_pass(self):
        older, newer = self.input_dir / "older.pdf", self.input_dir / "newer.pdf"
        older.write_bytes(b"old")
        newer.write_bytes(b"new")
        os.utime(older, (1, 1))
        os.utime(newer, (2, 2))
        client = FakeDoclingClient()
        worker = ConversionWorker(lambda: self.config, self.store, client, EventBroker())
        await self._discover_as_stable(worker)
        self.assertEqual((await self.store.counts())["pending"], 2)
        await worker._process_one()
        self.assertEqual(client.submitted, ["older.pdf"])
        self.assertEqual((await self.store.counts())["pending"], 1)
        self.assertEqual((await self.store.counts())["completed"], 1)
        await worker._process_one()
        self.assertEqual(client.submitted, ["older.pdf", "newer.pdf"])


    async def test_job_snapshots_and_submits_exact_multi_format_selection(self):
        self.config.to_formats = ["json", "html", "text"]
        source = self.input_dir / "multi.pdf"
        source.write_bytes(b"multi")
        client = FakeDoclingClient()
        worker = ConversionWorker(lambda: self.config, self.store, client, EventBroker())
        await self._discover_as_stable(worker)

        pending = await self.store.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(json.loads(pending[0]["output_formats"]), ["json", "html", "text"])

        # Changing settings after discovery must not change this queued job.
        self.config.to_formats = ["md"]
        await worker._process_one()
        self.assertEqual(client.submitted_formats, [["json", "html", "text"]])

    async def test_stores_actionable_docling_task_failure_detail(self):
        problem = self.input_dir / "problem.pdf"
        problem.write_bytes(b"invalid")
        client = FakeDoclingClient(failures={"problem"})
        worker = ConversionWorker(lambda: self.config, self.store, client, EventBroker())
        await self._discover_as_stable(worker)
        await worker._process_one()
        failures = await self.store.list_jobs(failures_only=True)
        self.assertIn("Unreadable source", failures[0]["error_message"])

    async def test_survives_transient_poll_errors_without_failing_the_job(self):
        # Regression test: a slow Docling server (busy converting) can make
        # a single /v1/status/poll call time out even though the document
        # converts successfully. That should be retried, not treated as a
        # job failure.
        slow = self.input_dir / "slow.pdf"
        slow.write_bytes(b"slow-but-fine")
        client = FakeDoclingClient(transient_poll_errors=2)
        worker = ConversionWorker(lambda: self.config, self.store, client, EventBroker())
        await self._discover_as_stable(worker)
        await worker._process_one()
        self.assertEqual((await self.store.counts())["completed"], 1)
        self.assertEqual((await self.store.counts())["failed"], 0)
        self.assertGreaterEqual(client.poll_calls, 3)

    async def test_fails_job_after_exceeding_max_consecutive_poll_errors(self):
        # If Docling never comes back (not just momentarily slow), the job
        # should still eventually fail rather than retry forever.
        dead = self.input_dir / "dead.pdf"
        dead.write_bytes(b"dead")
        client = FakeDoclingClient(transient_poll_errors=10)
        worker = ConversionWorker(lambda: self.config, self.store, client, EventBroker())
        await self._discover_as_stable(worker)
        await worker._process_one()
        failures = await self.store.list_jobs(failures_only=True)
        self.assertEqual(len(failures), 1)
        self.assertIn("poll conversion status", failures[0]["error_message"])
        self.assertEqual(client.poll_calls, self.config.poll_max_consecutive_errors)

class WatcherControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.input_dir, self.output_dir = root / "input", root / "output"
        self.input_dir.mkdir()
        self.config = AppConfig(
            input_dir=str(self.input_dir),
            output_dir=str(self.output_dir),
            database_path=str(root / "jobs.db"),
            docling_poll_interval_seconds=1,
            watcher_auto_run=False,
        )
        self.config.validate()
        self.store = JobStore(self.config.database_path)
        await self.store.initialize()
        self.client = FakeDoclingClient()
        self.worker = ConversionWorker(lambda: self.config, self.store, self.client, EventBroker())

    async def asyncTearDown(self):
        self.temporary_directory.cleanup()

    async def _discover_as_stable(self):
        for path in self.input_dir.iterdir():
            stat = path.stat()
            self.worker._stability[path] = (stat.st_size, stat.st_mtime_ns, time.monotonic() - 2)
        await self.worker._discover_files()

    async def test_manual_mode_waits_until_start_and_processes_smallest_first(self):
        big = self.input_dir / "big.pdf"
        small = self.input_dir / "small.pdf"
        medium = self.input_dir / "medium.pdf"
        big.write_bytes(b"b" * 300)
        small.write_bytes(b"s" * 10)
        medium.write_bytes(b"m" * 100)
        await self._discover_as_stable()

        # Manual mode must not touch an unauthorized queue item.
        self.assertFalse(await self.worker._process_one(authorized_only=True))
        self.assertEqual(self.client.submitted, [])

        started = await self.worker.start_batch()
        self.assertTrue(started["accepted"])
        self.assertEqual(started["queued"], 3)
        await self.worker._process_one(authorized_only=True)
        await self.worker._process_one(authorized_only=True)
        await self.worker._process_one(authorized_only=True)
        self.assertEqual(self.client.submitted, ["small.pdf", "medium.pdf", "big.pdf"])
        status = await self.worker.control_status()
        self.assertEqual(status["state"], "waiting_for_start")

    async def test_manual_start_snapshots_only_current_queue(self):
        first = self.input_dir / "first.pdf"
        first.write_bytes(b"a" * 20)
        await self._discover_as_stable()
        await self.worker.start_batch()

        late = self.input_dir / "late.pdf"
        late.write_bytes(b"z")
        stat = late.stat()
        self.worker._stability[late] = (stat.st_size, stat.st_mtime_ns, time.monotonic() - 2)
        await self.worker._discover_files()

        await self.worker._process_one(authorized_only=True)
        self.assertFalse(await self.worker._process_one(authorized_only=True))
        self.assertEqual(self.client.submitted, ["first.pdf"])
        pending = await self.store.list_pending()
        self.assertEqual([job["filename"] for job in pending], ["late.pdf"])

    async def test_auto_run_allows_pending_queue_and_mode_change_clears_manual_batch(self):
        one = self.input_dir / "one.pdf"
        two = self.input_dir / "two.pdf"
        one.write_bytes(b"1" * 80)
        two.write_bytes(b"2" * 5)
        await self._discover_as_stable()
        await self.worker.start_batch()
        self.assertGreater((await self.worker.control_status())["batch_remaining"], 0)

        self.config.watcher_auto_run = True
        await self.worker.set_auto_run(True)
        status = await self.worker.control_status()
        self.assertTrue(status["auto_run"])
        self.assertEqual(status["batch_remaining"], 0)

        await self.worker._process_one(authorized_only=False)
        await self.worker._process_one(authorized_only=False)
        self.assertEqual(self.client.submitted, ["two.pdf", "one.pdf"])
