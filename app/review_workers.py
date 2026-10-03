from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .anomaly_review import anomaly_evidence_signature, detect_anomaly_types
from .stage2c import _authoritative_visual_subjects, _vision_requires_human


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _utc_after(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=max(0, int(seconds)))).isoformat()


def _load_ledger_sync(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except Exception:
        return {}


def _signature(entry: dict[str, Any], review_type: str) -> str:
    if str(review_type).startswith("anomaly_"):
        base_type = "text" if str(review_type).endswith("text") else "vision"
        return "auto:" + anomaly_evidence_signature(entry, base_type)
    if review_type == "text":
        payload = {
            "entry_id": entry.get("entry_id"), "type": entry.get("entry_type"),
            "page": entry.get("page"), "source_index": entry.get("source_index"),
            "source_type": entry.get("source_type"), "table_index": entry.get("table_index"),
            "cell_index": entry.get("cell_index"), "original_text": entry.get("original_text"),
            "proposed_text": entry.get("proposed_text"), "verification_verdict": entry.get("verification_verdict"),
            "status": entry.get("status"),
        }
    else:
        payload = {
            "entry_id": entry.get("entry_id"), "type": entry.get("entry_type"),
            "page": entry.get("page"), "picture_index": entry.get("picture_index"),
            "source_index": entry.get("source_index"), "artifact": entry.get("artifact"),
            "diagram_category": entry.get("diagram_category"), "visible_text": entry.get("visible_text"),
            "visible_objects": entry.get("visible_objects"), "generated_summary": entry.get("generated_summary"),
            "verification_verdict": entry.get("verification_verdict"), "unresolved": entry.get("unresolved"),
        }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


class ReviewAssistantStore:
    def __init__(self, database_path: str) -> None:
        self.path = Path(database_path)
        self._lock = asyncio.Lock()

    @contextmanager
    def _conn(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
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
        with self._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS review_assistant_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                postprocess_job_id INTEGER NOT NULL,
                result_dir TEXT NOT NULL,
                entry_id TEXT NOT NULL,
                review_type TEXT NOT NULL,
                entry_signature TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                is_current INTEGER NOT NULL DEFAULT 1,
                claimed_by TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                processing_seconds REAL,
                result_json TEXT,
                error_type TEXT,
                error_message TEXT,
                UNIQUE(postprocess_job_id, entry_id, review_type, entry_signature)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_review_assistant_runnable ON review_assistant_jobs(is_current,status,review_type,next_attempt_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_review_assistant_book ON review_assistant_jobs(postprocess_job_id,is_current)")

    async def recover_interrupted(self) -> int:
        return await self._run(self._recover_sync)

    def _recover_sync(self) -> int:
        with self._conn() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM review_assistant_jobs WHERE is_current=1 AND status='processing'").fetchone()[0])
            conn.execute("""UPDATE review_assistant_jobs SET status='pending', claimed_by=NULL, started_at=NULL,
                         next_attempt_at=NULL, error_type='Interrupted', error_message='Recovered after restart'
                         WHERE is_current=1 AND status='processing'""")
            return count

    async def sync_candidate(self, postprocess_job_id: int, result_dir: str, entry: dict[str, Any], review_type: str) -> None:
        await self._run(self._sync_candidate_sync, postprocess_job_id, result_dir, entry, review_type)

    def _sync_candidate_sync(self, postprocess_job_id: int, result_dir: str, entry: dict[str, Any], review_type: str) -> None:
        sig = _signature(entry, review_type)
        entry_id = str(entry.get("entry_id") or "")
        if not entry_id:
            return
        with self._conn() as conn:
            if str(review_type).startswith("anomaly_"):
                base_type = "text" if str(review_type).endswith("text") else "vision"
                evidence_sig = anomaly_evidence_signature(entry, base_type)
                manual = conn.execute(
                    """SELECT entry_signature FROM review_assistant_jobs
                       WHERE postprocess_job_id=? AND entry_id=? AND review_type=?
                         AND is_current=1 AND entry_signature LIKE 'manual:%'
                       ORDER BY id DESC LIMIT 1""",
                    (postprocess_job_id, entry_id, review_type),
                ).fetchone()
                if manual is not None:
                    manual_sig = str(manual["entry_signature"] or "")
                    if manual_sig.startswith(f"manual:{evidence_sig}:"):
                        # A user-requested batch/single re-review is the newest
                        # authority for this same evidence version. Do not let
                        # background candidate sync retire it before Colab runs.
                        return
            conn.execute("""UPDATE review_assistant_jobs SET is_current=0
                         WHERE postprocess_job_id=? AND entry_id=? AND review_type=? AND entry_signature<>? AND is_current=1""",
                         (postprocess_job_id, entry_id, review_type, sig))
            conn.execute("""INSERT OR IGNORE INTO review_assistant_jobs
                         (postprocess_job_id,result_dir,entry_id,review_type,entry_signature,status,is_current,created_at)
                         VALUES (?,?,?,?,?,'pending',1,?)""",
                         (postprocess_job_id, result_dir, entry_id, review_type, sig, _utcnow()))

    async def queue_manual_anomaly(
        self, postprocess_job_id: int, result_dir: str, entry: dict[str, Any], review_type: str
    ) -> dict[str, Any]:
        return await self._run(
            self._queue_manual_anomaly_sync, postprocess_job_id, result_dir, entry, review_type
        )

    def _queue_manual_anomaly_sync(
        self, postprocess_job_id: int, result_dir: str, entry: dict[str, Any], review_type: str
    ) -> dict[str, Any]:
        if review_type not in {"anomaly_text", "anomaly_vision"}:
            raise ValueError("Manual anomaly review type must be anomaly_text or anomaly_vision")
        entry_id = str(entry.get("entry_id") or "")
        if not entry_id:
            raise ValueError("Review entry has no stable entry_id")
        base_type = "text" if review_type == "anomaly_text" else "vision"
        evidence_sig = anomaly_evidence_signature(entry, base_type)
        signature = f"manual:{evidence_sig}:{time.time_ns()}"
        with self._conn() as conn:
            existing = conn.execute(
                """SELECT * FROM review_assistant_jobs
                   WHERE postprocess_job_id=? AND entry_id=? AND review_type=?
                     AND is_current=1 AND status IN ('pending','processing')
                   ORDER BY id DESC LIMIT 1""",
                (int(postprocess_job_id), entry_id, review_type),
            ).fetchone()
            if existing and str(existing["entry_signature"]).startswith(f"manual:{evidence_sig}:"):
                return dict(existing)
            conn.execute(
                """UPDATE review_assistant_jobs SET is_current=0
                   WHERE postprocess_job_id=? AND entry_id=? AND review_type=? AND is_current=1""",
                (int(postprocess_job_id), entry_id, review_type),
            )
            cur = conn.execute(
                """INSERT INTO review_assistant_jobs
                   (postprocess_job_id,result_dir,entry_id,review_type,entry_signature,status,is_current,created_at)
                   VALUES (?,?,?,?,?,'pending',1,?)""",
                (int(postprocess_job_id), result_dir, entry_id, review_type, signature, _utcnow()),
            )
            return {
                "id": int(cur.lastrowid),
                "postprocess_job_id": int(postprocess_job_id),
                "result_dir": result_dir,
                "entry_id": entry_id,
                "review_type": review_type,
                "entry_signature": signature,
                "status": "pending",
                "is_current": 1,
            }

    async def retire_missing(self, valid: set[tuple[int, str, str]]) -> None:
        await self._run(self._retire_missing_sync, valid)

    def _retire_missing_sync(self, valid: set[tuple[int, str, str]]) -> None:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id,postprocess_job_id,entry_id,review_type,entry_signature FROM review_assistant_jobs WHERE is_current=1"
            ).fetchall()
            ids = [
                int(r["id"]) for r in rows
                if not str(r["entry_signature"] or "").startswith("manual:")
                and (int(r["postprocess_job_id"]), str(r["entry_id"]), str(r["review_type"])) not in valid
            ]
            if ids:
                conn.executemany("UPDATE review_assistant_jobs SET is_current=0 WHERE id=?", [(i,) for i in ids])

    async def claim_next(self, worker_id: str, allowed_types: set[str]) -> dict[str, Any] | None:
        return await self._run(self._claim_sync, worker_id, allowed_types)

    def _claim_sync(self, worker_id: str, allowed_types: set[str]) -> dict[str, Any] | None:
        if not allowed_types:
            return None
        placeholders = ",".join("?" for _ in allowed_types)
        args = [*sorted(allowed_types), _utcnow()]
        with self._conn() as conn:
            row = conn.execute(f"""SELECT * FROM review_assistant_jobs
                WHERE is_current=1 AND status='pending' AND review_type IN ({placeholders})
                  AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                ORDER BY CASE WHEN entry_signature LIKE 'manual:%'
                    AND review_type IN ('anomaly_text','anomaly_vision') THEN 0 ELSE 1 END,
                    CASE review_type
                    WHEN 'text' THEN 0 WHEN 'vision' THEN 1
                    WHEN 'anomaly_text' THEN 2 WHEN 'anomaly_vision' THEN 3 ELSE 4
                END, id ASC LIMIT 1""", args).fetchone()
            if row is None:
                return None
            jid = int(row["id"])
            cur = conn.execute("""UPDATE review_assistant_jobs SET status='processing', claimed_by=?, started_at=?,
                               attempt_count=attempt_count+1, next_attempt_at=NULL
                               WHERE id=? AND is_current=1 AND status='pending'""", (worker_id, _utcnow(), jid))
            if not cur.rowcount:
                return None
            out = dict(row); out.update({"status":"processing","claimed_by":worker_id,"attempt_count":int(out.get("attempt_count") or 0)+1})
            return out

    async def mark_completed(self, job_id: int, result: dict[str, Any], seconds: float) -> None:
        await self._run(self._complete_sync, job_id, result, seconds)

    def _complete_sync(self, job_id: int, result: dict[str, Any], seconds: float) -> None:
        with self._conn() as conn:
            conn.execute("""UPDATE review_assistant_jobs SET status='completed', completed_at=?, processing_seconds=?,
                         result_json=?, error_type=NULL, error_message=NULL WHERE id=?""",
                         (_utcnow(), float(seconds), json.dumps(result, ensure_ascii=False), job_id))

    async def mark_retryable(self, job_id: int, exc: Exception, delay: int = 30) -> None:
        await self._run(self._retry_sync, job_id, type(exc).__name__, str(exc), delay)

    def _retry_sync(self, job_id: int, etype: str, message: str, delay: int) -> None:
        with self._conn() as conn:
            # A bad crop or consistently malformed model response must not
            # monopolize the oldest queue positions forever. Failed reviews
            # remain visible for attention and never imply human approval.
            conn.execute("""UPDATE review_assistant_jobs
                         SET status=CASE WHEN attempt_count>=3 THEN 'failed' ELSE 'pending' END,
                         claimed_by=NULL, started_at=NULL,
                         next_attempt_at=CASE WHEN attempt_count>=3 THEN NULL ELSE ? END,
                         error_type=?, error_message=? WHERE id=?""",
                         (_utc_after(delay), etype, message[:2000], job_id))

    async def list_jobs(self, limit: int = 500) -> list[dict[str, Any]]:
        return await self._run(self._list_sync, limit)

    def _list_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("""SELECT * FROM review_assistant_jobs WHERE is_current=1
                                 ORDER BY CASE status WHEN 'processing' THEN 0 WHEN 'failed' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
                                 CASE WHEN error_message IS NOT NULL THEN 0 ELSE 1 END, id DESC LIMIT ?""", (max(1, int(limit)),)).fetchall()
            out=[]
            for row in rows:
                item=dict(row)
                try: item["result"] = json.loads(item.pop("result_json") or "{}")
                except Exception: item["result"]={}; item.pop("result_json",None)
                out.append(item)
            return out

    async def counts(self) -> dict[str, int]:
        return await self._run(self._counts_sync)

    def _counts_sync(self) -> dict[str, int]:
        with self._conn() as conn:
            rows=conn.execute("SELECT review_type,status,COUNT(*) n FROM review_assistant_jobs WHERE is_current=1 GROUP BY review_type,status").fetchall()
            out={
                "text_pending":0,"text_processing":0,"text_completed":0,
                "vision_pending":0,"vision_processing":0,"vision_completed":0,
                "anomaly_text_pending":0,"anomaly_text_processing":0,"anomaly_text_completed":0,
                "anomaly_vision_pending":0,"anomaly_vision_processing":0,"anomaly_vision_completed":0,
                "failed":0,
            }
            for r in rows:
                t=str(r["review_type"]); st=str(r["status"]); n=int(r["n"])
                key=f"{t}_{st}"
                if key in out: out[key]=n
                if st=="failed": out["failed"]+=n
            return out


class ReviewAssistantService:
    def __init__(self, config_getter, registry, store: ReviewAssistantStore, stage2b_store, postprocess_store, stage2b_worker, events) -> None:
        self._config_getter=config_getter; self._registry=registry; self._store=store
        self._stage2b_store=stage2b_store; self._postprocess_store=postprocess_store; self._worker=stage2b_worker; self._events=events
        self._stop=asyncio.Event(); self._task: asyncio.Task | None=None; self._active: dict[str, asyncio.Task]={}
        self._machine_blockers = 0

    async def start(self) -> None:
        await self._store.initialize(); await self._store.recover_interrupted()
        self._task=asyncio.create_task(self._loop(), name="review-assistant-supervisor")

    async def stop(self) -> None:
        self._stop.set()
        if self._task: self._task.cancel()
        for t in self._active.values(): t.cancel()
        await asyncio.gather(*([self._task] if self._task else [])+list(self._active.values()), return_exceptions=True)
        self._active.clear()

    async def _sync_candidates(self, settings: dict[str, Any] | None = None) -> bool:
        # The supervisor already has the registry snapshot for this cycle. Reuse
        # its review settings instead of rereading worker_registry.json, and fan
        # independent per-book ledger reads out concurrently.
        if settings is None:
            settings=(await asyncio.to_thread(self._registry.snapshot, self._config_getter())).get("review") or {}
        if not settings.get("enabled"):
            self._machine_blockers = 0
            return False
        books=await self._stage2b_store.list_books(); valid:set[tuple[int,str,str]]=set()
        blocker_keys=("text_pending","text_processing","text_failed","vision_pending","vision_processing","vision_failed","artifact_pending","artifact_processing","artifact_failed")
        self._machine_blockers = sum(sum(int(book.get(k) or 0) for k in blocker_keys) for book in books)
        # This is deliberately a global phase barrier. AI second-opinion work is
        # lower priority than every normal Text/Vision/Artifact route across the
        # library, not merely lower priority than routes from the same book.
        if self._machine_blockers:
            return False
        config = self._config_getter()
        candidates: list[tuple[int, str, Path]] = []
        for book in books:
            jid=int(book.get("postprocess_job_id") or 0); result_dir_name=str(book.get("result_dir") or "")
            if not jid or not result_dir_name: continue
            path=Path(config.processed_dir)/Path(result_dir_name).name/"correction_ledger.json"
            candidates.append((jid, result_dir_name, path))
        ledgers = await asyncio.gather(*(asyncio.to_thread(_load_ledger_sync, path) for _, _, path in candidates))
        for (jid, result_dir_name, _path), ledger in zip(candidates, ledgers):
            for entry in ledger.get("entries") or []:
                if entry.get("status")=="superseded": continue
                if entry.get("entry_type")=="text_correction" and not entry.get("human_verified") and str(entry.get("verification_verdict") or "").upper() in {"LIKELY_CORRUPT","UNCERTAIN"}:
                    key=(jid,str(entry.get("entry_id")),"text"); valid.add(key); await self._store.sync_candidate(jid,result_dir_name,entry,"text")
            vision=[e for e in (ledger.get("entries") or []) if e.get("entry_type")=="vision_enrichment" and e.get("status")!="superseded"]
            for entry in _authoritative_visual_subjects(vision):
                if _vision_requires_human(entry):
                    key=(jid,str(entry.get("entry_id")),"vision"); valid.add(key); await self._store.sync_candidate(jid,result_dir_name,entry,"vision")

            # Anomaly work is operator-triggered from the dedicated Anomaly
            # Review page. Do not auto-create anomaly jobs here. Manual anomaly
            # jobs use a "manual:" signature and retire_missing() intentionally
            # preserves them until the assigned Colab worker completes them.
        await self._store.retire_missing(valid)
        return True

    async def _run_one(self, worker_id: str, allowed: set[str]) -> None:
        try:
            job=await self._store.claim_next(worker_id,allowed)
            if not job: return
            started=time.monotonic()
            try:
                review_type=str(job["review_type"])
                if review_type.startswith("anomaly_"):
                    result=await self._worker.run_anomaly_review_job(
                        worker_id=worker_id,
                        postprocess_job_id=int(job["postprocess_job_id"]),
                        entry_id=str(job["entry_id"]),
                        review_type=("text" if review_type=="anomaly_text" else "vision"),
                        manual_requested=str(job.get("entry_signature") or "").startswith("manual:"),
                    )
                else:
                    result=await self._worker.run_review_assistant_job(
                        worker_id=worker_id, postprocess_job_id=int(job["postprocess_job_id"]),
                        entry_id=str(job["entry_id"]), review_type=review_type,
                    )
                await self._store.mark_completed(int(job["id"]),result,time.monotonic()-started)
                self._events.notify("review_assistant_job_completed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._store.mark_retryable(int(job["id"]),exc,30)
                self._events.notify("review_assistant_job_retry")
        finally:
            self._active.pop(worker_id,None)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                config=self._config_getter(); registry_snapshot=await asyncio.to_thread(self._registry.snapshot, config); settings=registry_snapshot.get("review") or {}
                worker_map={str(item.get("id")): item for item in (registry_snapshot.get("colab_workers") or [])}
                # Review workers have their own per-physical-worker pause state.
                # They are gated by completion of the primary Text/Vision/Artifact
                # workload, not by legacy Pi5/OnePlus role pause flags.
                if settings.get("enabled"):
                    machine_clear = await self._sync_candidates(settings)
                    text=set(settings.get("text_worker_ids") or []); vision=set(settings.get("vision_worker_ids") or [])
                    anomaly=set(settings.get("anomaly_worker_ids") or [])
                    if not machine_clear:
                        await asyncio.sleep(max(2,int(getattr(config,"stage2b_poll_interval_seconds",3))))
                        continue
                    for wid in sorted(text|vision|anomaly):
                        worker=worker_map.get(wid)
                        if not worker or not worker.get("enabled") or worker.get("paused"): continue
                        provider=f"colab:{wid}"
                        if provider in self._worker.dispatch_reservations: continue
                        task=self._active.get(wid)
                        if task and not task.done(): continue
                        allowed=set();
                        if wid in text: allowed.add("text")
                        if wid in vision: allowed.add("vision")
                        # Operator-requested anomaly audits may inspect existing
                        # evidence (including human decisions) while other books
                        # still await normal AI review. The primary machine gate
                        # and physical-worker reservation remain in force.
                        if wid in anomaly:
                            allowed.update({"anomaly_text","anomaly_vision"})
                        if not allowed:
                            continue
                        self._active[wid]=asyncio.create_task(self._run_one(wid,allowed), name=f"review-assistant-{wid}")
                await asyncio.sleep(max(2,int(getattr(config,"stage2b_poll_interval_seconds",3))))
            except asyncio.CancelledError: raise
            except Exception:
                await asyncio.sleep(3)

    async def status(self) -> dict[str, Any]:
        return {
            "counts":await self._store.counts(),
            "jobs":await self._store.list_jobs(300),
            "active_workers":sorted([k for k,v in self._active.items() if not v.done()]),
            "machine_work_complete": self._machine_blockers == 0,
            "machine_blockers": int(self._machine_blockers),
        }
