from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class JobStore:
    """SQLite-backed queue state.

    The schema is migrated in place so existing installations can keep the
    same jobs.db. Newer rows record a stable source identity (size, mtime and
    SHA-256) so a different file reusing the same filename is still processed.
    """

    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await self._run(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_sync(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
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
                    source_sha256 TEXT,
                    source_kind TEXT NOT NULL DEFAULT 'watcher'
                )"""
            )
            existing = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            migrations = {
                "output_format": "ALTER TABLE jobs ADD COLUMN output_format TEXT NOT NULL DEFAULT 'md'",
                "output_formats": "ALTER TABLE jobs ADD COLUMN output_formats TEXT",
                "output_filename": "ALTER TABLE jobs ADD COLUMN output_filename TEXT",
                "source_size": "ALTER TABLE jobs ADD COLUMN source_size INTEGER",
                "source_mtime_ns": "ALTER TABLE jobs ADD COLUMN source_mtime_ns INTEGER",
                "source_sha256": "ALTER TABLE jobs ADD COLUMN source_sha256 TEXT",
                "source_kind": "ALTER TABLE jobs ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'watcher'",
            }
            for column, statement in migrations.items():
                if column not in existing:
                    connection.execute(statement)

            # Backward-compatible migration: older jobs stored only the first
            # format. Preserve it as a one-item format list for the dashboard.
            rows = connection.execute(
                "SELECT id, output_format FROM jobs WHERE output_formats IS NULL OR output_formats = ''"
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE jobs SET output_formats = ? WHERE id = ?",
                    (json.dumps([row["output_format"] or "md"]), row["id"]),
                )

            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_filename ON jobs(filename)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source_sha256 ON jobs(source_sha256)")

    async def recover_interrupted_jobs(self) -> int:
        """Recover queue state after an application restart.

        A processing row that already has a Docling task_id is intentionally
        left as `processing`; the worker will resume polling that remote task.
        A processing row without a task_id could not be resumed safely, so it
        is re-queued as pending instead of being permanently failed.

        Returns the number of rows that were in `processing` state at startup.
        """
        return await self._run(self._recover_interrupted_sync)

    def _recover_interrupted_sync(self) -> int:
        with self._connection() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'processing'"
            ).fetchone()[0]
            connection.execute(
                """UPDATE jobs
                   SET status = 'pending',
                       error_type = NULL,
                       error_message = NULL
                   WHERE status = 'processing'
                     AND (docling_task_id IS NULL OR docling_task_id = '')"""
            )
            return int(total)

    async def is_tracked(
        self,
        filename: str,
        source_size: int | None = None,
        source_mtime_ns: int | None = None,
        source_sha256: str | None = None,
    ) -> bool:
        return await self._run(
            self._is_tracked_sync,
            filename,
            source_size,
            source_mtime_ns,
            source_sha256,
        )

    def _is_tracked_sync(
        self,
        filename: str,
        source_size: int | None,
        source_mtime_ns: int | None,
        source_sha256: str | None,
    ) -> bool:
        with self._connection() as connection:
            if source_sha256:
                return connection.execute(
                    "SELECT id FROM jobs WHERE filename = ? AND source_sha256 = ? LIMIT 1",
                    (filename, source_sha256),
                ).fetchone() is not None
            if source_size is not None and source_mtime_ns is not None:
                return connection.execute(
                    """SELECT id FROM jobs
                       WHERE filename = ? AND source_size = ? AND source_mtime_ns = ?
                       LIMIT 1""",
                    (filename, source_size, source_mtime_ns),
                ).fetchone() is not None
            return connection.execute(
                "SELECT id FROM jobs WHERE filename = ? LIMIT 1", (filename,)
            ).fetchone() is not None

    async def backfill_legacy_identity(
        self,
        filename: str,
        source_size: int,
        source_mtime_ns: int,
        source_sha256: str,
    ) -> bool:
        return await self._run(
            self._backfill_legacy_identity_sync,
            filename,
            source_size,
            source_mtime_ns,
            source_sha256,
        )

    def _backfill_legacy_identity_sync(
        self,
        filename: str,
        source_size: int,
        source_mtime_ns: int,
        source_sha256: str,
    ) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT id FROM jobs
                   WHERE filename = ?
                     AND source_size IS NULL
                     AND source_mtime_ns IS NULL
                     AND source_sha256 IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (filename,),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """UPDATE jobs
                   SET source_size = ?, source_mtime_ns = ?, source_sha256 = ?
                   WHERE id = ?""",
                (source_size, source_mtime_ns, source_sha256, row["id"]),
            )
            return True

    async def create_pending(
        self,
        filename: str,
        output_formats: str | list[str],
        source_size: int | None = None,
        source_mtime_ns: int | None = None,
        source_sha256: str | None = None,
    ) -> int:
        return await self._run(
            self._create_pending_sync,
            filename,
            output_formats,
            source_size,
            source_mtime_ns,
            source_sha256,
        )

    def _create_pending_sync(
        self,
        filename: str,
        output_formats: str | list[str],
        source_size: int | None,
        source_mtime_ns: int | None,
        source_sha256: str | None,
    ) -> int:
        formats = [output_formats] if isinstance(output_formats, str) else list(output_formats)
        formats = list(dict.fromkeys(formats)) or ["md"]
        primary_format = formats[0]
        with self._connection() as connection:
            cursor = connection.execute(
                """INSERT INTO jobs (
                       filename, status, submitted_at, output_format, output_formats,
                       source_size, source_mtime_ns, source_sha256
                   ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?)""",
                (
                    filename,
                    utcnow(),
                    primary_format,
                    json.dumps(formats),
                    source_size,
                    source_mtime_ns,
                    source_sha256,
                ),
            )
            return int(cursor.lastrowid)

    async def list_pending(self) -> list[dict[str, Any]]:
        return await self._run(self._list_sync, "WHERE status = 'pending'", (), False)

    async def list_resumable(self) -> list[dict[str, Any]]:
        return await self._run(
            self._list_sync,
            "WHERE status = 'processing' AND docling_task_id IS NOT NULL AND docling_task_id != ''",
            (),
            False,
        )

    async def list_jobs(self, limit: int = 100, failures_only: bool = False) -> list[dict[str, Any]]:
        clause = (
            "WHERE status = 'failed' AND source_kind != 'converted_folder'"
            if failures_only else "WHERE source_kind != 'converted_folder'"
        )
        return await self._run(self._list_sync, clause, (limit,), True)

    def _list_sync(self, clause: str, parameters: tuple[Any, ...], use_limit: bool) -> list[dict[str, Any]]:
        suffix = " ORDER BY COALESCE(completed_at, submitted_at) DESC, id DESC" + (" LIMIT ?" if use_limit else "")
        with self._connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM jobs {clause}{suffix}", parameters
                ).fetchall()
            ]

    async def counts(self) -> dict[str, int]:
        return await self._run(self._counts_sync)

    def _counts_sync(self) -> dict[str, int]:
        counts = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
        with self._connection() as connection:
            for row in connection.execute(
                "SELECT status, COUNT(*) AS total FROM jobs WHERE source_kind != 'converted_folder' GROUP BY status"
            ):
                counts[row["status"]] = row["total"]
        return counts

    async def mark_processing(self, job_id: int) -> None:
        await self._run(
            self._execute_sync,
            """UPDATE jobs
               SET status = 'processing', completed_at = NULL,
                   error_type = NULL, error_message = NULL
               WHERE id = ?""",
            (job_id,),
        )

    async def set_task_id(self, job_id: int, task_id: str) -> None:
        await self._run(
            self._execute_sync,
            "UPDATE jobs SET docling_task_id = ? WHERE id = ?",
            (task_id, job_id),
        )

    async def set_output_filename(self, job_id: int, output_filename: str) -> None:
        await self._run(
            self._execute_sync,
            "UPDATE jobs SET output_filename = ? WHERE id = ?",
            (output_filename, job_id),
        )

    async def has_earlier_job(self, filename: str, job_id: int) -> bool:
        return await self._run(self._has_earlier_job_sync, filename, job_id)

    def _has_earlier_job_sync(self, filename: str, job_id: int) -> bool:
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM jobs WHERE filename = ? AND id < ? LIMIT 1",
                (filename, job_id),
            ).fetchone() is not None

    async def mark_completed(self, job_id: int, seconds: float, output_filename: str) -> None:
        await self._run(
            self._execute_sync,
            """UPDATE jobs
               SET status = 'completed', completed_at = ?, processing_seconds = ?,
                   output_filename = ?, error_type = NULL, error_message = NULL
               WHERE id = ?""",
            (utcnow(), seconds, output_filename, job_id),
        )

    async def mark_failed(self, job_id: int, error_type: str, message: str, seconds: float | None = None) -> None:
        await self._run(
            self._execute_sync,
            """UPDATE jobs
               SET status = 'failed', completed_at = ?, processing_seconds = ?,
                   error_type = ?, error_message = ?
               WHERE id = ?""",
            (utcnow(), seconds, error_type, message[:2000], job_id),
        )

    async def retry(self, job_id: int) -> bool:
        return await self._run(self._retry_sync, job_id)

    def _retry_sync(self, job_id: int) -> bool:
        with self._connection() as connection:
            result = connection.execute(
                """UPDATE jobs
                   SET status = 'pending', submitted_at = ?, completed_at = NULL,
                       processing_seconds = NULL, docling_task_id = NULL,
                       error_type = NULL, error_message = NULL,
                       retry_count = retry_count + 1
                   WHERE id = ? AND status = 'failed'""",
                (utcnow(), job_id),
            )
            return result.rowcount == 1

    async def _run(self, func: Any, *args: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(func, *args)

    def _execute_sync(self, statement: str, parameters: tuple[Any, ...]) -> None:
        with self._connection() as connection:
            connection.execute(statement, parameters)
