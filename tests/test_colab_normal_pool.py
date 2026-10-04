import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import AppConfig
from app.stage2b import Stage2BWorker
from app.stage2b_store import Stage2BStore


def route(route_id: str):
    return {
        "route_id": route_id,
        "target": "pi5",
        "code": "OCR_GARBLE",
        "priority": "medium",
        "source": {"type": "text", "index": int(route_id[-1]), "page": 1},
        "action": "verify",
        "reason": "pool test",
    }


def events():
    return SimpleNamespace(notify=lambda *_args, **_kwargs: None)


@pytest.mark.asyncio
async def test_two_colabs_can_preclaim_distinct_rows_from_same_text_queue():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = Stage2BStore(str(root / "jobs.db"))
        await store.initialize()
        await store.sync_routes(
            1, 1, "g1", [route("R1"), route("R2")], "book__job1", "book.zip"
        )
        await store.start_manual_batch("pi5")
        cfg = AppConfig(
            database_path=str(root / "jobs.db"),
            processed_dir=str(root / "processed"),
            text_verifier_provider="colab",
            stage2b_pi5_auto_run=False,
            stage2b_oneplus_auto_run=False,
        )
        worker = Stage2BWorker(lambda: cfg, store, SimpleNamespace(), events())

        first = await worker._claim_colab_normal_job("colab:colab-1", ["pi5"], 0)
        second = await worker._claim_colab_normal_job("colab:colab-2", ["pi5"], 0)

        assert first is not None and second is not None
        assert first[1]["id"] != second[1]["id"]
        assert first[1]["_preclaimed_processing"] is True
        assert second[1]["_preclaimed_processing"] is True
        rows = await store.list_jobs(limit=10, current_only=True)
        assert {row["status"] for row in rows} == {"processing"}
        assert {row["claimed_by"] for row in rows} == {
            "colab:colab-1", "colab:colab-2"
        }


@pytest.mark.asyncio
async def test_two_physical_colab_loops_start_same_text_backlog_concurrently():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = Stage2BStore(str(root / "jobs.db"))
        await store.initialize()
        await store.sync_routes(
            1, 1, "g1", [route("R1"), route("R2")], "book__job1", "book.zip"
        )
        await store.start_manual_batch("pi5")
        cfg = AppConfig(
            database_path=str(root / "jobs.db"),
            processed_dir=str(root / "processed"),
            text_verifier_provider="colab",
            vision_verifier_provider="oneplus",
            stage2b_poll_interval_seconds=1,
            stage2b_pi5_auto_run=False,
            stage2b_oneplus_auto_run=False,
        )
        worker = Stage2BWorker(lambda: cfg, store, SimpleNamespace(), events())

        async def ready(_provider):
            return True

        worker._physical_worker_paused = lambda _provider: False
        worker._endpoint_provider_ready = ready

        started = []
        both_started = asyncio.Event()
        release = asyncio.Event()

        async def fake_run_job(target, job, **kwargs):
            started.append((target, kwargs.get("state_key"), int(job["id"])))
            if len(started) >= 2:
                both_started.set()
            await release.wait()

        worker._run_job = fake_run_job
        task1 = asyncio.create_task(worker._colab_normal_worker_loop("colab:colab-1"))
        task2 = asyncio.create_task(worker._colab_normal_worker_loop("colab:colab-2"))
        try:
            await asyncio.wait_for(both_started.wait(), timeout=2.0)
            assert {row[1] for row in started} == {"colab:colab-1", "colab:colab-2"}
            assert len({row[2] for row in started}) == 2
        finally:
            release.set()
            worker._stopping.set()
            await asyncio.gather(task1, task2, return_exceptions=True)


@pytest.mark.asyncio
async def test_colab_pool_round_robins_text_and_vision_when_both_use_colab():
    class FakeStore:
        def __init__(self):
            self.rows = {
                "pi5": [{"id": 1, "attempt_count": 0}],
                "oneplus": [{"id": 2, "attempt_count": 0}],
            }

        async def next_runnable(self, target, _auto_run):
            rows = self.rows[target]
            return dict(rows[0]) if rows else None

        async def mark_processing(self, job_id, _run_mode, _claimed_by):
            for rows in self.rows.values():
                if rows and rows[0]["id"] == job_id:
                    rows.pop(0)
                    return True
            return False

    cfg = AppConfig(
        text_verifier_provider="colab",
        vision_verifier_provider="colab",
        stage2b_pi5_auto_run=False,
        stage2b_oneplus_auto_run=False,
    )
    worker = Stage2BWorker(lambda: cfg, FakeStore(), SimpleNamespace(), events())
    first = await worker._claim_colab_normal_job("colab:colab-1", ["pi5", "oneplus"], 0)
    second = await worker._claim_colab_normal_job(
        "colab:colab-1", ["pi5", "oneplus"], first[2]
    )
    assert first[0] == "pi5"
    assert second[0] == "oneplus"
