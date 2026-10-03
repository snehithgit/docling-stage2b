import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import AppConfig
from app.events import EventBroker
from app.pipeline_state import stage2c_freshness, stage2c_output_signature, stage3_freshness, verification_rows_for_stage2c, verification_signature
from app.stage2b import Stage2BWorker
from app.stage2c import STAGE2C_RULE_VERSION
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
        "rule_version": STAGE2C_RULE_VERSION,
        "verification_signature": verification_signature(rows),
    }), encoding="utf-8")




def test_human_visual_recovery_rows_do_not_change_stage2c_signature_inputs():
    normal = _verification_row(row_id=1)
    recovery = {**_verification_row(row_id=2), "code": "HUMAN_VISUAL_EVIDENCE_RECOVERY", "route_id": "HUMAN_RECOVERY:g:vision:R1"}
    selected = verification_rows_for_stage2c([normal, recovery], artifact_sweep_required=True)
    assert selected == [normal]

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


def test_retrieval_rule_version_change_is_diagnostic_only(tmp_path: Path):
    result_dir = tmp_path / "book"
    rows = [_verification_row()]
    _write_current_stage2c(result_dir, rows)
    s2c = stage2c_freshness(result_dir, rows)
    (result_dir / "chunks.jsonl").write_text('{"chunk_id":"A"}\n', encoding="utf-8")
    (result_dir / "retrieval_index.jsonl").write_text('{"chunk_id":"A"}\n', encoding="utf-8")
    (result_dir / "retrieval_quality.json").write_text(json.dumps({"retrieval_rule_version":"old"}), encoding="utf-8")
    (result_dir / "stage3_chunking.json").write_text(json.dumps({"status":"completed", "stage2c_signature":s2c["output_signature"]}), encoding="utf-8")
    state = stage3_freshness(result_dir, s2c, retrieval_rule_version="new")
    assert state["ready"] is True
    assert state["canonical_ready"] is True
    assert state["reason"] is None
    assert state["ranking_only_version_drift"] is True


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


def test_optional_artifact_sweep_excludes_unfinished_rows_from_stage2c_signature(tmp_path):
    from app.pipeline_state import stage2c_freshness, verification_rows_for_stage2c
    rows = [
        {"id": 1, "generation": "g", "route_id": "T1", "target": "pi5", "status": "completed", "code": "TEXT_REVIEW", "result_json": "{}"},
        {"id": 2, "generation": "g", "route_id": "AV000001", "target": "oneplus", "status": "pending", "code": "FULL_TECHNICAL_VISUAL", "result_json": ""},
    ]
    effective = verification_rows_for_stage2c(rows, artifact_sweep_required=False)
    assert [row["route_id"] for row in effective] == ["T1"]
    (tmp_path / "correction_ledger.json").write_text('{"entries": []}')
    (tmp_path / "chunk_overlays.jsonl").write_text('')
    (tmp_path / "stage2c_backfill.json").write_text(json.dumps({
        "status": "completed",
        "verification_signature": verification_signature(effective),
    }))
    info = stage2c_freshness(tmp_path, rows, artifact_sweep_required=False)
    assert info["ready"] is True
    assert info["signature_route_count"] == 1
    assert info["artifact_sweep_required"] is False


def test_stage3_rule_version_change_marks_canonical_chunks_stale(tmp_path: Path):
    result_dir = tmp_path / "book"
    rows = [_verification_row()]
    _write_current_stage2c(result_dir, rows)
    s2c = stage2c_freshness(result_dir, rows)
    (result_dir / "chunks.jsonl").write_text('{"chunk_id":"A"}\n', encoding="utf-8")
    (result_dir / "retrieval_index.jsonl").write_text('{"chunk_id":"A"}\n', encoding="utf-8")
    (result_dir / "stage3_chunking.json").write_text(json.dumps({
        "status": "completed",
        "stage2c_signature": s2c["output_signature"],
        "rule_version": "old-stage3-rule",
    }), encoding="utf-8")
    state = stage3_freshness(result_dir, s2c, stage3_rule_version="new-stage3-rule")
    assert state["ready"] is False
    assert state["canonical_ready"] is False
    assert state["reason"] == "stage3_rule_version_stale"


def test_stage2a_human_review_summary_and_decision_are_durable(tmp_path: Path):
    from app.pipeline_state import stage2a_human_review_summary, set_stage2a_human_review_decision
    (tmp_path / "routes.json").write_text(json.dumps({"routes": [
        {"route_id":"R00001","target":"human","code":"DOCLING_GEOMETRY_ANOMALY","status":"pending","source":{"type":"diagnostic_group","count":2}},
        {"route_id":"R00002","target":"pi5","code":"TEXT_REVIEW","status":"pending","source":{"type":"text","index":1}},
    ]}), encoding="utf-8")
    (tmp_path / "diagnostics.json").write_text(json.dumps({"signals": [{
        "code": "DOCLING_GEOMETRY_ANOMALY", "classification": "HUMAN_REVIEW", "count": 2,
        "items": [
            {"page": 2, "source_type": "text", "text_index": 4, "problem": "degenerate_bbox"},
            {"page": 5, "source_type": "text", "text_index": 9, "problem": "bbox_outside_page"},
        ],
    }]}), encoding="utf-8")
    before = stage2a_human_review_summary(tmp_path)
    assert before["blocking_review_required"] == 1
    with pytest.raises(ValueError, match="Review every structural evidence item"):
        set_stage2a_human_review_decision(tmp_path, "R00001", decision="accepted", reviewed_items=["E0001"])
    decided = set_stage2a_human_review_decision(
        tmp_path, "R00001", decision="accepted", note="checked source layout",
        reviewed_items=["E0002", "E0001"],
    )
    assert decided["status"] == "accepted"
    assert decided["human_reviewed_evidence_ids"] == ["E0001", "E0002"]
    after = stage2a_human_review_summary(tmp_path)
    assert after["blocking_review_required"] == 0
    persisted = json.loads((tmp_path / "routes.json").read_text(encoding="utf-8"))
    row = next(r for r in persisted["routes"] if r["route_id"] == "R00001")
    assert row["human_decision"] == "accepted"
    assert row["human_decision_note"] == "checked source layout"


@pytest.mark.asyncio
async def test_stage3_rejects_pending_stage2a_structural_human_review(tmp_path: Path):
    processed = tmp_path / "processed"; output = tmp_path / "output"
    result_dir = processed / "book"; result_dir.mkdir(parents=True); output.mkdir()
    rows = [_verification_row()]
    _write_current_stage2c(result_dir, rows)
    (result_dir / "routes.json").write_text(json.dumps({"routes": [{
        "route_id":"R00002","target":"human","code":"DOCLING_GEOMETRY_ANOMALY","status":"pending",
        "source":{"type":"diagnostic_group","count":2},
    }]}), encoding="utf-8")

    class PostprocessStore:
        async def get_job(self, job_id):
            return {"id":1,"status":"completed","result_dir":"book","output_filename":"book.zip"}
    class VerificationStore:
        async def list_book_jobs_raw(self, job_id): return rows
    class Docling: pass

    cfg = AppConfig(output_dir=str(output), processed_dir=str(processed), database_path=str(tmp_path / "jobs.db"))
    builder = Stage3ChunkBuilder(lambda: cfg, PostprocessStore(), Docling(), EventBroker(), VerificationStore())
    with pytest.raises(ValueError, match="structural human review"):
        await builder.start(1)


def test_stage2c_publication_error_blocks_current_state_even_with_matching_backfill(tmp_path: Path):
    rows = [{
        "id": 91,
        "generation": "g1",
        "route_id": "R91",
        "target": "pi5",
        "status": "completed",
        "verdict": "UNCERTAIN",
        "completed_at": "2026-09-24T00:00:00Z",
        "model": "model",
        "result_json": '{"parsed":{"verdict":"UNCERTAIN"}}',
        "artifact_path": "verification/result.json",
        "error_type": "",
        "error_message": "",
        "stage2c_entry_state": "error",
        "stage2c_entry_id": "g1:text:R91",
        "stage2c_entry_error": "OSError: injected ledger write failure",
    }]
    (tmp_path / "correction_ledger.json").write_text('{"entries": []}', encoding="utf-8")
    (tmp_path / "chunk_overlays.jsonl").write_text('', encoding="utf-8")
    (tmp_path / "stage2c_backfill.json").write_text(json.dumps({
        "status": "completed",
        "verification_signature": verification_signature(rows),
    }), encoding="utf-8")

    info = stage2c_freshness(tmp_path, rows)
    assert info["ready"] is False
    assert info["reason"] == "stage2c_publication_incomplete"
    assert info["publication_blocker_count"] == 1
    assert info["publication_blocker_job_ids"] == [91]


def test_stage2c_legacy_completed_uncertain_text_row_without_ledger_entry_blocks_stage3(tmp_path: Path):
    rows = [{
        "id": 92,
        "generation": "legacy-g",
        "route_id": "R00001",
        "target": "pi5",
        "status": "completed",
        "verdict": "UNCERTAIN",
        "completed_at": "2026-09-24T00:00:00Z",
        "model": "model",
        "source_json": json.dumps({"type": "text", "index": 5378, "page": 72}),
        "result_json": '{"parsed":{"verdict":"UNCERTAIN"}}',
        "artifact_path": "verification/result.json",
        "error_type": "",
        "error_message": "",
        "stage2c_entry_state": None,
    }]
    (tmp_path / "correction_ledger.json").write_text('{"entries": []}', encoding="utf-8")
    (tmp_path / "chunk_overlays.jsonl").write_text('', encoding="utf-8")
    (tmp_path / "stage2c_backfill.json").write_text(json.dumps({
        "status": "completed",
        "verification_signature": verification_signature(rows),
    }), encoding="utf-8")

    info = stage2c_freshness(tmp_path, rows)
    assert info["ready"] is False
    assert info["reason"] == "stage2c_publication_incomplete"
    assert info["publication_blocker_job_ids"] == [92]


def test_reading_order_review_context_recovers_legacy_r_evidence_without_rerun(tmp_path: Path):
    from app.pipeline_state import stage2a_structural_review_context
    (tmp_path / "routes.json").write_text(json.dumps({"routes": [{
        "route_id": "R00001",
        "target": "human",
        "code": "READING_ORDER_ANOMALY",
        "status": "pending",
        "source": {"type": "diagnostic_group", "count": 2},
    }]}), encoding="utf-8")
    (tmp_path / "diagnostics.json").write_text(json.dumps({"signals": [{
        "code": "READING_ORDER_ANOMALY",
        "classification": "HUMAN_REVIEW",
        "count": 2,
        "items": [
            {"page": 7, "layout_model": "row_major", "score": 0.31, "body_order_sample": [{"type":"text","index":4,"text":"A"}]},
            {"page": 11, "layout_model": "column_major", "score": 0.27, "body_order_sample": [{"type":"text","index":9,"text":"B"}]},
        ],
    }]}), encoding="utf-8")

    context = stage2a_structural_review_context(tmp_path, "R00001")
    assert context["required_pages"] == [7, 11]
    assert [item["page"] for item in context["items"]] == [7, 11]
    assert context["route"]["source"] == {"type": "diagnostic_group", "count": 2}


def test_reading_order_review_cannot_be_resolved_blindly_and_records_reviewed_pages(tmp_path: Path):
    from app.pipeline_state import set_stage2a_human_review_decision
    (tmp_path / "routes.json").write_text(json.dumps({"routes": [{
        "route_id": "R00001",
        "target": "human",
        "code": "READING_ORDER_ANOMALY",
        "status": "pending",
        "source": {"type": "diagnostic_group", "count": 2},
    }]}), encoding="utf-8")
    (tmp_path / "diagnostics.json").write_text(json.dumps({"signals": [{
        "code": "READING_ORDER_ANOMALY",
        "classification": "HUMAN_REVIEW",
        "count": 2,
        "items": [{"page": 7}, {"page": 11}],
    }]}), encoding="utf-8")

    with pytest.raises(ValueError, match="Review every flagged reading-order page"):
        set_stage2a_human_review_decision(tmp_path, "R00001", decision="accepted", reviewed_pages=[7])
    persisted = json.loads((tmp_path / "routes.json").read_text(encoding="utf-8"))
    assert persisted["routes"][0]["status"] == "pending"

    decided = set_stage2a_human_review_decision(
        tmp_path, "R00001", decision="accepted", note="source pages checked", reviewed_pages=[11, 7]
    )
    assert decided["status"] == "accepted"
    assert decided["human_reviewed_pages"] == [7, 11]


def test_table_row_collapse_cannot_be_dismissed_without_source_review_marker(tmp_path: Path):
    from app.pipeline_state import set_stage2a_human_review_decision
    (tmp_path / "routes.json").write_text(json.dumps({"routes": [{
        "route_id": "R00007", "target": "human", "code": "TABLE_ROW_COLLAPSE",
        "status": "pending", "source": {"type": "table_structure", "table_index": 3, "page": 12},
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="original source page"):
        set_stage2a_human_review_decision(tmp_path, "R00007", decision="dismissed")
    decided = set_stage2a_human_review_decision(
        tmp_path, "R00007", decision="dismissed", reviewed_items=["table-source"]
    )
    assert decided["status"] == "dismissed"
    assert decided["human_reviewed_evidence_ids"] == ["table-source"]
