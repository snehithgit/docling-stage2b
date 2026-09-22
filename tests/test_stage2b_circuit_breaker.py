import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.config import AppConfig
from app.stage2b import Stage2BWorker
from app.stage2b_store import Stage2BStore


class Events:
    def __init__(self):
        self.names = []
    def notify(self, name):
        self.names.append(name)


class PostprocessStoreStub:
    async def get_job(self, *_args, **_kwargs):
        return None


async def _offline(*_args, **_kwargs):
    raise httpx.ConnectError("phone offline")


def test_endpoint_outage_defers_without_retry_budget_and_writes_one_file(tmp_path: Path):
    async def run():
        store = Stage2BStore(str(tmp_path / "jobs.db"))
        await store.initialize()
        await store.sync_routes(1, 1, "g", [
            {"route_id": "V1", "target": "oneplus", "code": "LOW_CONFIDENCE_VISUAL", "source": {"type": "picture", "index": 1}},
            {"route_id": "V2", "target": "oneplus", "code": "LOW_CONFIDENCE_VISUAL", "source": {"type": "picture", "index": 2}},
        ], "book__job1", "book.zip")
        await store.start_manual_book(1)
        cfg = AppConfig(
            processed_dir=str(tmp_path / "processed"),
            database_path=str(tmp_path / "jobs.db"),
            oneplus_url="http://127.0.0.1:9",
            stage2b_endpoint_breaker_base_seconds=30,
            stage2b_endpoint_breaker_max_seconds=300,
        )
        events = Events()
        worker = Stage2BWorker(lambda: cfg, store, PostprocessStoreStub(), events)
        worker._run_oneplus = _offline

        first = await store.next_runnable("oneplus", False)
        assert first is not None
        await worker._run_job("oneplus", first)
        rows = await store.list_book_jobs_raw(1)
        row1 = next(row for row in rows if row["route_id"] == "V1")
        assert row1["status"] == "pending"
        assert row1["retry_count"] == 0
        assert row1["error_type"] == "EndpointUnavailable"
        assert worker._endpoint_circuit["oneplus"]["open"] is True

        # Simulate another in-flight request failing during the same outage.
        second = next(row for row in rows if row["route_id"] == "V2")
        await worker._run_job("oneplus", second)
        rows = await store.list_book_jobs_raw(1)
        row2 = next(row for row in rows if row["route_id"] == "V2")
        assert row2["status"] == "pending"
        assert row2["retry_count"] == 0

        outage_files = list((tmp_path / "processed" / "_verification_outages").glob("oneplus_outage_*.json"))
        assert len(outage_files) == 1
        payload = json.loads(outage_files[0].read_text())
        assert payload["provider"] == "oneplus"
        assert payload["failure_count"] >= 1
        assert "retry_count is not consumed" in payload["note"]

    asyncio.run(run())


def test_old_connecterror_failures_are_requeued_for_outage_recovery(tmp_path: Path):
    async def run():
        store = Stage2BStore(str(tmp_path / "jobs.db"))
        await store.initialize()
        await store.sync_routes(7, 7, "g", [
            {"route_id": "V1", "target": "oneplus", "code": "LOW_CONFIDENCE_VISUAL", "source": {"type": "picture", "index": 1}},
        ], "book__job7", "book.zip")
        await store.start_manual_book(7)
        row = await store.next_runnable("oneplus", False)
        await store.mark_processing(row["id"], "manual")
        await store.mark_failed(row["id"], "ConnectError", "phone offline (retry limit exhausted)", "old.json")
        with store._connection() as conn:
            conn.execute("UPDATE verification_jobs SET retry_count=2 WHERE id=?", (row["id"],))
        assert await store.requeue_endpoint_outage_failures() == 1
        recovered = (await store.list_book_jobs_raw(7))[0]
        assert recovered["status"] == "pending"
        assert recovered["authorized"] == 1
        assert recovered["retry_count"] == 0
        assert recovered["run_mode"] == "outage_recovery"
        assert recovered["error_type"] is None

    asyncio.run(run())


def test_oneplus_workload_cooldown_defers_without_retry_budget(tmp_path: Path):
    from app.oneplus_workload import OnePlusCooldownActive

    async def run():
        store = Stage2BStore(str(tmp_path / "jobs.db"))
        await store.initialize()
        await store.sync_routes(9, 1, "g", [
            {"route_id": "V1", "target": "oneplus", "code": "LOW_CONFIDENCE_VISUAL", "source": {"type": "picture", "index": 1}},
        ], "book__job9", "book.zip")
        await store.start_manual_book(9)
        cfg = AppConfig(
            processed_dir=str(tmp_path / "processed"),
            database_path=str(tmp_path / "jobs.db"),
        )
        events = Events()
        worker = Stage2BWorker(lambda: cfg, store, PostprocessStoreStub(), events)

        async def cooling(*_args, **_kwargs):
            raise OnePlusCooldownActive(1200, "active_inference_budget_reached")

        worker._run_oneplus = cooling
        row = await store.next_runnable("oneplus", False)
        assert row is not None
        await worker._run_job("oneplus", row)
        saved = (await store.list_book_jobs_raw(9))[0]
        assert saved["status"] == "pending"
        assert saved["retry_count"] == 0
        assert saved["error_type"] == "OnePlusCooldown"
        assert "workload cooldown" in str(saved["error_message"])
        assert "stage2b_oneplus_workload_deferred" in events.names

    asyncio.run(run())
