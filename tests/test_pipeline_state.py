import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import AppConfig
from app.events import EventBroker
from app.pipeline_state import stage2c_freshness, stage2c_output_signature, stage3_freshness, verification_signature
from app.stage2b import Stage2BWorker
from app.stage3 import Stage3ChunkBuilder


def _verification_row(*, status="completed", result_json='{"ok":true}', row_id=1):
    return {
        "id": row_id,
        "generation": "g1",
        "route_id": f"R{row_id}",
        "target": "text:0",
        "status": status,
        "verdict": "OK" if status == "completed" else "",
        "completed_at": "2026-09-18T00:00:00Z" if status == "completed" else "",
        "model": "model",
        "result_json": result_json,
        "artifact_path": "verification/result.json",
        "error_type": "TestError" if status == "failed" else "",
        "error_message": "failed" if status == "failed" else "",
    }


def _write_current_stage2c(result_dir: Path, rows):
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "correction_ledger.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    (result_dir / "chunk_overlays.jsonl").write_text("", encoding="utf-8")
    (result_dir / "stage2c_backfill.json").write_text(json.dumps({
        "status": "completed",
        "verification_signature": verification_signature(rows),
    }), encoding="utf-8")


def test_stage2c_freshness_turns_stale_when_verification_changes(tmp_path: Path):
    result_dir = tmp_path / "book"
    rows = [_verification_row()]
    _write_current_stage2c(result_dir, rows)
    current = stage2c_freshness(result_dir, rows)
    assert current["ready"] is True

    changed = [_verification_row(result_json='{"ok":false,"changed":true}')]
    stale = stage2c_freshness(result_dir, changed)
    assert stale["ready"] is False
    assert stale["reason"] == "stage2c_stale_after_verification"


def test_stage3_freshness_turns_stale_when_stage2c_output_changes(tmp_path: Path):
    result_dir = tmp_path / "book"
    rows = [_verification_row()]
    _write_current_stage2c(result_dir, rows)
    s2c = stage2c_freshness(result_dir, rows)
    (result_dir / "chunks.jsonl").write_text('{"chunk_id":"A"}\n', encoding="utf-8")
    (result_dir / "retrieval_index.jsonl").write_text('{"chunk_id":"A"}\n', encoding="utf-8")
    (result_dir / "stage3_chunking.json").write_text(json.dumps({
        "status": "completed",
        "stage2c_signature": s2c["output_signature"],
    }), encoding="utf-8")
    assert stage3_freshness(result_dir, s2c)["ready"] is True

    (result_dir / "chunk_overlays.jsonl").write_text('{"entry_id":"changed"}\n', encoding="utf-8")
    changed_s2c = stage2c_freshness(result_dir, rows)
    assert changed_s2c["ready"] is True
    stale = stage3_freshness(result_dir, changed_s2c)
    assert stale["ready"] is False
    assert stale["reason"] == "stage3_stale_after_stage2c"


@pytest.mark.asyncio
async def test_stage2c_rejects_failed_verification_routes(tmp_path: Path):
    class VerificationStore:
        async def list_book_jobs_raw(self, job_id):
            return [_verification_row(status="failed")]

    class PostprocessStore:
        async def get_job(self, job_id):
            return {"id": 1, "status": "completed", "result_dir": "book"}

    cfg = SimpleNamespace(stage2c_enabled=True, processed_dir=str(tmp_path))
    worker = Stage2BWorker(lambda: cfg, VerificationStore(), PostprocessStore(), EventBroker())
    with pytest.raises(ValueError, match="1 failed"):
        await worker.start_stage2c_backfill(1)


@pytest.mark.asyncio
async def test_stage3_rejects_stage2c_if_verification_signature_is_stale(tmp_path: Path):
    processed = tmp_path / "processed"
    result_dir = processed / "book"
    output = tmp_path / "output"
    result_dir.mkdir(parents=True)
    output.mkdir()
    old_rows = [_verification_row(result_json='{"version":1}')]
    new_rows = [_verification_row(result_json='{"version":2}')]
    _write_current_stage2c(result_dir, old_rows)

    class PostprocessStore:
        async def get_job(self, job_id):
            return {"id": 1, "status": "completed", "result_dir": "book", "output_filename": "book.zip"}

    class VerificationStore:
        async def list_book_jobs_raw(self, job_id):
            return new_rows

    class Docling:
        pass

    cfg = AppConfig(output_dir=str(output), processed_dir=str(processed), database_path=str(tmp_path / "jobs.db"))
    builder = Stage3ChunkBuilder(lambda: cfg, PostprocessStore(), Docling(), EventBroker(), VerificationStore())
    with pytest.raises(ValueError, match="Stage 2C is stale"):
        await builder.start(1)


def test_stage2c_rule_version_change_marks_old_output_stale(tmp_path: Path):
    result_dir = tmp_path / "book"
    rows = [_verification_row()]
    _write_current_stage2c(result_dir, rows)
    old = json.loads((result_dir / "stage2c_backfill.json").read_text())
    old["rule_version"] = "old-rule"
    (result_dir / "stage2c_backfill.json").write_text(json.dumps(old), encoding="utf-8")
    state = stage2c_freshness(result_dir, rows, rule_version="new-rule")
    assert state["ready"] is False
    assert state["reason"] == "stage2c_rule_version_stale"


def test_retrieval_rule_version_change_marks_only_retrieval_stale(tmp_path: Path):
    result_dir = tmp_path / "book"
    rows = [_verification_row()]
    _write_current_stage2c(result_dir, rows)
    s2c = stage2c_freshness(result_dir, rows)
    (result_dir / "chunks.jsonl").write_text('{"chunk_id":"A"}\n', encoding="utf-8")
    (result_dir / "retrieval_index.jsonl").write_text('{"chunk_id":"A"}\n', encoding="utf-8")
    (result_dir / "retrieval_quality.json").write_text(json.dumps({"retrieval_rule_version":"old"}), encoding="utf-8")
    (result_dir / "stage3_chunking.json").write_text(json.dumps({"status":"completed", "stage2c_signature":s2c["output_signature"]}), encoding="utf-8")
    state = stage3_freshness(result_dir, s2c, retrieval_rule_version="new")
    assert state["ready"] is False
    assert state["canonical_ready"] is True
    assert state["reason"] == "retrieval_rules_stale"


def test_identity_metadata_repair_uses_result_directory_job_id(tmp_path: Path):
    from app.pipeline_state import repair_identity_metadata
    result_dir = tmp_path / "Manual__job5__run2"
    result_dir.mkdir()
    for name in ("stage2c_backfill.json", "stage3_chunking.json", "retrieval_quality.json"):
        (result_dir / name).write_text(json.dumps({"postprocess_job_id":3, "status":"completed"}), encoding="utf-8")
    result = repair_identity_metadata(result_dir, expected_job_id=5)
    assert result["ok"] is True
    assert set(result["repaired"]) == {"stage2c_backfill.json", "stage3_chunking.json", "retrieval_quality.json"}
    assert json.loads((result_dir / "stage3_chunking.json").read_text())["postprocess_job_id"] == 5


def test_identity_metadata_repair_refuses_runtime_directory_disagreement(tmp_path: Path):
    from app.pipeline_state import repair_identity_metadata
    result_dir = tmp_path / "Manual__job5__run2"
    result_dir.mkdir()
    (result_dir / "stage3_chunking.json").write_text(json.dumps({"postprocess_job_id":3}), encoding="utf-8")
    result = repair_identity_metadata(result_dir, expected_job_id=6)
    assert result["repairable"] is False
    assert json.loads((result_dir / "stage3_chunking.json").read_text())["postprocess_job_id"] == 3
