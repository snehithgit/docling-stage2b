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
    )
    assert review["text_worker_ids"] == ["colab-1", "colab-2"]
    assert review["vision_worker_ids"] == ["colab-1"]
    assert review["require_machine_complete"] is True

    registry.update_colab(first["id"], {"paused": True})
    assert [w["id"] for w in registry.configured_colabs(cfg)] == ["colab-2"]
    assert [w["id"] for w in registry.configured_colabs(cfg, include_paused=True)] == ["colab-1", "colab-2"]

    assert registry.remove_colab(first["id"]) is True
    snap = registry.snapshot(cfg)
    assert [w["id"] for w in snap["colab_workers"]] == ["colab-2"]
    assert snap["review"]["text_worker_ids"] == ["colab-2"]
    assert snap["review"]["vision_worker_ids"] == []


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
        registry.update_review(enabled=True, text_worker_ids=[w["id"]], vision_worker_ids=[w["id"]])

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
