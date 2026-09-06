import tempfile
import unittest
from pathlib import Path

from app.database import JobStore


class JobStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_job_lifecycle_and_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            job_id = await store.create_pending("report.pdf", "md")
            self.assertEqual((await store.counts())["pending"], 1)
            await store.mark_processing(job_id)
            await store.set_task_id(job_id, "task-123")
            await store.mark_failed(job_id, "DoclingApiError", "Connection refused", 1.2)
            failed = await store.list_jobs(failures_only=True)
            self.assertEqual(failed[0]["filename"], "report.pdf")
            self.assertTrue(await store.retry(job_id))
            pending = await store.list_pending()
            self.assertEqual(pending[0]["retry_count"], 1)
            await store.mark_processing(job_id)
            await store.mark_completed(job_id, 2.5, "report.zip")
            completed = await store.list_jobs()
            self.assertEqual(completed[0]["status"], "completed")
            self.assertEqual(completed[0]["output_filename"], "report.zip")

    async def test_processing_job_without_task_id_is_requeued_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            job_id = await store.create_pending("interrupted.pdf", "md")
            await store.mark_processing(job_id)
            self.assertEqual(await store.recover_interrupted_jobs(), 1)
            pending = await store.list_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["filename"], "interrupted.pdf")

    async def test_processing_job_with_task_id_remains_resumable_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            job_id = await store.create_pending("resumable.pdf", "md")
            await store.mark_processing(job_id)
            await store.set_task_id(job_id, "task-resume")
            self.assertEqual(await store.recover_interrupted_jobs(), 1)
            resumable = await store.list_resumable()
            self.assertEqual(len(resumable), 1)
            self.assertEqual(resumable[0]["docling_task_id"], "task-resume")


class Stage2MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_database_migrates_without_deleting_jobs(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.db"
            connection = sqlite3.connect(db)
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
                    output_formats TEXT,
                    output_filename TEXT,
                    source_size INTEGER,
                    source_mtime_ns INTEGER,
                    source_sha256 TEXT
                )"""
            )
            connection.execute(
                """INSERT INTO jobs (filename, status, output_format, output_formats, output_filename)
                   VALUES ('old.pdf', 'completed', 'md', '[\"md\"]', 'old.zip')"""
            )
            connection.execute(
                """CREATE TABLE postprocess_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversion_job_id INTEGER NOT NULL UNIQUE,
                    source_filename TEXT NOT NULL,
                    output_filename TEXT NOT NULL,
                    output_sha256 TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    processing_seconds REAL,
                    result_dir TEXT,
                    profile_kind TEXT,
                    route_count INTEGER DEFAULT 0,
                    error_type TEXT,
                    error_message TEXT,
                    retry_count INTEGER DEFAULT 0
                )"""
            )
            connection.execute(
                """INSERT INTO postprocess_jobs
                   (conversion_job_id, source_filename, output_filename, status, created_at, result_dir)
                   VALUES (1, 'old.pdf', 'old.zip', 'completed', '2026-01-01T00:00:00+00:00', 'old__job1')"""
            )
            connection.commit(); connection.close()

            store = JobStore(str(db)); await store.initialize()
            from app.postprocess_store import PostprocessStore
            pstore = PostprocessStore(str(db)); await pstore.initialize()

            rows = await store.list_jobs()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["filename"], "old.pdf")
            self.assertEqual(rows[0]["source_kind"], "watcher")
            prows = await pstore.list_jobs()
            self.assertEqual(len(prows), 1)
            self.assertEqual(prows[0]["result_dir"], "old__job1")
            self.assertEqual(prows[0]["rerun_count"], 0)
            self.assertIsNone(prows[0]["last_rerun_at"])
