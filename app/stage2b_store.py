from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def utc_after(seconds: int | float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=max(0, float(seconds)))).isoformat()


class Stage2BStore:
    """Persistent verification queues for Pi5 and OnePlus.

    Manual mode uses an ``authorized`` snapshot flag. Auto Run ignores that
    flag. Retryable failures are scheduled per job with ``next_attempt_at`` so
    one slow/offline route cannot monopolize a device worker and starve the
    rest of the queue.
    """

    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    async def _run(self, fn, *args):
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    async def initialize(self) -> None:
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS verification_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    postprocess_job_id INTEGER NOT NULL,
                    conversion_job_id INTEGER NOT NULL,
                    route_id TEXT NOT NULL,
                    route_key TEXT NOT NULL,
                    generation TEXT NOT NULL,
                    target TEXT NOT NULL,
                    code TEXT,
                    priority TEXT,
                    source_json TEXT NOT NULL,
                    action TEXT,
                    reason TEXT,
                    result_dir TEXT NOT NULL,
                    output_filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    authorized INTEGER NOT NULL DEFAULT 0,
                    run_mode TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    processing_seconds REAL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    model TEXT,
                    endpoint TEXT,
                    verdict TEXT,
                    request_json TEXT,
                    result_json TEXT,
                    artifact_path TEXT,
                    error_type TEXT,
                    error_message TEXT,
                    is_current INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(postprocess_job_id, generation, route_id, target)
                )"""
            )
            # Additive migration for databases created by the first Stage 2B build.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(verification_jobs)").fetchall()}
            if "retry_count" not in columns:
                conn.execute("ALTER TABLE verification_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
            if "next_attempt_at" not in columns:
                conn.execute("ALTER TABLE verification_jobs ADD COLUMN next_attempt_at TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_verification_runnable ON verification_jobs(target, is_current, status, authorized, next_attempt_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_verification_postprocess ON verification_jobs(postprocess_job_id, is_current)")

    async def recover_interrupted(self) -> int:
        return await self._run(self._recover_interrupted_sync)

    def _recover_interrupted_sync(self) -> int:
        with self._connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM verification_jobs WHERE status='processing'").fetchone()[0]
            conn.execute(
                """UPDATE verification_jobs
                   SET status='pending', started_at=NULL, next_attempt_at=NULL,
                       error_type='Interrupted', error_message='Recovered after restart'
                   WHERE status='processing'"""
            )
            return int(count)

    async def sync_routes(
        self,
        postprocess_job_id: int,
        conversion_job_id: int,
        generation: str,
        routes: list[dict[str, Any]],
        result_dir: str,
        output_filename: str,
    ) -> int:
        return await self._run(
            self._sync_routes_sync,
            postprocess_job_id,
            conversion_job_id,
            generation,
            routes,
            result_dir,
            output_filename,
        )

    def _sync_routes_sync(
        self,
        postprocess_job_id: int,
        conversion_job_id: int,
        generation: str,
        routes: list[dict[str, Any]],
        result_dir: str,
        output_filename: str,
    ) -> int:
        created = 0
        now = utcnow()
        with self._connection() as conn:
            conn.execute(
                "UPDATE verification_jobs SET is_current=0 WHERE postprocess_job_id=? AND generation<>?",
                (postprocess_job_id, generation),
            )
            for route in routes:
                target = str(route.get("target") or "")
                if target not in {"pi5", "oneplus"}:
                    continue
                route_id = str(route.get("route_id") or "")
                if not route_id:
                    continue
                source_json = json.dumps(route.get("source") or {}, ensure_ascii=False, sort_keys=True)
                route_key = f"{generation}:{target}:{route_id}"
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO verification_jobs (
                        postprocess_job_id, conversion_job_id, route_id, route_key,
                        generation, target, code, priority, source_json, action,
                        reason, result_dir, output_filename, status, authorized,
                        created_at, is_current, retry_count, next_attempt_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, 1, 0, NULL)""",
                    (
                        postprocess_job_id,
                        conversion_job_id,
                        route_id,
                        route_key,
                        generation,
                        target,
                        route.get("code"),
                        route.get("priority"),
                        source_json,
                        route.get("action"),
                        route.get("reason"),
                        result_dir,
                        output_filename,
                        now,
                    ),
                )
                if cursor.rowcount:
                    created += 1
                conn.execute(
                    """UPDATE verification_jobs
                       SET is_current=1, result_dir=?, output_filename=?,
                           code=?, priority=?, source_json=?, action=?, reason=?
                       WHERE postprocess_job_id=? AND generation=? AND route_id=? AND target=?""",
                    (
                        result_dir,
                        output_filename,
                        route.get("code"),
                        route.get("priority"),
                        source_json,
                        route.get("action"),
                        route.get("reason"),
                        postprocess_job_id,
                        generation,
                        route_id,
                        target,
                    ),
                )
        return created

    async def clear_manual_authorizations(self, target: str) -> int:
        return await self._run(self._clear_manual_authorizations_sync, target)

    def _clear_manual_authorizations_sync(self, target: str) -> int:
        with self._connection() as conn:
            cursor = conn.execute(
                """UPDATE verification_jobs
                   SET authorized=0, run_mode=NULL
                   WHERE target=? AND is_current=1 AND status='pending' AND authorized=1""",
                (target,),
            )
            return int(cursor.rowcount)

    async def start_manual_batch(self, target: str) -> int:
        return await self._run(self._start_manual_batch_sync, target)

    def _start_manual_batch_sync(self, target: str) -> int:
        with self._connection() as conn:
            cursor = conn.execute(
                """UPDATE verification_jobs
                   SET authorized=1, run_mode='manual'
                   WHERE target=? AND is_current=1 AND status='pending' AND authorized=0""",
                (target,),
            )
            return int(cursor.rowcount)

    async def start_manual_book(self, postprocess_job_id: int, target: str | None = None) -> int:
        return await self._run(self._start_manual_book_sync, postprocess_job_id, target)

    def _start_manual_book_sync(self, postprocess_job_id: int, target: str | None) -> int:
        with self._connection() as conn:
            params: list[Any] = [postprocess_job_id]
            target_sql = ""
            if target is not None:
                target_sql = " AND target=?"
                params.append(target)
            cursor = conn.execute(
                f"""UPDATE verification_jobs
                    SET authorized=1, run_mode='manual', next_attempt_at=NULL
                    WHERE postprocess_job_id=? AND is_current=1
                      AND status='pending'{target_sql}""",
                params,
            )
            return int(cursor.rowcount)

    async def next_runnable(self, target: str, auto_run: bool) -> dict[str, Any] | None:
        return await self._run(self._next_runnable_sync, target, auto_run)

    def _next_runnable_sync(self, target: str, auto_run: bool) -> dict[str, Any] | None:
        with self._connection() as conn:
            clause = "1=1" if auto_run else "authorized=1"
            now = utcnow()
            row = conn.execute(
                f"""SELECT * FROM verification_jobs
                    WHERE target=? AND is_current=1 AND status='pending' AND ({clause})
                      AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                    ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                             id ASC
                    LIMIT 1""",
                (target, now),
            ).fetchone()
            return dict(row) if row else None

    async def mark_processing(self, job_id: int, run_mode: str) -> None:
        await self._run(self._mark_processing_sync, job_id, run_mode)

    def _mark_processing_sync(self, job_id: int, run_mode: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """UPDATE verification_jobs
                   SET status='processing', started_at=?, completed_at=NULL,
                       attempt_count=attempt_count+1, run_mode=?, next_attempt_at=NULL,
                       error_type=NULL, error_message=NULL
                   WHERE id=?""",
                (utcnow(), run_mode, job_id),
            )

    async def mark_completed(
        self,
        job_id: int,
        seconds: float,
        model: str | None,
        endpoint: str,
        verdict: str,
        request: dict[str, Any],
        result: dict[str, Any],
        artifact_path: str,
    ) -> None:
        await self._run(
            self._mark_completed_sync,
            job_id,
            seconds,
            model,
            endpoint,
            verdict,
            json.dumps(request, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            artifact_path,
        )

    def _mark_completed_sync(self, job_id, seconds, model, endpoint, verdict, request_json, result_json, artifact_path) -> None:
        with self._connection() as conn:
            conn.execute(
                """UPDATE verification_jobs
                   SET status='completed', completed_at=?, processing_seconds=?,
                       model=?, endpoint=?, verdict=?, request_json=?, result_json=?,
                       artifact_path=?, authorized=0, retry_count=0, next_attempt_at=NULL,
                       error_type=NULL, error_message=NULL
                   WHERE id=?""",
                (utcnow(), seconds, model, endpoint, verdict, request_json, result_json, artifact_path, job_id),
            )

    async def mark_retryable(self, job_id: int, error_type: str, error_message: str, delay_seconds: int = 15, artifact_path: str | None = None) -> None:
        await self._run(self._mark_retryable_sync, job_id, error_type, error_message, delay_seconds, artifact_path)

    def _mark_retryable_sync(self, job_id: int, error_type: str, error_message: str, delay_seconds: int, artifact_path: str | None) -> None:
        with self._connection() as conn:
            conn.execute(
                """UPDATE verification_jobs
                   SET status='pending', started_at=NULL, retry_count=retry_count+1,
                       next_attempt_at=?, error_type=?, error_message=?, artifact_path=?
                   WHERE id=?""",
                (utc_after(delay_seconds), error_type, error_message[:2000], artifact_path, job_id),
            )

    async def mark_failed(self, job_id: int, error_type: str, error_message: str, artifact_path: str | None = None) -> None:
        await self._run(self._mark_failed_sync, job_id, error_type, error_message, artifact_path)

    def _mark_failed_sync(self, job_id: int, error_type: str, error_message: str, artifact_path: str | None) -> None:
        with self._connection() as conn:
            conn.execute(
                """UPDATE verification_jobs
                   SET status='failed', completed_at=?, authorized=0, next_attempt_at=NULL,
                       error_type=?, error_message=?, artifact_path=?
                   WHERE id=?""",
                (utcnow(), error_type, error_message[:2000], artifact_path, job_id),
            )

    async def rerun(self, job_id: int) -> bool:
        return await self._run(self._rerun_sync, job_id)

    def _rerun_sync(self, job_id: int) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                """UPDATE verification_jobs
                   SET status='pending', authorized=1, run_mode='manual',
                       started_at=NULL, completed_at=NULL, processing_seconds=NULL,
                       verdict=NULL, request_json=NULL, result_json=NULL, artifact_path=NULL,
                       retry_count=0, next_attempt_at=NULL,
                       error_type=NULL, error_message=NULL
                   WHERE id=? AND is_current=1 AND status IN ('completed','failed')""",
                (job_id,),
            )
            return bool(cursor.rowcount)

    async def retry(self, job_id: int) -> bool:
        return await self._run(self._retry_sync, job_id)

    def _retry_sync(self, job_id: int) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                """UPDATE verification_jobs
                   SET status='pending', authorized=1, run_mode='manual',
                       started_at=NULL, completed_at=NULL, retry_count=0, next_attempt_at=NULL,
                       error_type=NULL, error_message=NULL
                   WHERE id=? AND status='failed' AND is_current=1""",
                (job_id,),
            )
            return bool(cursor.rowcount)

    async def counts(self) -> dict[str, dict[str, int]]:
        return await self._run(self._counts_sync)

    def _counts_sync(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        with self._connection() as conn:
            for target in ("pi5", "oneplus"):
                rows = conn.execute(
                    """SELECT status, COUNT(*) AS n FROM verification_jobs
                       WHERE target=? AND is_current=1 GROUP BY status""",
                    (target,),
                ).fetchall()
                counts = {row["status"]: int(row["n"]) for row in rows}
                counts["total"] = sum(counts.values())
                counts["waiting_manual"] = int(
                    conn.execute(
                        """SELECT COUNT(*) FROM verification_jobs
                           WHERE target=? AND is_current=1 AND status='pending' AND authorized=0""",
                        (target,),
                    ).fetchone()[0]
                )
                counts["backoff"] = int(
                    conn.execute(
                        """SELECT COUNT(*) FROM verification_jobs
                           WHERE target=? AND is_current=1 AND status='pending'
                             AND next_attempt_at IS NOT NULL AND next_attempt_at>?""",
                        (target, utcnow()),
                    ).fetchone()[0]
                )
                result[target] = counts
        return result

    async def list_jobs(self, limit: int = 100, current_only: bool = True) -> list[dict[str, Any]]:
        return await self._run(self._list_jobs_sync, limit, current_only)

    def _list_jobs_sync(self, limit: int, current_only: bool) -> list[dict[str, Any]]:
        where = "WHERE is_current=1" if current_only else ""
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM verification_jobs {where} ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._public_row(row) for row in rows]

    async def list_results(self, target: str, limit: int = 5000) -> list[dict[str, Any]]:
        return await self._run(self._list_results_sync, target, limit)

    def _list_results_sync(self, target: str, limit: int) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT * FROM verification_jobs
                   WHERE target=? AND is_current=1 AND status IN ('completed','failed')
                   ORDER BY CASE status WHEN 'failed' THEN 0 ELSE 1 END, id DESC LIMIT ?""",
                (target, limit),
            ).fetchall()
            return [self._public_row(row) for row in rows]

    async def list_remaining(self, target: str, limit: int = 5000) -> list[dict[str, Any]]:
        return await self._run(self._list_remaining_sync, target, limit)

    def _list_remaining_sync(self, target: str, limit: int) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT * FROM verification_jobs
                   WHERE target=? AND is_current=1 AND status IN ('pending','processing','failed')
                   ORDER BY CASE status WHEN 'processing' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END,
                            CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                            id ASC LIMIT ?""",
                (target, limit),
            ).fetchall()
            return [self._public_row(row) for row in rows]

    async def list_books(self) -> list[dict[str, Any]]:
        return await self._run(self._list_books_sync)

    def _list_books_sync(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT postprocess_job_id, result_dir, output_filename,
                          SUM(CASE WHEN target='pi5' AND status='pending' THEN 1 ELSE 0 END) AS pi5_pending,
                          SUM(CASE WHEN target='pi5' AND status='processing' THEN 1 ELSE 0 END) AS pi5_processing,
                          SUM(CASE WHEN target='pi5' AND status='completed' THEN 1 ELSE 0 END) AS pi5_completed,
                          SUM(CASE WHEN target='pi5' AND status='failed' THEN 1 ELSE 0 END) AS pi5_failed,
                          SUM(CASE WHEN target='oneplus' AND status='pending' THEN 1 ELSE 0 END) AS oneplus_pending,
                          SUM(CASE WHEN target='oneplus' AND status='processing' THEN 1 ELSE 0 END) AS oneplus_processing,
                          SUM(CASE WHEN target='oneplus' AND status='completed' THEN 1 ELSE 0 END) AS oneplus_completed,
                          SUM(CASE WHEN target='oneplus' AND status='failed' THEN 1 ELSE 0 END) AS oneplus_failed,
                          COUNT(*) AS total
                   FROM verification_jobs
                   WHERE is_current=1
                   GROUP BY postprocess_job_id, result_dir, output_filename
                   ORDER BY postprocess_job_id DESC"""
            ).fetchall()
            return [dict(row) for row in rows]

    def _public_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["source"] = json.loads(item.pop("source_json") or "{}")
        except json.JSONDecodeError:
            item["source"] = {}
        item.pop("request_json", None)
        item.pop("result_json", None)
        return item

    async def get_job(self, job_id: int) -> dict[str, Any] | None:
        return await self._run(self._get_job_sync, job_id)

    def _get_job_sync(self, job_id: int) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None
