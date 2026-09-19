import asyncio
import json
from pathlib import Path
import tempfile

from app.config import AppConfig
from app.stage2b import Stage2BWorker
from app.stage2b_store import Stage2BStore


def test_full_artifact_sweep_jobs_use_single_vision_role_queue():
    async def run():
        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            jobs = [
                {
                    "route_id": "AV000001",
                    "target": "oneplus",
                    "source": {"type": "picture", "index": 1, "artifact_sweep": True},
                },
                {
                    "route_id": "AV000002",
                    "target": "oneplus",
                    "source": {"type": "picture", "index": 2, "artifact_sweep": True},
                },
            ]
            created = await store.create_artifact_sweep_jobs(7, 7, "gen", "book__job7", "book.zip", jobs)
            assert created == 2
            assert await store.create_artifact_sweep_jobs(7, 7, "gen", "book__job7", "book.zip", jobs) == 0

            rows = await store.list_book_jobs_raw(7)
            assert len(rows) == 2
            assert {row["target"] for row in rows} == {"oneplus"}
            assert {row["code"] for row in rows} == {"FULL_TECHNICAL_VISUAL"}
            for row in rows:
                source = json.loads(row["source_json"])
                assert source["type"] == "picture"
                assert source["artifact_sweep"] is True
                assert "processor" not in source

            assert await store.start_artifact_sweep("pi5") == 0
            assert await store.start_artifact_sweep("oneplus") == 2
            rows = await store.list_book_jobs_raw(7)
            assert all(int(row["authorized"]) == 1 for row in rows)

    asyncio.run(run())


def test_legacy_artifact_sweep_processor_hint_does_not_override_selected_vision_provider(tmp_path):
    cfg = AppConfig(
        processed_dir=str(tmp_path),
        vision_verifier_provider="pi5",
        pi5_url="http://pi5.test:8080",
        oneplus_url="http://phone.test:8080",
    )
    worker = Stage2BWorker(lambda: cfg, None, None, None)
    legacy_job = {
        "id": 1,
        "result_dir": "book__job1",
        "generation": "g1",
        "postprocess_job_id": 1,
        "route_id": "AV000001",
        "source_json": json.dumps({
            "type": "picture",
            "index": 1,
            "artifact_sweep": True,
            "processor": "oneplus",
        }),
    }
    client = worker._vision_client_for_role("oneplus", legacy_job)
    assert client.provider == "pi5"
    assert client.endpoint == "http://pi5.test:8080"


def test_shared_artifact_pool_is_claimed_by_whichever_worker_is_idle():
    async def run():
        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            jobs = [
                {"route_id": f"AV{i:06d}", "target": "oneplus", "source": {"type": "picture", "index": i, "artifact_sweep": True}}
                for i in range(1, 4)
            ]
            assert await store.create_artifact_sweep_jobs(9, 9, "gen", "book__job9", "book.zip", jobs) == 3
            assert await store.start_artifact_sweep("oneplus") == 3

            # Artifact rows are not consumed by the normal role queue.
            assert await store.next_runnable("oneplus", False) is None

            pi_job = await store.claim_next_artifact("pi5")
            phone_job = await store.claim_next_artifact("oneplus")
            assert pi_job is not None and phone_job is not None
            assert pi_job["id"] != phone_job["id"]
            assert pi_job["_artifact_worker"] == "pi5"
            assert phone_job["_artifact_worker"] == "oneplus"

            # The remaining row is immediately available to whichever worker
            # asks next; there is no fixed 50/50 assignment.
            next_job = await store.claim_next_artifact("pi5")
            assert next_job is not None
            assert next_job["_artifact_worker"] == "pi5"
            assert await store.claim_next_artifact("oneplus") is None

    asyncio.run(run())


def test_failed_artifact_retry_can_move_between_workers():
    async def run():
        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            jobs = [{"route_id": "AV000001", "target": "oneplus", "source": {"type": "picture", "index": 1, "artifact_sweep": True}}]
            await store.create_artifact_sweep_jobs(10, 10, "gen", "book__job10", "book.zip", jobs)
            await store.start_artifact_sweep("oneplus")
            first = await store.claim_next_artifact("oneplus")
            assert first and first["_artifact_worker"] == "oneplus"
            await store.mark_retryable(int(first["id"]), "ConnectError", "phone offline", delay_seconds=0)
            second = await store.claim_next_artifact("pi5")
            assert second and int(second["id"]) == int(first["id"])
            assert second["_artifact_worker"] == "pi5"

    asyncio.run(run())


def test_artifact_worker_overrides_normal_vision_provider_only_for_sweeps(tmp_path):
    cfg = AppConfig(
        processed_dir=str(tmp_path),
        vision_verifier_provider="oneplus",
        pi5_url="http://pi5.test:8080",
        oneplus_url="http://phone.test:8080",
    )
    worker = Stage2BWorker(lambda: cfg, None, None, None)
    artifact_job = {
        "id": 2,
        "result_dir": "book__job2",
        "generation": "g2",
        "postprocess_job_id": 2,
        "route_id": "AV000002",
        "_artifact_worker": "pi5",
        "source_json": json.dumps({"type": "picture", "index": 2, "artifact_sweep": True}),
    }
    artifact_client = worker._vision_client_for_role("oneplus", artifact_job)
    assert artifact_client.provider == "pi5"
    assert artifact_client.endpoint == "http://pi5.test:8080"

    normal_job = dict(artifact_job)
    normal_job.pop("_artifact_worker")
    normal_job["source_json"] = json.dumps({"type": "picture", "index": 2})
    normal_client = worker._vision_client_for_role("oneplus", normal_job)
    assert normal_client.provider == "oneplus"
    assert normal_client.endpoint == "http://phone.test:8080"


def test_artifact_worker_cooldown_is_per_device(tmp_path):
    cfg = AppConfig(processed_dir=str(tmp_path), stage2b_artifact_worker_cooldown_seconds=30)
    worker = Stage2BWorker(lambda: cfg, None, None, None)
    assert worker._artifact_worker_available("pi5")
    assert worker._artifact_worker_available("oneplus")
    worker._cooldown_artifact_worker("oneplus")
    assert worker._artifact_worker_available("pi5")
    assert not worker._artifact_worker_available("oneplus")
    worker._clear_artifact_worker_cooldown("oneplus")
    assert worker._artifact_worker_available("oneplus")
