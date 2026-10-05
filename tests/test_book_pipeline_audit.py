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
