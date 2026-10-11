from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import main
from app.database import JobStore
from app.pipeline_state import attach_pipeline_blockers, pipeline_blockers
from app.postprocess_store import PostprocessStore


def test_pipeline_blocker_contract_uses_only_canonical_current_stage():
    pipeline = {
        "next_stage": "verifier_audit",
        "blocked_reason": "2 verifier audit item(s) require a human decision before Stage 3.",
        "stage2a_human_review_pending": 0,
        "verifier_audit_pending": 3,
        "verifier_audit_blocking": 2,
        "stage2c_ready": True,
        "stage3_ready": False,
        "machine_embedding_ready": False,
    }
    blockers = pipeline_blockers(pipeline, book_status="completed")
    assert len(blockers) == 1
    blocker = blockers[0]
    assert blocker["code"] == "verifier_audit_required"
    assert blocker["stage"] == "verifier_audit"
    assert blocker["severity"] == "attention"
    assert blocker["operator_action_required"] is True
    assert blocker["auto_resolvable"] is False
    assert blocker["count"] == 2


@pytest.mark.parametrize(
    ("next_stage", "expected_code", "operator", "automatic"),
    [
        ("stage2a_human_review", "stage2a_structural_review_required", True, False),
        ("assign_machine", "manual_not_assigned_to_machine", True, False),
        ("machine_embedding", "machine_embedding_not_ready", False, True),
    ],
)
def test_pipeline_blocker_contract_classifies_operator_vs_automatic(
    next_stage, expected_code, operator, automatic
):
    pipeline = {
        "next_stage": next_stage,
        "stage2a_human_review_pending": 1,
        "machine_id": "eq-1",
        "machine_name": "Pump",
    }
    blocker = pipeline_blockers(pipeline, book_status="completed")[0]
    assert blocker["code"] == expected_code
    assert blocker["operator_action_required"] is operator
    assert blocker["auto_resolvable"] is automatic


def test_rag_ready_has_no_pipeline_blocker():
    projected = attach_pipeline_blockers(
        {"next_stage": "rag_ready", "machine_embedding_ready": True},
        book_status="completed",
        entity_id=17,
    )
    assert projected["blockers"] == []
    assert projected["primary_blocker"] is None


@pytest.mark.asyncio
async def test_pipeline_transition_ledger_is_append_only_and_deduplicated(tmp_path):
    database_path = str(tmp_path / "jobs.db")
    jobs = JobStore(database_path)
    await jobs.initialize()
    store = PostprocessStore(database_path)
    await store.initialize()

    assert await store.record_pipeline_projection(
        7,
        "stage2b",
        blocker_code="stage2b_verification_pending",
        blocker_message="Verification pending",
        context={"stage": "stage2b"},
    )
    assert not await store.record_pipeline_projection(
        7,
        "stage2b",
        blocker_code="stage2b_verification_pending",
        blocker_message="Verification pending",
        context={"stage": "stage2b", "ignored_for_dedupe": True},
    )
    assert await store.record_pipeline_projection(
        7,
        "stage2c",
        blocker_code="stage2c_not_current",
        blocker_message="Stage 2C is not current",
        context={"stage": "stage2c"},
    )

    rows = await store.list_pipeline_transitions(postprocess_job_id=7, limit=10)
    assert len(rows) == 2
    newest, oldest = rows
    assert newest["previous_stage"] == "stage2b"
    assert newest["new_stage"] == "stage2c"
    assert newest["blocker_code"] == "stage2c_not_current"
    assert newest["context"]["stage"] == "stage2c"
    assert oldest["previous_stage"] is None
    assert oldest["new_stage"] == "stage2b"


@pytest.mark.asyncio
async def test_pipeline_health_aggregates_normalized_blockers_and_transitions(monkeypatch):
    documents = [
        {
            "id": 1,
            "source_filename": "Needs Review.pdf",
            "pipeline": attach_pipeline_blockers(
                {
                    "next_stage": "verifier_audit",
                    "verifier_audit_blocking": 2,
                    "verifier_audit_pending": 2,
                    "blocked_reason": "2 verifier audit item(s) require a human decision before Stage 3.",
                },
                book_status="completed",
                entity_id=1,
            ),
        },
        {
            "id": 2,
            "source_filename": "Embedding.pdf",
            "pipeline": attach_pipeline_blockers(
                {
                    "next_stage": "machine_embedding",
                    "machine_id": "eq-2",
                    "machine_name": "Pump",
                    "machine_embedding_reason": "hybrid_stale",
                },
                book_status="completed",
                entity_id=2,
            ),
        },
        {
            "id": 3,
            "source_filename": "Ready.pdf",
            "pipeline": attach_pipeline_blockers(
                {"next_stage": "rag_ready", "machine_embedding_ready": True},
                book_status="completed",
                entity_id=3,
            ),
        },
    ]
    transition_reader = AsyncMock(return_value=[{
        "id": 9,
        "postprocess_job_id": 1,
        "observed_at": "2026-10-11T00:00:00+00:00",
        "previous_stage": "stage3",
        "new_stage": "verifier_audit",
        "blocker_code": "verifier_audit_required",
        "blocker_message": "Review required",
        "context": {},
    }])
    original_runtime = main.runtime
    main.runtime = SimpleNamespace(
        postprocess_store=SimpleNamespace(list_pipeline_transitions=transition_reader)
    )
    monkeypatch.setattr(
        main,
        "document_summaries",
        AsyncMock(return_value={"documents": documents}),
    )
    try:
        result = await main.pipeline_health()
    finally:
        main.runtime = original_runtime

    assert result["schema"] == "pipeline-health/v1"
    assert result["books_total"] == 3
    assert result["books_ready"] == 1
    assert result["books_needing_operator"] == 1
    assert result["books_auto_progressing"] == 1
    assert result["blocker_counts"]["verifier_audit_required"] == 1
    assert result["blocker_counts"]["machine_embedding_not_ready"] == 1
    assert result["blockers"][0]["action_href"] == "/vision-audit?book=1"
    assert result["recent_transitions"][0]["source_filename"] == "Needs Review.pdf"
    transition_reader.assert_awaited_once_with(limit=100)
