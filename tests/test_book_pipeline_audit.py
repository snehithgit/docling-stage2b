from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app import main


@pytest.mark.asyncio
async def test_sequence_uses_postprocess_store_id_not_conversion_id(tmp_path, monkeypatch):
    directory = tmp_path / "Manual__job15__run0"
    directory.mkdir()
    job = {"id":32, "conversion_job_id":15, "status":"completed", "result_dir":directory.name}
    worker = SimpleNamespace(stage2c_state_for=lambda job_id: {}, start_stage2c_backfill=AsyncMock(return_value={"accepted":True}))
    runtime = SimpleNamespace(
        config=SimpleNamespace(processed_dir=str(tmp_path), stage2c_auto_finalize_after_stage2b=True, retrieval_hybrid_enabled=False),
        postprocess_store=SimpleNamespace(list_jobs=AsyncMock(return_value=[job])),
        stage2b_store=SimpleNamespace(list_books=AsyncMock(return_value=[{"postprocess_job_id":32,"total":1}]),list_book_jobs_raw=AsyncMock(return_value=[])),
        stage2b_worker=worker, pipeline_sequence_state={},events=SimpleNamespace(notify=lambda *args: None))
    monkeypatch.setattr(main,"stage2c_freshness",lambda *args, **kwargs: {"ready":False})
    await main.Runtime._advance_pipeline_sequence_once(runtime)
    worker.start_stage2c_backfill.assert_awaited_once_with(32)
    runtime.stage2b_store.list_book_jobs_raw.assert_awaited_once_with(32)


@pytest.mark.asyncio
async def test_colab_artifact_counts_include_every_status(tmp_path):
    import sqlite3
    from app.stage2b_store import Stage2BStore

    store = Stage2BStore(str(tmp_path / "jobs.db"))
    await store.initialize()
    jobs = [{"route_id": f"AV{i:06}", "target": "oneplus", "source": {"type": "picture", "index": i, "artifact_sweep": True}} for i in range(1, 5)]
    await store.create_artifact_sweep_jobs(32, 15, "g", "Manual__job15__run0", "Manual.zip", jobs)
    rows = await store.list_book_jobs_raw(32)
    with sqlite3.connect(store.database_path) as connection:
        for row, status in zip(rows, ("completed", "processing", "failed", "pending")):
            connection.execute("UPDATE verification_jobs SET status=?, claimed_by='colab-2' WHERE id=?", (status, row["id"]))
    book, = await store.list_books()
    assert book["total"] == 4
    assert all(book[status] == 1 for status in ("completed", "processing", "failed", "pending"))
    assert book["pi5_completed"] + book["oneplus_completed"] == 0
