import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import main
from app.book_lifecycle_lock import BookLifecycleLocks
from app.config import AppConfig
from app.stage2b import Stage2BWorker
from app.stage2b_store import Stage2BStore
from app.stage2c import apply_human_visual_decision, undo_human_visual_decision
from app.review_workers import ReviewAssistantService, ReviewAssistantStore


@pytest.mark.asyncio
async def test_cancel_requeues_only_current_processing_without_retry_penalty(tmp_path):
    store = Stage2BStore(str(tmp_path / "jobs.db"))
    await store.initialize()
    route = {"route_id": "R1", "target": "pi5", "code": "OCR_GARBLE", "source": {"type": "text", "index": 1}}
    await store.sync_routes(1, 1, "g", [route], "book", "book.zip")
    row, = await store.list_book_jobs_raw(1)
    with sqlite3.connect(store.database_path) as conn:
        conn.execute("UPDATE verification_jobs SET status='processing', attempt_count=2, retry_count=1 WHERE id=?", (row["id"],))
    await store.requeue_cancelled(row["id"])
    current = await store.get_job(row["id"])
    assert (current["status"], current["attempt_count"], current["retry_count"]) == ("pending", 1, 1)
    with sqlite3.connect(store.database_path) as conn:
        conn.execute("UPDATE verification_jobs SET status='completed' WHERE id=?", (row["id"],))
    await store.requeue_cancelled(row["id"])
    assert (await store.get_job(row["id"]))["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError("down"), httpx.HTTPStatusError("gateway", request=httpx.Request("POST", "https://worker/v1"), response=httpx.Response(530))])
async def test_review_outage_preserves_attempts_and_stops_drain(tmp_path, error):
    store = ReviewAssistantStore(str(tmp_path / "jobs.db"))
    await store.initialize()
    for i in range(3):
        await store.sync_candidate(1, "book", {"entry_id": str(i), "entry_type": "text_correction"}, "text")
    service = object.__new__(ReviewAssistantService)
    service._store = store
    service._worker = SimpleNamespace(run_review_assistant_job=AsyncMock(side_effect=error))
    service._events = SimpleNamespace(notify=lambda *args: None)
    assert await service._run_one("colab-1", {"text"}) is False
    rows = await store.list_jobs()
    assert all(row["status"] == "pending" and row["attempt_count"] == 0 for row in rows)
    assert service._endpoint_cooldowns["colab-1"]["delay"] == 30
    assert service._worker.run_review_assistant_job.await_count == 1


@pytest.mark.parametrize("action", [apply_human_visual_decision, undo_human_visual_decision])
def test_stale_visual_action_cannot_resurrect_superseded_entry(tmp_path, action):
    path = tmp_path / "correction_ledger.json"
    path.write_text(json.dumps({"entries": [{"entry_id": "old", "entry_type": "vision_enrichment", "status": "superseded", "human_verified": True, "human_visual_decision": "technical"}]}))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="reload"):
        action(tmp_path, "old", *(["technical"] if action is apply_human_visual_decision else []))
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_inference_releases_book_lock_and_rejects_deleted_book(tmp_path):
    locks = BookLifecycleLocks()
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    post = SimpleNamespace(get_job=AsyncMock(return_value={"status": "completed", "result_dir": "book"}))
    worker = Stage2BWorker(lambda: cfg, None, post, SimpleNamespace(notify=lambda *args: None), lifecycle_lock_getter=locks.get)
    lock = locks.get(1)
    async with lock:
        async with worker._review_inference_window(1):
            assert not lock.locked()
            async with lock:
                pass
        assert lock.locked()
    post.get_job.return_value = None
    async with lock:
        with pytest.raises(ValueError, match="removed"):
            async with worker._review_inference_window(1):
                pass
        assert lock.locked()


def test_pipeline_retry_backoff_and_pi5_wait(tmp_path, monkeypatch):
    rt = SimpleNamespace()
    now = 1000
    monkeypatch.setattr(main.time, "time", lambda: now)
    assert main.Runtime._pipeline_retry_ready(rt, 1, "stage3", {})
    main.Runtime._pipeline_retry_started(rt, 1, "stage3")
    assert not main.Runtime._pipeline_retry_ready(rt, 1, "stage3", {})
    now += 60
    assert main.Runtime._pipeline_retry_ready(rt, 1, "stage3", {})
    main.Runtime._pipeline_retry_started(rt, 1, "stage3")
    assert rt._pipeline_stage_retry[(1, "stage3")]["delay"] == 120
    waiting = {"status": "waiting_for_pi5", "retry_after_seconds": 600, "started_at_epoch": 1}
    assert not main.Runtime._pipeline_retry_ready(rt, 2, "stage2c", waiting)
    now += 600
    assert main.Runtime._pipeline_retry_ready(rt, 2, "stage2c", waiting)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, 502, 530, "cancel"])
async def test_normal_colab_outage_opens_only_physical_circuit(tmp_path, status):
    from app.worker_registry import WorkerRegistry
    store = Stage2BStore(str(tmp_path / "jobs.db"))
    await store.initialize()
    await store.sync_routes(1, 1, "g", [{"route_id": "R1", "target": "pi5", "code": "OCR_GARBLE", "source": {"type": "text", "index": 1}}], "book", "book.zip")
    await store.start_manual_book(1)
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"), processed_dir=str(tmp_path), text_verifier_provider="colab")
    registry = WorkerRegistry(cfg.database_path)
    info = registry.add_colab(name="GPU")
    registry.update_colab(info["id"], {"enabled": True, "url": "https://worker.example"})
    registry.write_api_key(info["id"], "c" * 32)
    provider = "colab:" + info["id"]
    worker = Stage2BWorker(lambda: cfg, store, SimpleNamespace(get_job=AsyncMock(return_value=None)), SimpleNamespace(notify=lambda *args, **kwargs: None), worker_registry=registry)
    worker._ensure_provider_state(provider)
    claim = await worker._claim_colab_normal_job(provider, ["pi5"], 0)
    if status == "cancel":
        worker._run_pi5 = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await worker._run_job("pi5", claim[1], preclaimed=True, state_key=provider)
        row = await store.get_job(claim[1]["id"])
        assert row["status"] == "pending" and row["attempt_count"] == 0
        assert row["retry_count"] == 0
        return
    if status is None:
        error = httpx.ConnectError("offline")
    else:
        request = httpx.Request("POST", "https://worker.example/v1/chat/completions")
        error = httpx.HTTPStatusError("gateway", request=request, response=httpx.Response(status, request=request))
    worker._run_pi5 = AsyncMock(side_effect=error)
    await worker._run_job("pi5", claim[1], preclaimed=True, state_key=provider)
    row = await store.get_job(claim[1]["id"])
    assert row["status"] == "pending" and row["retry_count"] == 0
    assert worker._endpoint_circuit[provider]["open"]
    assert not worker._endpoint_circuit["colab"]["open"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["normal", "artifact"])
async def test_supervisor_does_not_cancel_inflight_lane_when_worker_leaves_pool(kind):
    worker = object.__new__(Stage2BWorker)
    worker._stopping = asyncio.Event()
    worker._config_getter = lambda: SimpleNamespace(stage2b_poll_interval_seconds=.01)
    worker._paused = lambda target: False
    worker._configured_colab_providers = lambda **kwargs: []
    waiting = asyncio.Event()
    lane = asyncio.create_task(waiting.wait())
    tasks = {"colab:colab-1": lane}
    setattr(worker, "_colab_" + kind + "_tasks", tasks)
    supervisor = asyncio.create_task(getattr(worker, "_colab_" + kind + "_dispatch_loop")())
    try:
        await asyncio.sleep(.03)
        assert not lane.done()
        assert tasks["colab:colab-1"] is lane
    finally:
        worker._stopping.set()
        waiting.set()
        await asyncio.gather(lane, supervisor)


@pytest.mark.asyncio
async def test_startup_recovers_interrupted_deletion(tmp_path):
    from app.database import JobStore
    from app.postprocess_store import PostprocessStore
    path = str(tmp_path / "jobs.db")
    conversion, post = JobStore(path), PostprocessStore(path)
    await conversion.initialize()
    await post.initialize()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO jobs(id,filename,status) VALUES(1,'book.pdf','deleting')")
        conn.execute("INSERT INTO postprocess_jobs(conversion_job_id,source_filename,output_filename,status,created_at) VALUES(1,'book.pdf','book.zip','deleting','now')")
    await conversion.recover_interrupted_jobs()
    await post.recover_interrupted()
    with sqlite3.connect(path) as conn:
        for table in ("jobs", "postprocess_jobs"):
            row = conn.execute(f"SELECT status,error_type FROM {table}").fetchone()
            assert row == ("failed", "DeletionInterrupted")


@pytest.mark.asyncio
async def test_poll_404_requires_new_docling_task(tmp_path, monkeypatch):
    from app.docling_client import DoclingClient, DoclingTaskNotFoundError
    original = httpx.AsyncClient
    monkeypatch.setattr("app.docling_client.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(lambda request: httpx.Response(404))))
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    with pytest.raises(DoclingTaskNotFoundError):
        await DoclingClient(lambda: cfg).poll("lost-id")


@pytest.mark.asyncio
async def test_pipeline_one_bad_book_does_not_stop_next_book(tmp_path, monkeypatch):
    jobs = [{"id": i, "conversion_job_id": i, "status": "completed", "result_dir": str(i)} for i in (1, 2)]
    cfg = SimpleNamespace(processed_dir=str(tmp_path), stage2c_auto_finalize_after_stage2b=True, retrieval_hybrid_enabled=False)
    worker = SimpleNamespace(stage2c_state_for=lambda _: {}, start_stage2c_backfill=AsyncMock(return_value={"accepted": True}))
    completed_rows = [{"id": i, "route_id": f"R{i}", "target": "pi5", "status": "completed"} for i in (1, 2)]
    async def raw_rows(job_id):
        return [row for row in completed_rows if row["id"] == job_id]
    rt = SimpleNamespace(config=cfg, postprocess_store=SimpleNamespace(list_jobs=AsyncMock(return_value=jobs)), stage2b_store=SimpleNamespace(list_books=AsyncMock(return_value=[{"postprocess_job_id": i, "total": 1} for i in (1, 2)]), list_book_jobs_raw=AsyncMock(side_effect=raw_rows)), stage2b_worker=worker, pipeline_sequence_state={}, events=SimpleNamespace(notify=lambda *args: None))
    def identity(path, job_id, **kwargs):
        if job_id == 1:
            raise OSError("NAS unavailable")
    monkeypatch.setattr(main, "repair_identity_metadata", identity)
    monkeypatch.setattr(main, "stage2c_freshness", lambda *args, **kwargs: {"ready": False})
    await main.Runtime._advance_pipeline_sequence_once(rt)
    worker.start_stage2c_backfill.assert_awaited_once_with(2)
    assert "NAS unavailable" in rt.pipeline_sequence_state["book_errors"]["1"]


def test_semantic_signature_migration_preserves_current_chunks_and_ignores_counters(tmp_path):
    from app.pipeline_state import stage2c_freshness, stage3_freshness, verification_signature, migrate_stage3_semantic_signature
    def save(name, value):
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    state = {"status": "completed", "verification_signature": verification_signature([]), "rule_version": "test", "completed_at_epoch": 1, "processed": 0}
    save("stage2c_backfill.json", state)
    save("correction_ledger.json", {"entries": []})
    (tmp_path / "chunk_overlays.jsonl").write_text("")
    (tmp_path / "chunks.jsonl").write_text("original chunks")
    (tmp_path / "retrieval_index.jsonl").write_text("original index")
    current = stage2c_freshness(tmp_path, [])
    save("stage3_chunking.json", {"status": "completed", "stage2c_signature": current["output_signature"]})
    assert migrate_stage3_semantic_signature(tmp_path, current)
    save("stage2c_backfill.json", {**state, "completed_at_epoch": 99, "processed": 10})
    new = stage2c_freshness(tmp_path, [])
    assert new["output_signature"] != current["output_signature"]
    assert stage3_freshness(tmp_path, new)["ready"]
    assert (tmp_path / "chunks.jsonl").read_text() == "original chunks"
    save("correction_ledger.json", {"entries": [{"entry_id": "x", "human_verified": True, "proposed_text": "new content"}]})
    assert not stage3_freshness(tmp_path, stage2c_freshness(tmp_path, []))["ready"]


def test_empty_book_requires_current_successful_discovery(tmp_path):
    cfg = AppConfig(database_path=str(tmp_path / "jobs.db"))
    worker = Stage2BWorker(lambda: cfg, None, None, SimpleNamespace(notify=lambda *args: None))
    (tmp_path / "routes.json").write_text('{"routes": []}')
    assert not worker.book_discovery_current(1)
    stat = (tmp_path / "routes.json").stat()
    worker._route_sync_signatures[1] = (str(tmp_path), stat.st_mtime_ns, stat.st_size, 0, 0)
    assert worker.book_discovery_current(1)
    (tmp_path / "routes.json").write_text('{"routes": [{"route_id": "new"}]}')
    assert not worker.book_discovery_current(1)


@pytest.mark.asyncio
async def test_terminal_primary_failure_does_not_block_all_review_workers(tmp_path):
    store = ReviewAssistantStore(str(tmp_path / "jobs.db"))
    await store.initialize()
    service = object.__new__(ReviewAssistantService)
    service._store = store
    service._config_getter = lambda: SimpleNamespace(processed_dir=str(tmp_path))
    book = {"postprocess_job_id": 1, "result_dir": "book", "text_failed": 1}
    service._stage2b_store = SimpleNamespace(list_books=AsyncMock(return_value=[book]))
    service._worker = SimpleNamespace(_stage2c_ledger_lock=asyncio.Lock())
    settings = {"enabled": True, "text_worker_ids": ["colab-1"]}
    assert await service._sync_candidates(settings)
    book["text_pending"] = 1
    assert not await service._sync_candidates(settings)
