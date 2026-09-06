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


class PostprocessStore:
    """SQLite queue for post-processing completed Docling ZIPs."""

    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)
        self._lock = asyncio.Lock()

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

    async def _run(self, fn, *args):
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    async def initialize(self) -> None:
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS postprocess_jobs (
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
                    retry_count INTEGER DEFAULT 0,
                    rerun_count INTEGER DEFAULT 0,
                    last_rerun_at TEXT,
                    FOREIGN KEY(conversion_job_id) REFERENCES jobs(id)
                )"""
            )
            existing = {row[1] for row in connection.execute("PRAGMA table_info(postprocess_jobs)")}
            migrations = {
                "rerun_count": "ALTER TABLE postprocess_jobs ADD COLUMN rerun_count INTEGER DEFAULT 0",
                "last_rerun_at": "ALTER TABLE postprocess_jobs ADD COLUMN last_rerun_at TEXT",
            }
            for column, statement in migrations.items():
                if column not in existing:
                    connection.execute(statement)

            # The conversion table also gets a lightweight source marker so
            # manually dropped Docling ZIPs can be represented without
            # polluting the normal PDF conversion dashboard.
            job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "source_kind" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'watcher'"
                )

            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_postprocess_status ON postprocess_jobs(status)"
            )

    async def recover_interrupted(self) -> int:
        return await self._run(self._recover_interrupted_sync)

    def _recover_interrupted_sync(self) -> int:
        with self._connection() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM postprocess_jobs WHERE status='processing'"
            ).fetchone()[0]
            connection.execute(
                """UPDATE postprocess_jobs
                   SET status='pending', started_at=NULL,
                       error_type=NULL, error_message=NULL
                   WHERE status='processing'"""
            )
            return int(total)

    async def converted_output_needs_registration(
        self, output_filename: str, source_size: int, source_mtime_ns: int
    ) -> bool:
        return await self._run(
            self._converted_output_needs_registration_sync,
            output_filename, source_size, source_mtime_ns,
        )

    def _converted_output_needs_registration_sync(
        self, output_filename: str, source_size: int, source_mtime_ns: int
    ) -> bool:
        with self._connection() as connection:
            # Normal watcher-produced output already has a real conversion job
            # and must not be duplicated as a folder import.
            watcher = connection.execute(
                """SELECT id FROM jobs
                   WHERE output_filename=?
                     AND source_kind != 'converted_folder'
                   ORDER BY id DESC LIMIT 1""",
                (output_filename,),
            ).fetchone()
            if watcher is not None:
                return False
            imported = connection.execute(
                """SELECT id FROM jobs
                   WHERE source_kind='converted_folder'
                     AND output_filename=? AND source_size=? AND source_mtime_ns=?
                   LIMIT 1""",
                (output_filename, source_size, source_mtime_ns),
            ).fetchone()
            return imported is None

    async def register_converted_output(
        self,
        output_filename: str,
        source_size: int,
        source_mtime_ns: int,
        source_sha256: str,
        output_formats: list[str],
    ) -> int | None:
        return await self._run(
            self._register_converted_output_sync,
            output_filename, source_size, source_mtime_ns, source_sha256, output_formats,
        )

    def _register_converted_output_sync(
        self,
        output_filename: str,
        source_size: int,
        source_mtime_ns: int,
        source_sha256: str,
        output_formats: list[str],
    ) -> int | None:
        formats = list(dict.fromkeys(output_formats)) or ["json"]
        with self._connection() as connection:
            watcher = connection.execute(
                """SELECT id FROM jobs
                   WHERE output_filename=?
                     AND source_kind != 'converted_folder'
                   ORDER BY id DESC LIMIT 1""",
                (output_filename,),
            ).fetchone()
            if watcher is not None:
                return None
            existing = connection.execute(
                """SELECT id FROM jobs
                   WHERE source_kind='converted_folder' AND output_filename=?
                     AND source_sha256=? LIMIT 1""",
                (output_filename, source_sha256),
            ).fetchone()
            if existing is not None:
                return None
            now = utcnow()
            cursor = connection.execute(
                """INSERT INTO jobs (
                       filename, status, submitted_at, completed_at, output_format,
                       output_formats, output_filename, source_size, source_mtime_ns,
                       source_sha256, source_kind
                   ) VALUES (?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, 'converted_folder')""",
                (
                    output_filename, now, now, formats[0], json.dumps(formats),
                    output_filename, source_size, source_mtime_ns, source_sha256,
                ),
            )
            return int(cursor.lastrowid)

    async def discover_completed_conversions(self) -> int:
        return await self._run(self._discover_completed_conversions_sync)

    def _discover_completed_conversions_sync(self) -> int:
        created = 0
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT j.id, j.filename, j.output_filename
                   FROM jobs j
                   LEFT JOIN postprocess_jobs p ON p.conversion_job_id = j.id
                   WHERE j.status='completed'
                     AND j.output_filename IS NOT NULL
                     AND p.id IS NULL
                   ORDER BY j.id ASC"""
            ).fetchall()
            for row in rows:
                connection.execute(
                    """INSERT INTO postprocess_jobs (
                           conversion_job_id, source_filename, output_filename,
                           status, created_at
                       ) VALUES (?, ?, ?, 'pending', ?)""",
                    (row["id"], row["filename"], row["output_filename"], utcnow()),
                )
                created += 1
        return created

    async def next_pending(self) -> dict[str, Any] | None:
        return await self._run(self._next_pending_sync)

    def _next_pending_sync(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM postprocess_jobs WHERE status='pending' ORDER BY id ASC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    async def mark_processing(self, job_id: int) -> None:
        await self._run(self._mark_processing_sync, job_id)

    def _mark_processing_sync(self, job_id: int) -> None:
        with self._connection() as connection:
            connection.execute(
                """UPDATE postprocess_jobs
                   SET status='processing', started_at=?, completed_at=NULL,
                       error_type=NULL, error_message=NULL
                   WHERE id=?""",
                (utcnow(), job_id),
            )

    async def mark_completed(
        self,
        job_id: int,
        seconds: float,
        result_dir: str,
        output_sha256: str,
        profile_kind: str,
        route_count: int,
    ) -> None:
        await self._run(
            self._mark_completed_sync,
            job_id,
            seconds,
            result_dir,
            output_sha256,
            profile_kind,
            route_count,
        )

    def _mark_completed_sync(
        self,
        job_id: int,
        seconds: float,
        result_dir: str,
        output_sha256: str,
        profile_kind: str,
        route_count: int,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """UPDATE postprocess_jobs
                   SET status='completed', completed_at=?, processing_seconds=?,
                       result_dir=?, output_sha256=?, profile_kind=?, route_count=?,
                       error_type=NULL, error_message=NULL
                   WHERE id=?""",
                (
                    utcnow(), seconds, result_dir, output_sha256,
                    profile_kind, route_count, job_id,
                ),
            )

    async def mark_failed(
        self, job_id: int, error_type: str, message: str, seconds: float | None = None
    ) -> None:
        await self._run(self._mark_failed_sync, job_id, error_type, message, seconds)

    def _mark_failed_sync(
        self, job_id: int, error_type: str, message: str, seconds: float | None
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """UPDATE postprocess_jobs
                   SET status='failed', completed_at=?, processing_seconds=?,
                       error_type=?, error_message=?
                   WHERE id=?""",
                (utcnow(), seconds, error_type, message[:2000], job_id),
            )

    async def retry(self, job_id: int) -> bool:
        return await self._run(self._retry_sync, job_id)

    def _retry_sync(self, job_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """UPDATE postprocess_jobs
                   SET status='pending', started_at=NULL, completed_at=NULL,
                       processing_seconds=NULL, error_type=NULL, error_message=NULL,
                       retry_count=retry_count+1
                   WHERE id=? AND status='failed'""",
                (job_id,),
            )
            return cursor.rowcount > 0

    async def rerun(self, job_id: int) -> bool:
        return await self._run(self._rerun_sync, job_id)

    def _rerun_sync(self, job_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """UPDATE postprocess_jobs
                   SET status='pending', started_at=NULL, completed_at=NULL,
                       processing_seconds=NULL, output_sha256=NULL,
                       profile_kind=NULL, route_count=0,
                       error_type=NULL, error_message=NULL,
                       rerun_count=COALESCE(rerun_count, 0)+1, last_rerun_at=?
                   WHERE id=? AND status IN ('completed', 'failed')""",
                (utcnow(), job_id),
            )
            return cursor.rowcount > 0

    async def get_job(self, job_id: int) -> dict[str, Any] | None:
        return await self._run(self._get_job_sync, job_id)

    def _get_job_sync(self, job_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT p.*, COALESCE(j.source_kind, 'watcher') AS source_kind
                   FROM postprocess_jobs p
                   LEFT JOIN jobs j ON j.id = p.conversion_job_id
                   WHERE p.id=?""",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    async def get_conversion_job(self, conversion_job_id: int) -> dict[str, Any] | None:
        return await self._run(self._get_conversion_job_sync, conversion_job_id)

    def _get_conversion_job_sync(self, conversion_job_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (conversion_job_id,),
            ).fetchone()
            return dict(row) if row else None

    async def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._run(self._list_jobs_sync, limit)

    def _list_jobs_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT p.*, COALESCE(j.source_kind, 'watcher') AS source_kind
                   FROM postprocess_jobs p
                   LEFT JOIN jobs j ON j.id = p.conversion_job_id
                   ORDER BY COALESCE(p.completed_at, p.started_at, p.created_at) DESC, p.id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    async def counts(self) -> dict[str, int]:
        return await self._run(self._counts_sync)

    def _counts_sync(self) -> dict[str, int]:
        counts = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
        with self._connection() as connection:
            for row in connection.execute(
                "SELECT status, COUNT(*) total FROM postprocess_jobs GROUP BY status"
            ):
                counts[row["status"]] = row["total"]
        return counts
