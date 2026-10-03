import asyncio
import json
from types import SimpleNamespace

import pytest

from app.pipeline_state import stage2c_output_signature
from app.review_workers import ReviewAssistantStore
from app.stage2c import _human_decision_epoch
from app import book_lifecycle


def test_advisory_reviews_do_not_invalidate_accepted_chunks(tmp_path):
    path = tmp_path / "correction_ledger.json"
    ledger = {"updated_at_epoch": 1, "entries": [{"entry_id": "text:R1", "proposed_text": "accepted", "human_verified": True}]}
    path.write_text(json.dumps(ledger))
    initial = stage2c_output_signature(tmp_path)
    ledger["updated_at_epoch"] = 2
    ledger["entries"][0].update(ai_review_assistant={"verdict": "review"}, anomaly_review={"verdict": "inspect"}, anomaly_review_history=[{}], anomaly_review_decision={"dismissed": True})
    path.write_text(json.dumps(ledger))
    assert stage2c_output_signature(tmp_path) == initial
    ledger["entries"][0]["proposed_text"] = "human changed text"
    path.write_text(json.dumps(ledger))
    assert stage2c_output_signature(tmp_path) != initial


def test_text_human_timestamp_is_not_hidden_by_missing_visual_timestamp():
    assert _human_decision_epoch({"created_at_epoch": 10, "human_review": {"saved_at_epoch": 30}}) == 30


def test_returning_evidence_reactivates_existing_queue_row_and_failed_retry(tmp_path):
    async def run():
        store = ReviewAssistantStore(str(tmp_path / "jobs.db"))
        await store.initialize()
        entry = {"entry_id": "text:R1", "entry_type": "text_correction", "proposed_text": "A"}
        await store.sync_candidate(21, "book", entry, "text")
        original = await store.claim_next("colab-1", {"text"})
        for _ in range(3):
            await store.mark_retryable(original["id"], ValueError("bad JSON"), delay=0)
            if _ < 2:
                original = await store.claim_next("colab-1", {"text"})
        await store.sync_candidate(21, "book", {**entry, "proposed_text": "B"}, "text")
        await store.sync_candidate(21, "book", entry, "text")
        jobs = await store.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["id"] == original["id"]
        assert jobs[0]["status"] == "failed"
        assert await store.retry_failed(original["id"])
        fresh = await store.claim_next("colab-1", {"text"})
        assert fresh["attempt_count"] == 1
        assert not await store.retry_failed(fresh["id"])
    asyncio.run(run())


def test_parallel_manual_queue_requests_deduplicate_across_store_instances(tmp_path):
    async def run():
        stores = [ReviewAssistantStore(str(tmp_path / "jobs.db")) for _ in range(2)]
        await stores[0].initialize()
        entry = {"entry_id": "text:R1", "entry_type": "text_correction"}
        rows = await asyncio.gather(*(s.queue_manual_anomaly(21, "book", entry, "anomaly_text") for s in stores))
        assert rows[0]["id"] == rows[1]["id"]
    asyncio.run(run())


@pytest.mark.parametrize("queue_only", [False, True])
def test_partial_quarantine_failure_restores_already_moved_files(tmp_path, monkeypatch, queue_only):
    for directory in ("input", "output", "processed"):
        (tmp_path / directory).mkdir()
    config = SimpleNamespace(**{f"{name}_dir": str(tmp_path / name) for name in ("input", "output", "processed")})
    source = tmp_path / "input" / "manual.pdf"
    source.write_bytes(b"source")
    (tmp_path / "output" / "manual.zip").write_bytes(b"converted")
    original_move = book_lifecycle._move
    calls = 0
    def failing_move(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk failure")
        return original_move(*args, **kwargs)
    monkeypatch.setattr(book_lifecycle, "_move", failing_move)
    row = {"id": 21, "filename": "manual.pdf", "conversion_filename": "manual.pdf", "output_filename": "manual.zip"}
    fn = book_lifecycle.quarantine_conversion_job if queue_only else book_lifecycle.quarantine_book_artifacts
    with pytest.raises(OSError, match="disk failure"):
        fn(config, row)
    assert source.read_bytes() == b"source"


def test_busy_provider_does_not_exhaust_review_attempts(tmp_path):
    async def run():
        store = ReviewAssistantStore(str(tmp_path / "jobs.db"))
        await store.initialize()
        await store.sync_candidate(21, "book", {"entry_id": "text:R1"}, "text")
        for _ in range(5):
            row = await store.claim_next("colab-1", {"text"})
            assert row["attempt_count"] == 1
            await store.defer_busy(row["id"], delay=0)
        assert (await store.counts())["failed"] == 0
        assert (await store.counts())["text_pending"] == 1
    asyncio.run(run())
