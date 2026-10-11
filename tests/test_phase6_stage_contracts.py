from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app import main
from app.pipeline_state import attach_pipeline_blockers, stage_result_contract


REQUIRED_FIELDS = {
    "schema",
    "stage_id",
    "generation_id",
    "created_at",
    "source_signature",
    "upstream_generation",
    "status",
    "current",
    "ready_to_advance",
    "blockers",
    "data_hash",
    "data_hash_status",
    "human_review_required",
    "rule_version",
    "recorded_rule_version",
    "reason",
    "details",
}


def _row():
    return {
        "id": 7,
        "conversion_job_id": 3,
        "status": "completed",
        "result_dir": "Manual__job3__run0",
        "source_filename": "Manual.pdf",
        "output_sha256": "source-sha",
        "created_at": "2026-10-11T00:00:00+00:00",
        "completed_at": "2026-10-11T00:01:00+00:00",
    }


def _required(**overrides):
    value = {
        "ready": True,
        "total": 0,
        "raw_total": 0,
        "pending": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "discovery_current": True,
        "snapshot_missing": False,
    }
    value.update(overrides)
    return value


def _s2c(**overrides):
    value = {
        "ready": True,
        "reason": None,
        "status": "completed",
        "verification_signature": "verify-sig",
        "recorded_verification_signature": "verify-sig",
        "semantic_output_signature": "s2c-semantic",
        "output_signature": "s2c-output",
        "rule_version": "stage2c-current",
        "recorded_rule_version": "stage2c-current",
        "signature_route_count": 0,
        "publication_blocker_count": 0,
        "publication_blocker_job_ids": [],
        "state": {"completed_at_epoch": 10},
    }
    value.update(overrides)
    return value


def _s3(**overrides):
    value = {
        "ready": True,
        "reason": None,
        "status": "completed",
        "stage2c_signature": "s2c-output",
        "recorded_stage2c_signature": "s2c-output",
        "stage3_rule_version": "stage3-current",
        "recorded_stage3_rule_version": "stage3-current",
        "canonical_ready": True,
        "chunks_available": True,
        "retrieval_index_available": True,
        "retrieval_rule_version": "retrieval-current",
        "recorded_retrieval_rule_version": "retrieval-current",
        "retrieval_rule_match": True,
        "ranking_only_version_drift": False,
        "state": {
            "task_id": "docling-task-1",
            "completed_at_epoch": 20,
            "data_hash": "stage3-data-sha",
            "data_hash_basis": "canonical_chunks_and_retrieval_rows_sha256",
        },
    }
    value.update(overrides)
    return value


def _contracts(pipeline, *, rows=None, required=None, s2c=None, s3=None, structural=None, audit=None):
    row = _row()
    return main._build_document_stage_contracts(
        row,
        attach_pipeline_blockers(pipeline, book_status="completed", entity_id=7),
        result_dir=Path(row["result_dir"]),
        verification_rows=rows or [],
        required_verification=required or _required(),
        stage2c_info=s2c or _s2c(),
        stage3_info=s3 or _s3(),
        structural_review=structural or {},
        audit_gate=audit or {},
    )


def _by_stage(contracts):
    return {item["stage_id"]: item for item in contracts}


def test_stage_result_contract_has_strict_schema_even_when_legacy_values_are_missing():
    contract = stage_result_contract(
        stage_id="stage3",
        generation_id=None,
        created_at=None,
        source_signature=None,
        upstream_generation=None,
        status="not_built",
        current=False,
        ready_to_advance=False,
    )
    assert REQUIRED_FIELDS == set(contract)
    assert contract["schema"] == "pipeline-stage/v1"
    assert contract["data_hash"] is None
    assert contract["data_hash_status"] == "not_persisted"


def test_clean_zero_route_stage2b_contract_is_complete_and_ready():
    pipeline = {
        "stage2a_ready": True,
        "stage2b_ready": True,
        "stage2c_ready": False,
        "stage3_ready": False,
        "next_stage": "stage2c",
        "blocked_reason": "stage2c_not_built",
    }
    contracts = _by_stage(_contracts(
        pipeline,
        required=_required(),
        s2c=_s2c(
            ready=False,
            reason="stage2c_not_built",
            status="not_built",
            semantic_output_signature=None,
            output_signature=None,
            state={},
        ),
        s3=_s3(ready=False, status="not_built", state={}, reason="stage2c_not_current"),
    ))
    stage2b = contracts["stage2b"]
    assert stage2b["status"] == "completed"
    assert stage2b["current"] is True
    assert stage2b["ready_to_advance"] is True
    assert stage2b["details"]["required_total"] == 0
    assert stage2b["details"]["discovery_current"] is True


def test_stale_stage2c_contract_exposes_signature_mismatch_and_blocker():
    pipeline = {
        "stage2a_ready": True,
        "stage2b_ready": True,
        "stage2c_ready": False,
        "stage3_ready": False,
        "stage2c_reason": "stage2c_stale_after_verification",
        "next_stage": "stage2c",
        "blocked_reason": "stage2c_stale_after_verification",
    }
    contracts = _by_stage(_contracts(
        pipeline,
        s2c=_s2c(
            ready=False,
            reason="stage2c_stale_after_verification",
            verification_signature="new-verification",
            recorded_verification_signature="old-verification",
            semantic_output_signature="old-semantic",
            output_signature="old-output",
        ),
        s3=_s3(ready=False, reason="stage2c_not_current", status="completed"),
    ))
    stage2c = contracts["stage2c"]
    assert stage2c["current"] is False
    assert stage2c["ready_to_advance"] is False
    assert stage2c["source_signature"] == "new-verification"
    assert stage2c["details"]["recorded_verification_signature"] == "old-verification"
    assert stage2c["blockers"][0]["code"] == "stage2c_not_current"


def test_human_review_contract_blocks_advance_even_when_old_stage3_is_current():
    pipeline = {
        "stage2a_ready": True,
        "stage2b_ready": True,
        "stage2c_ready": True,
        "stage3_ready": True,
        "stage2a_human_review_pending": 0,
        "verifier_audit_pending": 2,
        "verifier_audit_blocking": 2,
        "blocking_reviews": 2,
        "next_stage": "verifier_audit",
        "blocked_reason": "2 verifier audit item(s) require a human decision before Stage 3.",
    }
    contracts = _by_stage(_contracts(
        pipeline,
        structural={"blocking_review_required": 0},
        audit={"review_required": 2, "blocking_review_required": 2, "bypassed_for_testing": False},
    ))
    review = contracts["review_gate"]
    stage3 = contracts["stage3"]
    assert review["status"] == "blocked"
    assert review["human_review_required"] is True
    assert review["ready_to_advance"] is False
    assert review["blockers"][0]["code"] == "verifier_audit_required"
    assert stage3["current"] is True
    assert stage3["ready_to_advance"] is False
    assert stage3["human_review_required"] is True
    assert stage3["blockers"][0]["code"] == "verifier_audit_required"


def test_current_stage3_contract_links_upstream_signature_and_persisted_data_hash():
    pipeline = {
        "stage2a_ready": True,
        "stage2b_ready": True,
        "stage2c_ready": True,
        "stage3_ready": True,
        "blocking_reviews": 0,
        "next_stage": "post_stage3",
        "blocked_reason": None,
    }
    contracts = _by_stage(_contracts(pipeline))
    stage3 = contracts["stage3"]
    assert stage3["generation_id"] == "docling-task-1"
    assert stage3["source_signature"] == "s2c-output"
    assert stage3["upstream_generation"] == "s2c-semantic"
    assert stage3["data_hash"] == "stage3-data-sha"
    assert stage3["data_hash_status"] == "persisted_output_hash"
    assert stage3["details"]["data_hash_basis"] == "canonical_chunks_and_retrieval_rows_sha256"
    assert stage3["ready_to_advance"] is True


def test_machine_and_rag_contracts_use_equipment_scope_and_respect_upstream_blockers():
    row = _row()
    row["pipeline"] = attach_pipeline_blockers(
        {
            "stage3_ready": True,
            "blocking_reviews": 0,
            "next_stage": "post_stage3",
        },
        book_status="completed",
        entity_id=7,
    )
    row["stage_contracts"] = []
    owner = {"equipment_id": "eq-pump", "name": "Bilge Pump"}
    main._apply_document_machine_state(
        row,
        owner,
        {
            "ready": True,
            "reason": None,
            "rows": 100,
            "model": "bge",
            "created_at_epoch": 30,
            "corpus_fingerprint": "machine-corpus",
            "manual_count": 2,
        },
    )
    contracts = _by_stage(row["stage_contracts"])
    assert row["pipeline"]["next_stage"] == "rag_ready"
    assert contracts["machine_embedding"]["current"] is True
    assert contracts["machine_embedding"]["ready_to_advance"] is True
    assert contracts["machine_embedding"]["data_hash_status"] == "semantic_corpus_fingerprint"
    assert contracts["rag"]["current"] is True
    assert contracts["rag"]["details"]["retrieval_scope"] == "equipment"

    blocked = _row()
    blocked["pipeline"] = attach_pipeline_blockers(
        {
            "stage3_ready": True,
            "blocking_reviews": 1,
            "verifier_audit_blocking": 1,
            "next_stage": "verifier_audit",
            "blocked_reason": "1 verifier audit item requires a human decision.",
        },
        book_status="completed",
        entity_id=7,
    )
    blocked["stage_contracts"] = []
    main._apply_document_machine_state(
        blocked,
        owner,
        {"ready": True, "reason": None, "rows": 100, "corpus_fingerprint": "old-machine"},
    )
    blocked_contracts = _by_stage(blocked["stage_contracts"])
    assert blocked["pipeline"]["next_stage"] == "verifier_audit"
    assert blocked_contracts["machine_embedding"]["current"] is True
    assert blocked_contracts["machine_embedding"]["ready_to_advance"] is False
    assert blocked_contracts["machine_embedding"]["human_review_required"] is True
    assert blocked_contracts["rag"]["current"] is False
    assert blocked_contracts["rag"]["blockers"][0]["code"] == "verifier_audit_required"


def test_compact_library_summary_omits_full_stage_contracts():
    compact = main._compact_document_summary({
        "id": 7,
        "status": "completed",
        "source_filename": "Manual.pdf",
        "verification": {},
        "pipeline": {"next_stage": "stage3"},
        "stage_contracts": [{"stage_id": "stage3"}],
    })
    assert "stage_contracts" not in compact


@pytest.mark.asyncio
async def test_contract_endpoint_is_read_only_projection(monkeypatch):
    detail = AsyncMock(return_value={
        "document": {
            "id": 7,
            "source_filename": "Manual.pdf",
            "pipeline": {"next_stage": "stage3"},
            "stage_contracts": [{"schema": "pipeline-stage/v1", "stage_id": "stage3"}],
        }
    })
    monkeypatch.setattr(main, "document_details", detail)
    result = await main.document_stage_contracts(7)
    detail.assert_awaited_once_with(7)
    assert result["schema"] == "pipeline-stage-set/v1"
    assert result["next_stage"] == "stage3"
    assert result["contracts"][0]["stage_id"] == "stage3"


def test_testing_bypass_preserves_human_review_requirement_without_blocking_advance():
    pipeline = {
        "stage2a_ready": True,
        "stage2b_ready": True,
        "stage2c_ready": True,
        "stage3_ready": True,
        "stage2a_human_review_pending": 0,
        "verifier_audit_pending": 2,
        "verifier_audit_blocking": 0,
        "blocking_reviews": 0,
        "audit_bypassed": True,
        "next_stage": "post_stage3",
        "blocked_reason": None,
    }
    contracts = _by_stage(_contracts(
        pipeline,
        audit={"review_required": 2, "blocking_review_required": 0, "bypassed_for_testing": True},
    ))
    review = contracts["review_gate"]
    stage3 = contracts["stage3"]
    assert review["status"] == "bypassed_for_testing"
    assert review["human_review_required"] is True
    assert review["ready_to_advance"] is True
    assert review["blockers"] == []
    assert stage3["human_review_required"] is True
    assert stage3["ready_to_advance"] is True
