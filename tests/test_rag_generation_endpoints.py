import asyncio

from app import main


def _result():
    return {
        "rank": 1,
        "score": 12.3,
        "postprocess_job_id": 7,
        "source_filename": "Manual.pdf",
        "chunk_id": "CHK-1",
        "page_numbers": [10],
        "doc_items": ["#/texts/1"],
        "headings": ["Procedure"],
        "text": "Check the pressure at test point P1. Normal pressure is 10 bar.",
        "content_type": "prose",
        "quality_score": 100,
    }


def test_prompt_bundle_endpoint_is_model_free_and_exports_full_evidence(monkeypatch):
    async def fake_results(query, postprocess_job_id, equipment_id, top_k, retrieval_mode="hybrid"):
        assert query == "What is the normal pressure?"
        assert postprocess_job_id == 7
        assert equipment_id is None
        assert top_k == 20
        return [_result()], 1, [{"postprocess_job_id": 7, "result_dir": "missing", "visual_index_ready": False}], {"mode":"single_book", "postprocess_job_id":7}

    monkeypatch.setattr(main, "_retrieval_results_for_question", fake_results)
    payload = asyncio.run(main.retrieval_prompt_bundle(main.RetrievalPromptExportRequest(
        query="What is the normal pressure?", top_k=5, postprocess_job_id=7
    )))
    assert payload["generator_used"] is False
    assert payload["llm_calls"] == 0
    assert payload["sources"][0]["label"] == "S1"
    assert "Normal pressure is 10 bar" in payload["prompt"]
    assert "Page: 10" in payload["prompt"]


def test_generate_endpoint_uses_only_explicit_selected_provider(monkeypatch):
    async def fake_results(query, postprocess_job_id, equipment_id, top_k, retrieval_mode="hybrid"):
        return [_result()], 1, [{"postprocess_job_id": 7, "result_dir": "missing", "visual_index_ready": False}], {"mode":"single_book", "postprocess_job_id":7}

    calls = []

    async def fake_generate(provider, config, question, sources, *, quota_guard=None):
        calls.append((provider, question, [row["label"] for row in sources], quota_guard is main.runtime.groq_quota))
        return {
            "provider": provider,
            "provider_label": "Pi5" if provider == "pi5" else provider,
            "model": "test-model",
            "answer": "Normal pressure is 10 bar [S1].",
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            "finish_reason": "stop",
            "truncated": False,
            "latency_seconds": 0.1,
            "citation_labels": ["S1"],
            "invalid_citation_labels": [],
            "grounding_warning": None,
        }

    monkeypatch.setattr(main, "_retrieval_results_for_question", fake_results)
    monkeypatch.setattr(main, "generate_grounded_answer", fake_generate)
    payload = asyncio.run(main.retrieval_generate(main.RetrievalGenerateRequest(
        query="What is the normal pressure?", provider="pi5", top_k=5, postprocess_job_id=7
    )))
    assert calls == [("pi5", "What is the normal pressure?", ["S1"], True)]
    assert payload["generator_used"] is True
    assert payload["llm_calls"] == 1
    assert payload["provider"] == "pi5"
    assert payload["sources"][0]["page_numbers"] == [10]
    assert payload["evidence_scope"]["mode"] == "top_result_book"
