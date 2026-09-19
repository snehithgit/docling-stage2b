import asyncio

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


def test_hybrid_index_request_is_machine_only():
    with pytest.raises(ValidationError, match="machine/equipment scope"):
        main.RetrievalHybridIndexRequest()
    with pytest.raises(ValidationError, match="machine-scoped"):
        main.RetrievalHybridIndexRequest(postprocess_job_id=7)
    with pytest.raises(ValidationError, match="machine-scoped"):
        main.RetrievalHybridIndexRequest(postprocess_job_id=7, equipment_id="eq-crane")
    assert main.RetrievalHybridIndexRequest(equipment_id="eq-crane").equipment_id == "eq-crane"
