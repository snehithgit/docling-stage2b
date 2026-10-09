import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import main


@pytest.mark.parametrize(
    "model,extra",
    [
        (main.RetrievalSearchRequest, {"query": "hydraulic pressure"}),
        (main.RetrievalGenerateRequest, {"query": "hydraulic pressure", "provider": "pi5"}),
        (main.RetrievalPromptExportRequest, {"query": "hydraulic pressure"}),
    ],
)
def test_retrieval_requests_reject_missing_scope(model, extra):
    with pytest.raises(ValidationError, match="Choose exactly one retrieval scope"):
        model(**extra)


@pytest.mark.parametrize(
    "model,extra",
    [
        (main.RetrievalSearchRequest, {"query": "hydraulic pressure"}),
        (main.RetrievalGenerateRequest, {"query": "hydraulic pressure", "provider": "pi5"}),
        (main.RetrievalPromptExportRequest, {"query": "hydraulic pressure"}),
    ],
)
def test_retrieval_requests_reject_both_scopes(model, extra):
    with pytest.raises(ValidationError, match="Choose exactly one retrieval scope"):
        model(postprocess_job_id=7, equipment_id="eq-crane", **extra)


@pytest.mark.parametrize(
    "model,extra",
    [
        (main.RetrievalSearchRequest, {"query": "hydraulic pressure"}),
        (main.RetrievalGenerateRequest, {"query": "hydraulic pressure", "provider": "pi5"}),
        (main.RetrievalPromptExportRequest, {"query": "hydraulic pressure"}),
    ],
)
def test_retrieval_requests_accept_book_or_equipment_scope(model, extra):
    assert model(postprocess_job_id=7, **extra).postprocess_job_id == 7
    assert model(equipment_id="eq-crane", **extra).equipment_id == "eq-crane"


def test_internal_retrieval_has_no_all_books_fallback(monkeypatch):
    async def fake_books():
        return []

    monkeypatch.setattr(main, "_retrieval_books", fake_books)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main._retrieval_results_for_question("pressure", None, None, 5, "lexical"))
    assert exc.value.status_code == 422
    assert "exactly one retrieval scope" in exc.value.detail


def test_existing_stage3_index_is_not_search_ready_while_human_review_blocks(tmp_path, monkeypatch):
    directory = tmp_path / "book"
    directory.mkdir()
    (directory / "retrieval_index.jsonl").write_text('{"chunk_id":"A"}\n', encoding="utf-8")
    (directory / "visual_evidence_index.jsonl").write_text('{"chunk_id":"V"}\n', encoding="utf-8")

    monkeypatch.setattr(main, "identity_metadata_status", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(main, "stage2c_freshness", lambda *args, **kwargs: {"ready": True, "reason": None})
    monkeypatch.setattr(main, "stage3_freshness", lambda *args, **kwargs: {"ready": True, "reason": None, "status": "completed"})
    monkeypatch.setattr(main, "stage2a_human_review_summary", lambda *args, **kwargs: {"blocking_review_required": 1})
    monkeypatch.setattr(main, "verifier_audit_summary", lambda *args, **kwargs: {"blocking_review_required": 0, "bypassed_for_testing": False})
    monkeypatch.setattr(main, "ensure_visual_evidence_fresh", lambda *args, **kwargs: {})
    monkeypatch.setattr(main, "evidence_coverage", lambda *args, **kwargs: {"status": "not_scanned"})

    original_runtime = main.runtime
    main.runtime = SimpleNamespace(
        config=SimpleNamespace(
            processed_dir=str(tmp_path),
            stage2b_artifact_sweep_required_for_finalize=True,
            stage2c_require_human_review=True,
        ),
        postprocess_store=SimpleNamespace(list_jobs=AsyncMock(return_value=[{
            "id": 7, "status": "completed", "result_dir": "book",
            "source_filename": "Manual.pdf", "conversion_job_id": 3,
        }])),
        stage2b_store=SimpleNamespace(list_book_jobs_raw=AsyncMock(return_value=[])),
    )
    try:
        books = asyncio.run(main._retrieval_books())
    finally:
        main.runtime = original_runtime

    assert len(books) == 1
    assert books[0]["index_state"] == "review_pending"
    assert books[0]["pending_human_review"] == 1
    assert books[0]["index_ready"] is False
    assert books[0]["visual_index_ready"] is False


def test_hybrid_index_request_is_machine_only():
    with pytest.raises(ValidationError, match="machine/equipment scope"):
        main.RetrievalHybridIndexRequest()
    with pytest.raises(ValidationError, match="machine-scoped"):
        main.RetrievalHybridIndexRequest(postprocess_job_id=7)
    with pytest.raises(ValidationError, match="machine-scoped"):
        main.RetrievalHybridIndexRequest(postprocess_job_id=7, equipment_id="eq-crane")
    assert main.RetrievalHybridIndexRequest(equipment_id="eq-crane").equipment_id == "eq-crane"
