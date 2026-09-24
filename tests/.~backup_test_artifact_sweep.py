import asyncio
import json
from pathlib import Path
import tempfile
import zipfile
from types import SimpleNamespace

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
            assert all(int(row["authorized"]) == 0 for row in rows)
            assert await store.release_ready_artifact_sweeps(7) == 2
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
            assert await store.release_ready_artifact_sweeps(9) == 3

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
            assert await store.release_ready_artifact_sweeps(10) == 1
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


def test_artifact_sweep_waits_until_all_normal_routes_complete():
    async def run():
        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            await store.sync_routes(21, 21, "gen", [
                {"route_id": "T1", "target": "pi5", "code": "TEXT_REVIEW", "priority": "high", "source": {"type": "text", "index": 1}},
                {"route_id": "V1", "target": "oneplus", "code": "LOW_CONFIDENCE_VISUAL", "priority": "medium", "source": {"type": "picture", "index": 2}},
            ], "book__job21", "book.zip")
            await store.create_artifact_sweep_jobs(21, 21, "gen", "book__job21", "book.zip", [
                {"route_id": "AV000003", "target": "oneplus", "source": {"type": "picture", "index": 3, "artifact_sweep": True}},
            ])

            # Verify book authorizes only normal routes and arms the sweep.
            assert await store.start_manual_book(21) == 2
            rows = await store.list_book_jobs_raw(21)
            sweep = next(row for row in rows if row["code"] == "FULL_TECHNICAL_VISUAL")
            assert sweep["authorized"] == 0
            assert sweep["run_mode"] == "awaiting_normal"
            assert await store.release_ready_artifact_sweeps(21) == 0
            assert await store.claim_next_artifact("pi5") is None

            text = await store.next_runnable("pi5", False)
            await store.mark_processing(text["id"], "manual")
            await store.mark_completed(text["id"], 0.1, "m", "e", "LIKELY_OK", {}, {}, "text.json")
            assert await store.release_ready_artifact_sweeps(21) == 0

            vision = await store.next_runnable("oneplus", False)
            await store.mark_processing(vision["id"], "manual")
            await store.mark_completed(vision["id"], 0.1, "m", "e", "TECHNICAL_USEFUL", {}, {}, "vision.json")
            assert await store.release_ready_artifact_sweeps(21) == 1
            artifact = await store.claim_next_artifact("pi5")
            assert artifact is not None
            assert artifact["route_id"] == "AV000003"
    asyncio.run(run())


def test_artifact_claim_guard_blocks_even_if_row_is_accidentally_authorized_early():
    async def run():
        with tempfile.TemporaryDirectory() as directory:
            store = Stage2BStore(str(Path(directory) / "jobs.db"))
            await store.initialize()
            await store.sync_routes(22, 22, "gen", [
                {"route_id": "V1", "target": "oneplus", "code": "LOW_CONFIDENCE_VISUAL", "priority": "medium", "source": {"type": "picture", "index": 1}},
            ], "book__job22", "book.zip")
            await store.create_artifact_sweep_jobs(22, 22, "gen", "book__job22", "book.zip", [
                {"route_id": "AV000002", "target": "oneplus", "source": {"type": "picture", "index": 2, "artifact_sweep": True}},
            ])
            # Simulate an old/misbehaving caller authorizing the artifact early.
            with store._connection() as conn:
                conn.execute("UPDATE verification_jobs SET authorized=1 WHERE code='FULL_TECHNICAL_VISUAL'")
            assert await store.claim_next_artifact("oneplus") is None
    asyncio.run(run())


def test_route_discovery_auto_prepares_sweep_and_skips_normal_picture_overlap():
    async def run():
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            processed = root / "processed"
            output = root / "converted"
            result_dir = processed / "book__job30"
            processed.mkdir()
            output.mkdir()
            result_dir.mkdir()

            routes_payload = {
                "routes": [{
                    "route_id": "R00001",
                    "target": "oneplus",
                    "code": "LOW_CONFIDENCE_VISUAL",
                    "priority": "medium",
                    "source": {"type": "picture", "index": 0, "page": 1},
                    "action": "verify",
                    "reason": "normal picture route",
                }]
            }
            (result_dir / "routes.json").write_text(json.dumps(routes_payload), encoding="utf-8")
            (result_dir / "source_manifest.json").write_text(json.dumps({
                "converted_zip": "book.zip",
                "converted_zip_sha256": "source-sha",
            }), encoding="utf-8")

            document = {
                "texts": [],
                "pictures": [
                    {
                        "annotations": [{"kind": "classification", "predicted_classes": [{"class_name": "engineering_drawing", "confidence": 0.8}]}],
                        "prov": [{"page_no": 1}],
                        "image": {"uri": "pictures/0.png"},
                    },
                    {
                        "annotations": [{"kind": "classification", "predicted_classes": [{"class_name": "table", "confidence": 0.9}]}],
                        "prov": [{"page_no": 2}],
                        "image": {"uri": "pictures/1.png"},
                    },
                ],
            }
            with zipfile.ZipFile(output / "book.zip", "w") as archive:
                archive.writestr("book.json", json.dumps(document))

            class FakePostprocessStore:
                async def list_jobs(self, limit=-1):
                    return [{
                        "id": 30,
                        "conversion_job_id": 300,
                        "status": "completed",
                        "result_dir": "book__job30",
                        "output_filename": "book.zip",
                    }]

            store = Stage2BStore(str(root / "jobs.db"))
            await store.initialize()
            cfg = SimpleNamespace(
                processed_dir=str(processed),
                output_dir=str(output),
                stage2b_pi5_auto_run=False,
                stage2b_oneplus_auto_run=False,
            )
            worker = Stage2BWorker(
                lambda: cfg,
                store,
                FakePostprocessStore(),
                SimpleNamespace(notify=lambda *_: None),
            )
            assert await worker.sync_routes_once() == 2
            rows = await store.list_book_jobs_raw(30)
            assert {row["route_id"] for row in rows} == {"R00001", "AV000001"}
            assert all(row["route_id"] != "AV000000" for row in rows)
            sweep = next(row for row in rows if row["route_id"] == "AV000001")
            assert sweep["authorized"] == 0
            assert await worker.sync_routes_once() == 0
    asyncio.run(run())


def test_book_with_no_normal_routes_releases_prepared_sweep_without_extra_click():
    async def run():
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            processed = root / "processed"; processed.mkdir()
            output = root / "converted"; output.mkdir()
            result_dir = processed / "book__job31"; result_dir.mkdir()
            (result_dir / "routes.json").write_text(json.dumps({"routes": []}), encoding="utf-8")
            (result_dir / "source_manifest.json").write_text(json.dumps({"converted_zip": "book.zip", "converted_zip_sha256": "sha"}), encoding="utf-8")
            document = {"texts": [], "pictures": [{
                "annotations": [{"kind": "classification", "predicted_classes": [{"class_name": "engineering_drawing", "confidence": 0.9}]}],
                "prov": [{"page_no": 1}], "image": {"uri": "pictures/0.png"},
            }]}
            with zipfile.ZipFile(output / "book.zip", "w") as archive:
                archive.writestr("book.json", json.dumps(document))
            class FakePostprocessStore:
                async def list_jobs(self, limit=-1):
                    return [{"id": 31, "conversion_job_id": 310, "status": "completed", "result_dir": "book__job31", "output_filename": "book.zip"}]
            store = Stage2BStore(str(root / "jobs.db")); await store.initialize()
            cfg = SimpleNamespace(
                processed_dir=str(processed), output_dir=str(output),
                stage2b_pi5_auto_run=False, stage2b_oneplus_auto_run=False,
                stage2b_artifact_sweep_enabled=True,
            )
            worker = Stage2BWorker(lambda: cfg, store, FakePostprocessStore(), SimpleNamespace(notify=lambda *_: None))
            assert await worker.sync_routes_once() == 1
            rows = await store.list_book_jobs_raw(31)
            assert len(rows) == 1
            assert rows[0]["code"] == "FULL_TECHNICAL_VISUAL"
            assert rows[0]["authorized"] == 1
            assert rows[0]["run_mode"] == "artifact_ready"
    asyncio.run(run())
