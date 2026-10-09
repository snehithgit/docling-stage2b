from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app import main


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_status", [None, "processing", "failed", "pending"])
async def test_sequence_uses_postprocess_store_id_not_conversion_id(tmp_path, monkeypatch, blocked_status):
    directory = tmp_path / "Manual__job15__run0"
    directory.mkdir()
    job = {"id":32, "conversion_job_id":15, "status":"completed", "result_dir":directory.name}
    worker = SimpleNamespace(stage2c_state_for=lambda job_id: {}, start_stage2c_backfill=AsyncMock(return_value={"accepted":True}))
    raw_rows = [] if blocked_status else [{"id": 1, "status": "completed", "route_id": "R1", "target": "pi5"}]
    runtime = SimpleNamespace(
        config=SimpleNamespace(processed_dir=str(tmp_path), stage2c_auto_finalize_after_stage2b=True, retrieval_hybrid_enabled=False),
        postprocess_store=SimpleNamespace(list_jobs=AsyncMock(return_value=[job])),
        stage2b_store=SimpleNamespace(list_books=AsyncMock(return_value=[{"postprocess_job_id":32,"total":1, **({blocked_status: 1} if blocked_status else {})}]),list_book_jobs_raw=AsyncMock(return_value=raw_rows)),
        stage2b_worker=worker, pipeline_sequence_state={},events=SimpleNamespace(notify=lambda *args: None))
    monkeypatch.setattr(main,"stage2c_freshness",lambda *args, **kwargs: {"ready":False})
    await main.Runtime._advance_pipeline_sequence_once(runtime)
    if blocked_status:
        worker.start_stage2c_backfill.assert_not_awaited()
        runtime.stage2b_store.list_book_jobs_raw.assert_awaited_once_with(32)
    else:
        worker.start_stage2c_backfill.assert_awaited_once_with(32)
        runtime.stage2b_store.list_book_jobs_raw.assert_awaited_once_with(32)


@pytest.mark.asyncio
async def test_sequence_advances_clean_zero_route_book_after_discovery(tmp_path, monkeypatch):
    directory = tmp_path / "Manual__job15__run0"
    directory.mkdir()
    job = {"id": 32, "conversion_job_id": 15, "status": "completed", "result_dir": directory.name}
    worker = SimpleNamespace(
        book_discovery_current=lambda job_id: job_id == 32,
        stage2c_state_for=lambda job_id: {},
        start_stage2c_backfill=AsyncMock(return_value={"accepted": True}),
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            processed_dir=str(tmp_path),
            stage2c_auto_finalize_after_stage2b=True,
            retrieval_hybrid_enabled=False,
        ),
        postprocess_store=SimpleNamespace(list_jobs=AsyncMock(return_value=[job])),
        stage2b_store=SimpleNamespace(
            list_books=AsyncMock(return_value=[{"postprocess_job_id": 32, "total": 0}]),
            list_book_jobs_raw=AsyncMock(return_value=[]),
        ),
        stage2b_worker=worker,
        pipeline_sequence_state={},
        events=SimpleNamespace(notify=lambda *args: None),
    )
    monkeypatch.setattr(main, "stage2c_freshness", lambda *args, **kwargs: {"ready": False})
    await main.Runtime._advance_pipeline_sequence_once(runtime)
    worker.start_stage2c_backfill.assert_awaited_once_with(32)
    runtime.stage2b_store.list_book_jobs_raw.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_document_endpoint_does_not_list_unrelated_library_jobs(tmp_path, monkeypatch):
    raw = {"id": 7, "status": "completed", "result_dir": "book", "source_filename": "Manual.pdf"}
    post_store = SimpleNamespace(get_job=AsyncMock(return_value=raw))
    stage2b_store = SimpleNamespace(list_books=AsyncMock(return_value=[]))
    original_runtime = main.runtime
    main.runtime = SimpleNamespace(
        config=SimpleNamespace(processed_dir=str(tmp_path)),
        postprocess_store=post_store,
        stage2b_store=stage2b_store,
        pipeline_sequence_state={},
    )
    monkeypatch.setattr(main, "enrich_postprocess_jobs", lambda rows: list(rows))
    monkeypatch.setattr(main, "load_registry", lambda *_args, **_kwargs: {"equipment": []})

    async def enrich(row, _summary):
        return {**row, "pipeline": {"next_stage": "post_stage3", "stage3_ready": True}}

    async def readiness(row):
        row["readiness"] = {"search": {"lexical_ready": True}}
        return row

    monkeypatch.setattr(main, "_enrich_document_core", enrich)
    monkeypatch.setattr(main, "_apply_document_readiness", readiness)
    try:
        result = await main.document_details(7)
    finally:
        main.runtime = original_runtime

    post_store.get_job.assert_awaited_once_with(7)
    assert result["document"]["id"] == 7
    assert result["document"]["pipeline"]["next_stage"] == "assign_machine"
    assert result["document"]["readiness"]["search"]["lexical_ready"] is True


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
