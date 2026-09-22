import asyncio
import json
from types import SimpleNamespace

import pytest

from app.main import Runtime


class _Events:
    def __init__(self):
        self.reasons = []

    def notify(self, reason):
        self.reasons.append(reason)


class _Stage2BStore:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def list_results_raw(self, target, limit=5000):
        return [row for row in self.rows if row.get("target") == target]

    async def get_job(self, job_id):
        return next((row for row in self.rows if int(row.get("id") or 0) == int(job_id)), None)


class _Worker:
    def __init__(self):
        self._stage2c_ledger_lock = asyncio.Lock()
        self.recovery = []

    async def text_audit_image(self, job_id):
        return b"text-image", "image/png", "target"

    async def vision_audit_image(self, job_id, region="full"):
        return b"vision-image", "image/png", region

    async def start_human_visual_evidence_recovery(self, job_id, entry_id):
        self.recovery.append((job_id, entry_id))
        return {"status": "queued"}


def _runtime(tmp_path, rows=None):
    runtime = object.__new__(Runtime)
    runtime.config = SimpleNamespace(processed_dir=str(tmp_path))
    runtime.stage2b_store = _Stage2BStore(rows)
    runtime.stage2b_worker = _Worker()
    runtime.events = _Events()
    return runtime


def _write_ledger(tmp_path, result_dir, entries):
    root = tmp_path / result_dir
    root.mkdir(parents=True, exist_ok=True)
    (root / "correction_ledger.json").write_text(json.dumps({
        "schema": "docling-correction-ledger/v2",
        "source_zip_sha256": "abc",
        "entries": entries,
    }), encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_telegram_text_decision_uses_same_human_authority_path(tmp_path):
    entry_id = "1:text:R00001"
    _write_ledger(tmp_path, "book", [{
        "entry_id": entry_id,
        "entry_type": "text_correction",
        "route_id": "R00001",
        "original_text": "PSU M AC/DC SA",
        "proposed_text": "PSU M AC/DC 5A",
        "status": "pending",
        "verification_verdict": "LIKELY_CORRUPT",
    }])
    runtime = _runtime(tmp_path)
    result = await runtime._telegram_apply_text_audit(
        {"row_id": 1, "result_dir": "book", "entry_id": entry_id}, "apply"
    )
    assert result["ok"] is True
    ledger = json.loads((tmp_path / "book" / "correction_ledger.json").read_text(encoding="utf-8"))
    saved = next(item for item in ledger["entries"] if item["entry_id"] == entry_id)
    assert saved["human_verified"] is True
    assert saved["status"] == "applied"
    assert saved["proposed_text"] == "PSU M AC/DC 5A"
    assert "stage2c_human_correction" in runtime.events.reasons


@pytest.mark.asyncio
async def test_telegram_visual_acceptance_queues_evidence_recovery_when_needed(tmp_path):
    entry_id = "1:vision:R00006"
    _write_ledger(tmp_path, "book", [{
        "entry_id": entry_id,
        "entry_type": "vision_enrichment",
        "route_id": "R00006",
        "source_index": 4,
        "status": "pending",
        "verification_verdict": "UNCERTAIN",
        "verification_parse_failed": True,
        "verification_job_id": 55,
        "visible_text": [],
        "visible_objects": [],
        "generated_summary": "",
    }])
    runtime = _runtime(tmp_path)
    result = await runtime._telegram_apply_visual_audit(
        {"row_id": 55, "result_dir": "book", "entry_id": entry_id}, "useful"
    )
    assert result["ok"] is True
    assert runtime.stage2b_worker.recovery == [(55, entry_id)]
    ledger = json.loads((tmp_path / "book" / "correction_ledger.json").read_text(encoding="utf-8"))
    saved = next(item for item in ledger["entries"] if item["entry_id"] == entry_id)
    assert saved["human_visual_decision"] == "useful"
    assert saved["human_verified"] is True
    assert saved["status"] == "applied"
    assert "verifier_audit_decision" in runtime.events.reasons


@pytest.mark.asyncio
async def test_telegram_visual_and_artifact_queues_are_separate(tmp_path):
    normal_entry = "1:vision:R00001"
    sweep_entry = "1:vision:AV000001"
    _write_ledger(tmp_path, "book", [
        {
            "entry_id": normal_entry,
            "entry_type": "vision_enrichment",
            "route_id": "R00001",
            "status": "pending",
            "verification_verdict": "UNCERTAIN",
            "unresolved": True,
        },
        {
            "entry_id": sweep_entry,
            "entry_type": "vision_enrichment",
            "route_id": "AV000001",
            "status": "pending",
            "verification_verdict": "UNCERTAIN",
            "unresolved": True,
            "artifact_sweep": True,
        },
    ])
    rows = [
        {
            "id": 10, "target": "oneplus", "status": "completed", "postprocess_job_id": 1,
            "generation": 1, "route_id": "R00001", "code": "LOW_CONFIDENCE_VISUAL",
            "result_dir": "book", "output_filename": "book.zip", "verdict": "UNCERTAIN",
            "source_json": json.dumps({"type": "picture", "index": 0}),
            "request_json": json.dumps({"page": 2}),
            "result_json": json.dumps({"parsed": {"verdict": "UNCERTAIN", "unresolved": True}}),
        },
        {
            "id": 11, "target": "pi5", "status": "completed", "postprocess_job_id": 1,
            "generation": 1, "route_id": "AV000001", "code": "FULL_TECHNICAL_VISUAL",
            "result_dir": "book", "output_filename": "book.zip", "verdict": "UNCERTAIN",
            "source_json": json.dumps({"type": "picture", "index": 1}),
            "request_json": json.dumps({"page": 3}),
            "result_json": json.dumps({"parsed": {"verdict": "UNCERTAIN", "unresolved": True}}),
        },
    ]
    runtime = _runtime(tmp_path, rows)
    vision = await runtime._telegram_next_visual_audit("vision")
    artifact = await runtime._telegram_next_visual_audit("artifact")
    assert vision["key"]["entry_id"] == normal_entry
    assert [x["value"] for x in vision["options"]] == ["useful", "not_useful"]
    assert artifact["key"]["entry_id"] == sweep_entry
    assert [x["value"] for x in artifact["options"]] == ["technical", "decorative"]


@pytest.mark.asyncio
async def test_telegram_visual_queue_uses_current_ledger_even_when_evidence_row_is_from_older_result_dir(tmp_path):
    entry_id = "newgen:vision:R00009"
    _write_ledger(tmp_path, "current-book", [{
        "entry_id": entry_id,
        "entry_type": "vision_enrichment",
        "route_id": "R00009",
        "source_index": 9,
        "status": "pending",
        "verification_verdict": "UNCERTAIN",
        "unresolved": True,
        "verification_job_id": 42,
        "page": 8,
    }])
    rows = [{
        "id": 42, "target": "oneplus", "status": "completed", "postprocess_job_id": 5,
        "generation": "oldgen", "route_id": "R00009", "code": "LOW_CONFIDENCE_VISUAL",
        "result_dir": "historical-book", "output_filename": "manual.zip", "verdict": "UNCERTAIN",
        "source_json": json.dumps({"type": "picture", "index": 9}),
        "request_json": json.dumps({"page": 8}),
        "result_json": json.dumps({"parsed": {"verdict": "UNCERTAIN", "unresolved": True}}),
    }]
    runtime = _runtime(tmp_path, rows)

    class _Postprocess:
        async def list_jobs(self, limit=5000):
            return [{
                "id": 5, "status": "completed", "result_dir": "current-book",
                "source_filename": "manual.pdf", "output_filename": "manual.zip",
            }]

    runtime.postprocess_store = _Postprocess()
    review = await runtime._telegram_next_visual_audit("vision")
    assert review["done"] is False
    assert review["key"]["entry_id"] == entry_id
    assert review["key"]["result_dir"] == "current-book"
    assert review["key"]["row_id"] == 42
    assert review["remaining"] == 1
