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
        # This is a migration, not a restart-time retry-budget bypass.
        await store.mark_failed(row["id"], "ConnectError", "still offline", "again.json")
        assert await store.requeue_endpoint_outage_failures() == 0
        still_failed = (await store.list_book_jobs_raw(7))[0]
        assert still_failed["status"] == "failed"

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


def test_dynamic_colab_artifact_auth_failure_opens_worker_specific_circuit(tmp_path: Path):
    from app.worker_registry import WorkerRegistry

    async def run():
        store = Stage2BStore(str(tmp_path / "jobs.db"))
        await store.initialize()
        await store.create_artifact_sweep_jobs(
            21, 21, "g", "book__job21", "book.zip",
            [{"route_id":"AV1","target":"oneplus","source":{"type":"picture","index":1,"artifact_sweep":True}}],
        )
        await store.start_artifact_sweep("oneplus")
        await store.release_ready_artifact_sweeps(21)

        cfg = AppConfig(
            processed_dir=str(tmp_path / "processed"),
            database_path=str(tmp_path / "jobs.db"),
            stage2b_endpoint_breaker_base_seconds=30,
            stage2b_endpoint_breaker_max_seconds=300,
        )
        registry = WorkerRegistry(cfg.database_path)
        item = registry.add_colab(name="Artifact GPU")
        registry.update_colab(item["id"], {
            "enabled": True,
            "url": "https://artifact.trycloudflare.com",
            "artifact_enabled": True,
        })
        registry.write_api_key(item["id"], "c" * 32)
        provider = f"colab:{item['id']}"
        events = Events()
        worker = Stage2BWorker(lambda: cfg, store, PostprocessStoreStub(), events, worker_registry=registry)
        worker._ensure_provider_state(provider)

        artifact = await store.claim_next_artifact(item["id"])
        assert artifact is not None
        artifact["_artifact_worker"] = provider

        async def unauthorized(*_args, **_kwargs):
            request = httpx.Request("POST", "https://artifact.trycloudflare.com/v1/chat/completions")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

        worker._run_oneplus = unauthorized
        await worker._run_job("oneplus", artifact, preclaimed=True, run_mode_override="artifact_shared", state_key=provider)

        saved = (await store.list_book_jobs_raw(21))[0]
        assert saved["status"] == "pending"
        assert saved["retry_count"] == 0
        assert saved["error_type"] == "ColabEndpointUnavailable"
        assert worker._endpoint_circuit[provider]["open"] is True
        assert worker._endpoint_circuit["colab"]["open"] is False

    asyncio.run(run())


def test_false_completed_colab_http_text_is_reclassified_failed_and_artifact_regated(tmp_path: Path):
    async def run():
        store = Stage2BStore(str(tmp_path / "jobs.db"))
        await store.initialize()
        await store.sync_routes(42, 9, "g", [
            {"route_id": "T1", "target": "pi5", "code": "OCR_GARBLE", "source": {"type": "text", "index": 1}},
        ], "book__job9__run2", "book.zip")
        await store.start_manual_book(42)
        row = await store.next_runnable("pi5", False)
        assert row is not None
        await store.mark_processing(row["id"], "manual", "colab:colab-1")
        result = {
            "text_provider": "colab:colab-1",
            "source_reconstruction": {
                "provider": "colab:colab-1",
                "status": "UNREADABLE",
                "error_type": "HTTPStatusError",
                "error_message": "Server error '530' for url https://worker.trycloudflare.com/v1/chat/completions",
            },
        }
        await store.mark_completed(
            row["id"], 1.2, "koboldcpp", "https://worker.trycloudflare.com", "UNCERTAIN",
            {"provider": "colab:colab-1"}, result, "old-false-result.json",
        )
        await store.create_artifact_sweep_jobs(
            42, 9, "g", "book__job9__run2", "book.zip",
            [{"route_id": "AV1", "target": "oneplus", "code": "FULL_TECHNICAL_VISUAL", "source": {"type": "picture", "index": 3}}],
        )
        with store._connection() as conn:
            conn.execute(
                "UPDATE verification_jobs SET authorized=1, run_mode='artifact_ready' WHERE postprocess_job_id=42 AND code='FULL_TECHNICAL_VISUAL'"
            )

        changed = await store.reclassify_completed_colab_transport_failures()
        assert changed == {"failed_text": 1, "artifact_regated": 1, "books": 1}
        rows = await store.list_book_jobs_raw(42)
        text_row = next(item for item in rows if item["route_id"] == "T1")
        artifact = next(item for item in rows if item["route_id"] == "AV1")
        assert text_row["status"] == "failed"
        assert text_row["error_type"] == "HTTPStatusError"
        assert text_row["verdict"] is None
        assert artifact["status"] == "pending"
        assert artifact["authorized"] == 0
        assert artifact["run_mode"] == "awaiting_normal"

        counts = await store.retry_all_failed()
        assert counts["pi5"] == 1
        retried = next(item for item in await store.list_book_jobs_raw(42) if item["route_id"] == "T1")
        assert retried["status"] == "pending"
        assert retried["authorized"] == 1
        assert retried["claimed_by"] is None
        assert retried["execution_provider"] is None

        # Migration is idempotent and must not keep rewriting history.
        assert await store.reclassify_completed_colab_transport_failures() == {
            "failed_text": 0, "artifact_regated": 0, "books": 0,
        }

    asyncio.run(run())
