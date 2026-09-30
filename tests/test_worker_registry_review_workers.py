import asyncio
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

from app.config import AppConfig
from app.review_workers import ReviewAssistantService, ReviewAssistantStore
from app.stage2b import Stage2BWorker
from app.worker_registry import WorkerRegistry


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        database_path=str(tmp_path / "jobs.db"),
        processed_dir=str(tmp_path / "processed"),
        input_dir=str(tmp_path / "input"),
        output_dir=str(tmp_path / "output"),
    )


def test_worker_registry_supports_independent_local_and_multiple_colab_controls(tmp_path: Path):
    cfg = _config(tmp_path)
    registry = WorkerRegistry(cfg.database_path)

    registry.update_local("pi5", paused=True, artifact_enabled=False)
    registry.update_local("oneplus", paused=False, artifact_enabled=True)
    assert registry.local_state("pi5") == {"paused": True, "artifact_enabled": False}
    assert registry.local_state("oneplus") == {"paused": False, "artifact_enabled": True}

    first = registry.add_colab(name="Review GPU")
    second = registry.add_colab(name="Sweep GPU")
    assert first["id"] == "colab-1"
    assert second["id"] == "colab-2"

    key1 = "a" * 32
    key2 = "b" * 32
    registry.update_colab(first["id"], {
        "enabled": True, "url": "https://one.trycloudflare.com/v1",
        "model": "koboldcpp", "artifact_enabled": False,
    })
    registry.update_colab(second["id"], {
        "enabled": True, "url": "https://two.trycloudflare.com",
        "model": "koboldcpp", "artifact_enabled": True,
    })
    registry.write_api_key(first["id"], key1)
    registry.write_api_key(second["id"], key2)

    assert registry.read_api_key(first["id"]) == key1
    assert registry.read_api_key(second["id"]) == key2
    assert stat.S_IMODE(os.stat(registry.secret_path(first["id"])).st_mode) == 0o600
    assert [w["id"] for w in registry.configured_colabs(cfg)] == ["colab-1", "colab-2"]
    assert [w["id"] for w in registry.configured_colabs(cfg, artifact_only=True)] == ["colab-2"]

    review = registry.update_review(
        enabled=True,
        text_worker_ids=[first["id"], second["id"]],
        vision_worker_ids=[first["id"]],
        anomaly_worker_ids=[second["id"]],
    )
    assert review["text_worker_ids"] == ["colab-1", "colab-2"]
    assert review["vision_worker_ids"] == ["colab-1"]
    assert review["anomaly_worker_ids"] == ["colab-2"]
    assert review["require_machine_complete"] is True

    registry.update_colab(first["id"], {"paused": True})
    assert [w["id"] for w in registry.configured_colabs(cfg)] == ["colab-2"]
    assert [w["id"] for w in registry.configured_colabs(cfg, include_paused=True)] == ["colab-1", "colab-2"]

    assert registry.remove_colab(first["id"]) is True
    snap = registry.snapshot(cfg)
    assert [w["id"] for w in snap["colab_workers"]] == ["colab-2"]
    assert snap["review"]["text_worker_ids"] == ["colab-2"]
    assert snap["review"]["vision_worker_ids"] == []
    assert snap["review"]["anomaly_worker_ids"] == ["colab-2"]


def test_stage2b_worker_uses_registry_artifact_participation_and_multiple_colab_pool(tmp_path: Path):
    cfg = _config(tmp_path)
    registry = WorkerRegistry(cfg.database_path)
    a = registry.add_colab(name="A")
    b = registry.add_colab(name="B")
    for worker, artifact in ((a, False), (b, True)):
        registry.update_colab(worker["id"], {
            "enabled": True,
            "url": f"https://{worker['id']}.trycloudflare.com",
            "model": "koboldcpp",
            "artifact_enabled": artifact,
        })
        registry.write_api_key(worker["id"], (worker["id"].replace("-", "") * 8)[:32])

    registry.update_local("pi5", artifact_enabled=False)
    worker = Stage2BWorker(lambda: cfg, None, None, None, worker_registry=registry)
    assert worker._artifact_participates("pi5") is False
    assert worker._artifact_participates("oneplus") is True
    assert worker._artifact_participates("colab:colab-1") is False
    assert worker._artifact_participates("colab:colab-2") is True
    assert worker._configured_colab_providers() == ["colab:colab-1", "colab:colab-2"]
    assert worker._configured_colab_providers(artifact_only=True) == ["colab:colab-2"]

    registry.update_colab("colab-2", {"paused": True})
    assert worker._physical_worker_paused("colab:colab-2") is True
    assert worker._configured_colab_providers(artifact_only=True) == []


class _Books:
    def __init__(self, rows):
        self.rows = rows
    async def list_books(self):
        return list(self.rows)


class _UnusedPostprocess:
    pass


class _UnusedWorker:
    dispatch_reservations = {}


class _Events:
    def notify(self, *args, **kwargs):
        pass


def test_review_assistant_waits_until_text_vision_and_artifact_machine_jobs_finish(tmp_path: Path):
    async def run():
        cfg = _config(tmp_path)
        Path(cfg.processed_dir).mkdir(parents=True)
        result_dir = Path(cfg.processed_dir) / "book__job7"
        result_dir.mkdir()
        ledger = {
            "entries": [
                {
                    "entry_id": "g:text:R1", "entry_type": "text_correction",
                    "status": "pending", "human_verified": False,
                    "verification_verdict": "UNCERTAIN", "page": 2,
                    "source_index": 4, "source_type": "text",
                    "original_text": "PUMP PRESURE", "proposed_text": "PUMP PRESSURE",
                },
                {
                    "entry_id": "g:vision:V1", "entry_type": "vision_enrichment",
                    "status": "pending", "verification_verdict": "UNCERTAIN",
                    "unresolved": True, "page": 3, "source_index": 1,
                    "picture_index": 1, "generated_summary": "uncertain diagram",
                },
            ]
        }
        (result_dir / "correction_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")

        registry = WorkerRegistry(cfg.database_path)
        w = registry.add_colab(name="Reviewer")
        registry.update_colab(w["id"], {"enabled": True, "url": "https://review.trycloudflare.com"})
        registry.write_api_key(w["id"], "r" * 32)
        registry.update_review(
            enabled=True, text_worker_ids=[w["id"]], vision_worker_ids=[w["id"]],
            anomaly_worker_ids=[w["id"]],
        )

        blocked = {
            "postprocess_job_id": 7, "result_dir": "book__job7",
            "text_pending": 0, "text_processing": 0, "text_failed": 0,
            "vision_pending": 0, "vision_processing": 0, "vision_failed": 0,
            "artifact_pending": 1, "artifact_processing": 0, "artifact_failed": 0,
        }
        store = ReviewAssistantStore(cfg.database_path)
        await store.initialize()
        books = _Books([blocked])
        service = ReviewAssistantService(
            lambda: cfg, registry, store, books, _UnusedPostprocess(), _UnusedWorker(), _Events()
        )
        await service._sync_candidates()
        assert (await store.counts())["text_pending"] == 0
        assert (await store.counts())["vision_pending"] == 0

        ready = dict(blocked)
        ready["artifact_pending"] = 0
        # The review phase is globally deferred: a different book with any
        # primary Text/Vision/Artifact work still blocks all AI review workers.
        other_blocked = dict(ready)
        other_blocked.update({"postprocess_job_id": 8, "result_dir": "other__job8", "vision_pending": 1})
        books.rows = [ready, other_blocked]
        assert await service._sync_candidates() is False
        assert (await store.counts())["text_pending"] == 0
        assert service._machine_blockers == 1

        books.rows = [ready]
        assert await service._sync_candidates() is True
        counts = await store.counts()
        assert counts["text_pending"] == 1
        assert counts["vision_pending"] == 1
        jobs = await store.list_jobs()
        assert {j["review_type"] for j in jobs} == {"text", "vision"}

    asyncio.run(run())


def test_legacy_colab_import_is_one_time_and_deleted_worker_stays_deleted(tmp_path: Path):
    key_path = tmp_path / "legacy-colab.key"
    key_path.write_text("k" * 32 + "\n", encoding="utf-8")
    cfg = AppConfig(
        database_path=str(tmp_path / "jobs.db"),
        processed_dir=str(tmp_path / "processed"),
        input_dir=str(tmp_path / "input"),
        output_dir=str(tmp_path / "output"),
        colab_enabled=True,
        colab_url="https://legacy.trycloudflare.com",
        colab_api_key_path=str(key_path),
    )
    registry = WorkerRegistry(cfg.database_path)

    registry.ensure_legacy_colab(cfg)
    snap = registry.snapshot(cfg)
    assert snap["legacy_colab_import_done"] is True
    assert [w["id"] for w in snap["colab_workers"]] == ["colab-1"]

    assert registry.remove_colab("colab-1") is True
    # A later status/snapshot call must not recreate the deleted worker from
    # the still-populated legacy config fields.
    snap = registry.snapshot(cfg)
    assert snap["legacy_colab_import_done"] is True
    assert snap["colab_workers"] == []


def test_busy_colab_removal_drains_and_is_finalized_when_reservation_releases(tmp_path: Path):
    async def run():
        cfg = _config(tmp_path)
        registry = WorkerRegistry(cfg.database_path)
        item = registry.add_colab(name="Busy GPU")
        registry.update_colab(item["id"], {
            "enabled": True,
            "url": "https://busy.trycloudflare.com",
            "artifact_enabled": True,
        })
        registry.write_api_key(item["id"], "z" * 32)
        registry.update_review(
            enabled=True,
            text_worker_ids=[item["id"]],
            vision_worker_ids=[item["id"]],
            anomaly_worker_ids=[item["id"]],
        )

        events = SimpleNamespace(notify=lambda *args, **kwargs: None)
        worker = Stage2BWorker(
            lambda: cfg, None, None, events, worker_registry=registry
        )
        provider = f"colab:{item['id']}"
        worker._ensure_provider_state(provider)
        worker._provider_reservations[provider] = "test-owner"

        requested = registry.request_remove_colab(item["id"])
        assert requested["paused"] is True
        assert requested["remove_requested"] is True
        assert registry.configured_colabs(cfg) == []
        snap = registry.snapshot(cfg)
        assert snap["review"]["text_worker_ids"] == []
        assert snap["review"]["vision_worker_ids"] == []
        assert snap["review"]["anomaly_worker_ids"] == []

        await worker._release_provider(provider, "test-owner")
        assert registry.get_colab(item["id"]) is None
        assert provider not in worker.dispatch_reservations
        assert not registry.secret_path(item["id"]).exists()

    asyncio.run(run())


def test_worker_registry_hot_snapshot_reads_registry_and_each_key_once(tmp_path: Path):
    cfg = _config(tmp_path)
    registry = WorkerRegistry(cfg.database_path)
    first = registry.add_colab(name="A")
    second = registry.add_colab(name="B")
    for item, key in ((first, "a" * 32), (second, "b" * 32)):
        registry.update_colab(item["id"], {"enabled": True, "url": f"https://{item['id']}.trycloudflare.com"})
        registry.write_api_key(item["id"], key)

    read_count = 0
    key_reads: dict[str, int] = {}
    original_read = registry._read_unlocked
    original_secret_path = registry.secret_path

    def counted_read():
        nonlocal read_count
        read_count += 1
        return original_read()

    class CountingSecret:
        def __init__(self, worker_id: str):
            self.worker_id = worker_id
            self.path = original_secret_path(worker_id)
        def read_text(self, *args, **kwargs):
            key_reads[self.worker_id] = key_reads.get(self.worker_id, 0) + 1
            return self.path.read_text(*args, **kwargs)

    registry._read_unlocked = counted_read  # type: ignore[method-assign]
    registry.secret_path = lambda worker_id: CountingSecret(worker_id)  # type: ignore[method-assign]
    status = registry.snapshot_with_key_status()
    assert read_count == 1
    assert key_reads == {"colab-1": 1, "colab-2": 1}
    assert [item["id"] for item in status["configured_colabs"]] == ["colab-1", "colab-2"]


def test_colab_admin_update_serializes_against_scheduler_reservation(tmp_path: Path):
    import threading

    async def run():
        cfg = _config(tmp_path)
        registry = WorkerRegistry(cfg.database_path)
        item = registry.add_colab(name="Race GPU")
        registry.update_colab(item["id"], {"enabled": True, "url": "https://race.trycloudflare.com"})
        registry.write_api_key(item["id"], "r" * 32)
        worker = Stage2BWorker(lambda: cfg, None, None, SimpleNamespace(notify=lambda *a, **k: None), worker_registry=registry)
        provider = f"colab:{item['id']}"

        entered = threading.Event()
        release = threading.Event()
        original_get = registry.get_colab

        def slow_get(worker_id: str):
            entered.set()
            release.wait(timeout=2)
            return original_get(worker_id)

        registry.get_colab = slow_get  # type: ignore[method-assign]
        update_task = asyncio.create_task(worker.update_colab_worker_admin(item["id"], {"model": "new-model"}))
        assert await asyncio.to_thread(entered.wait, 1.0)
        reserve_task = asyncio.create_task(worker._reserve_provider(provider, "race-owner"))
        await asyncio.sleep(0.05)
        assert reserve_task.done() is False
        release.set()
        updated = await update_task
        assert updated and updated["model"] == "new-model"
        assert await reserve_task is True
        await worker._release_provider(provider, "race-owner")

    asyncio.run(run())


def test_colab_delete_blocks_stale_scheduler_claim_after_removal(tmp_path: Path):
    import threading

    async def run():
        cfg = _config(tmp_path)
        registry = WorkerRegistry(cfg.database_path)
        item = registry.add_colab(name="Delete Race")
        registry.update_colab(item["id"], {"enabled": True, "url": "https://delete.trycloudflare.com"})
        registry.write_api_key(item["id"], "d" * 32)
        worker = Stage2BWorker(lambda: cfg, None, None, SimpleNamespace(notify=lambda *a, **k: None), worker_registry=registry)
        provider = f"colab:{item['id']}"

        entered = threading.Event()
        release = threading.Event()
        original_remove = registry.remove_colab

        def slow_remove(worker_id: str):
            entered.set()
            release.wait(timeout=2)
            return original_remove(worker_id)

        registry.remove_colab = slow_remove  # type: ignore[method-assign]
        delete_task = asyncio.create_task(worker.remove_colab_worker_admin(item["id"]))
        assert await asyncio.to_thread(entered.wait, 1.0)
        stale_claim = asyncio.create_task(worker._reserve_provider(provider, "stale-owner"))
        await asyncio.sleep(0.05)
        assert stale_claim.done() is False
        release.set()
        result = await delete_task
        assert result["state"] == "deleted"
        assert await stale_claim is False
        assert provider not in worker.dispatch_reservations

    asyncio.run(run())


def test_review_assistant_pending_predicate_rejects_human_resolved_entries(tmp_path: Path):
    cfg = _config(tmp_path)
    worker = Stage2BWorker(lambda: cfg, None, None, SimpleNamespace(notify=lambda *a, **k: None))
    text = {"entries": [{
        "entry_id": "t1", "entry_type": "text_correction", "status": "pending",
        "verification_verdict": "UNCERTAIN", "human_verified": False,
    }]}
    assert worker._review_entry_still_needs_assistance(text, "t1", "text") is True
    text["entries"][0]["human_verified"] = True
    assert worker._review_entry_still_needs_assistance(text, "t1", "text") is False

    vision = {"entries": [{
        "entry_id": "v1", "entry_type": "vision_enrichment", "status": "pending",
        "verification_verdict": "UNCERTAIN", "unresolved": True,
        "picture_index": 1,
    }]}
    assert worker._review_entry_still_needs_assistance(vision, "v1", "vision") is True
    vision["entries"][0]["human_visual_decision"] = "technical"
    assert worker._review_entry_still_needs_assistance(vision, "v1", "vision") is False


def test_manual_anomaly_job_survives_automatic_candidate_retirement(tmp_path: Path):
    async def run():
        cfg = _config(tmp_path)
        store = ReviewAssistantStore(cfg.database_path)
        await store.initialize()
        entry = {
            "entry_id": "g:text:R9", "entry_type": "text_correction",
            "verification_verdict": "UNCERTAIN", "original_text": "PUMP PRESURE",
            "proposed_text": "PUMP PRESSURE", "human_verified": True,
        }
        queued = await store.queue_manual_anomaly(
            9, "book__job9", entry, "anomaly_text"
        )
        assert queued["status"] == "pending"
        await store.retire_missing(set())
        jobs = await store.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["review_type"] == "anomaly_text"
        assert jobs[0]["status"] == "pending"
        assert str(jobs[0]["entry_signature"]).startswith("manual:")

    asyncio.run(run())


def test_anomaly_job_is_automatic_third_pass_after_normal_review(tmp_path: Path):
    async def run():
        cfg = _config(tmp_path)
        Path(cfg.processed_dir).mkdir(parents=True)
        result_dir = Path(cfg.processed_dir) / "book__job11"
        result_dir.mkdir()
        entry = {
            "entry_id": "g:text:R1", "entry_type": "text_correction",
            "status": "pending", "human_verified": False,
            "verification_verdict": "LIKELY_CORRUPT", "page": 2,
            "source_index": 4, "source_type": "text",
            "original_text": "PUMP PRESURE", "proposed_text": "PUMP PRESSURE",
            "ai_review_assistant": {
                "recommendation": "KEEP_ORIGINAL", "confidence": 0.99,
            },
        }
        (result_dir / "correction_ledger.json").write_text(
            json.dumps({"entries": [entry]}), encoding="utf-8"
        )

        registry = WorkerRegistry(cfg.database_path)
        w = registry.add_colab(name="Anomaly GPU")
        registry.update_colab(w["id"], {"enabled": True, "url": "https://review.trycloudflare.com"})
        registry.write_api_key(w["id"], "a" * 32)
        registry.update_review(
            enabled=True, text_worker_ids=[w["id"]], vision_worker_ids=[],
            anomaly_worker_ids=[w["id"]],
        )
        ready = {
            "postprocess_job_id": 11, "result_dir": "book__job11",
            "text_pending": 0, "text_processing": 0, "text_failed": 0,
            "vision_pending": 0, "vision_processing": 0, "vision_failed": 0,
            "artifact_pending": 0, "artifact_processing": 0, "artifact_failed": 0,
        }
        store = ReviewAssistantStore(cfg.database_path)
        await store.initialize()
        service = ReviewAssistantService(
            lambda: cfg, registry, store, _Books([ready]),
            _UnusedPostprocess(), _UnusedWorker(), _Events(),
        )
        assert await service._sync_candidates() is True
        jobs = await store.list_jobs()
        assert {j["review_type"] for j in jobs} == {"text", "anomaly_text"}

    asyncio.run(run())

