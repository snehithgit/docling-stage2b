import asyncio

import pytest

from app.config import AppConfig
from app.rag_generation import (
    build_grounded_user_prompt,
    build_portable_prompt,
    citation_audit,
    completion_text,
    generate_grounded_answer,
    prepare_sources,
)


def _rows():
    return [
        {
            "postprocess_job_id": 7,
            "source_filename": "Instruction Manual.pdf",
            "chunk_id": "CHK-22",
            "page_numbers": [74],
            "doc_items": ["#/texts/100"],
            "headings": ["Control levers"],
            "text": "The joystick potentiometer output is approximately +6V with the joystick in neutral.",
            "quality_score": 100,
        },
        {
            "postprocess_job_id": 8,
            "source_filename": "Other Manual.pdf",
            "chunk_id": "CHK-9",
            "page_numbers": [12],
            "doc_items": ["#/texts/9"],
            "headings": ["Joystick"],
            "text": "The joystick must be in neutral before starting.",
            "quality_score": 100,
        },
    ]


def test_portable_prompt_contains_question_full_chunks_and_citation_metadata():
    sources = prepare_sources(_rows(), max_sources=2)
    prompt = build_portable_prompt("What is the joystick neutral voltage?", sources)
    assert "What is the joystick neutral voltage?" in prompt
    assert "[S1]" in prompt and "[S2]" in prompt
    assert "Instruction Manual" in prompt
    assert "Page: 74" in prompt
    assert "Docling refs: #/texts/100" in prompt
    assert "approximately +6V" in prompt
    assert "Use ONLY the SOURCE EXCERPTS" in prompt
    assert "Not enough information in the retrieved sources." in prompt


def test_prepare_sources_assigns_stable_labels_and_keeps_provenance():
    sources = prepare_sources(_rows(), max_sources=1)
    assert [row["label"] for row in sources] == ["S1"]
    assert sources[0]["page_numbers"] == [74]
    assert sources[0]["doc_items"] == ["#/texts/100"]
    assert sources[0]["chunk_id"] == "CHK-22"


def test_grounded_prompt_treats_sources_as_evidence_not_instructions():
    sources = prepare_sources(_rows(), max_sources=1)
    prompt = build_grounded_user_prompt("What is the neutral voltage?", sources)
    assert "SOURCE EXCERPTS" in prompt
    assert "Do not invent missing steps, values, causes, or safety limits." in prompt
    assert "[S1]" in prompt


def test_completion_text_removes_local_reasoning_tags():
    raw = {"choices": [{"message": {"content": "<think>private reasoning</think>Neutral output is about +6 V [S1]."}}]}
    assert completion_text(raw) == "Neutral output is about +6 V [S1]."


def test_generate_local_uses_selected_provider_without_fallback(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, base_url, timeout_seconds=180):
            calls.append((base_url, timeout_seconds))

        async def chat_text(self, system, user, model=None, max_tokens=160):
            assert "ONLY from the SOURCE EXCERPTS" in system
            assert "[S1]" in user
            return {
                "model": "local-test-model",
                "choices": [{"finish_reason": "stop", "message": {"content": "Neutral output is approximately +6 V [S1]."}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 12, "total_tokens": 112},
            }

    monkeypatch.setattr("app.rag_generation.OpenAICompatibleVerifier", FakeClient)
    config = AppConfig(pi5_url="http://pi5.test:8080", oneplus_url="http://phone.test:8080")
    config.validate()
    sources = prepare_sources(_rows(), max_sources=1)
    result = asyncio.run(generate_grounded_answer("oneplus", config, "What is the joystick neutral voltage?", sources))
    assert calls == [("http://phone.test:8080", config.rag_answer_local_timeout_seconds)]
    assert result["provider"] == "oneplus"
    assert result["answer"].endswith("[S1].")
    assert result["usage"]["total_tokens"] == 112


def test_generate_rejects_unknown_provider():
    config = AppConfig()
    config.validate()
    with pytest.raises(ValueError, match="pi5, oneplus, or groq"):
        asyncio.run(generate_grounded_answer("automatic", config, "Question?", prepare_sources(_rows(), max_sources=1)))


def test_citation_audit_warns_when_model_omits_or_invents_source_labels():
    sources = prepare_sources(_rows(), max_sources=1)
    missing = citation_audit("Neutral output is approximately +6 V.", sources)
    assert missing["grounding_warning"]
    invented = citation_audit("Neutral output is approximately +6 V [S9].", sources)
    assert invented["invalid_citation_labels"] == ["S9"]
    good = citation_audit("Neutral output is approximately +6 V [S1].", sources)
    assert good["citation_labels"] == ["S1"]
    assert good["grounding_warning"] is None


def test_generate_groq_uses_configured_cloud_model_and_quota_guard(monkeypatch):
    calls = {}

    class FakeResponse:
        status_code = 200
        content = b'{}'
        headers = {}

        def json(self):
            return {
                "id": "req-1",
                "model": "openai/gpt-oss-20b",
                "choices": [{"finish_reason": "stop", "message": {"content": "Neutral output is approximately +6 V [S1]."}}],
                "usage": {"prompt_tokens": 90, "completion_tokens": 14, "total_tokens": 104},
            }

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            calls["headers"] = kwargs.get("headers")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls["url"] = url
            calls["payload"] = json
            response = FakeResponse()
            response.content = b'{"ok":true}'
            return response

    class FakeQuota:
        def __init__(self):
            self.before = []
            self.recorded = []

        async def before_request(self, estimate):
            self.before.append(estimate)

        async def record_response(self, response, **kwargs):
            self.recorded.append(kwargs)

    monkeypatch.setattr("app.rag_generation.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("GROQ_API_KEY", "secret-key")
    config = AppConfig(text_cloud_base_url="https://api.groq.com/openai/v1", text_cloud_model="openai/gpt-oss-20b")
    config.validate()
    quota = FakeQuota()
    result = asyncio.run(generate_grounded_answer(
        "groq", config, "What is the joystick neutral voltage?", prepare_sources(_rows(), max_sources=1), quota_guard=quota
    ))
    assert calls["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert calls["payload"]["model"] == "openai/gpt-oss-20b"
    assert calls["payload"]["messages"][0]["role"] == "system"
    assert calls["headers"]["Authorization"] == "Bearer secret-key"
    assert quota.before and quota.recorded[0]["call_kind"] == "rag_generation"
    assert result["provider"] == "groq"
    assert result["citation_labels"] == ["S1"]
    assert result["usage"]["total_tokens"] == 104


def test_generation_sources_lock_to_top_result_book_and_include_adjacent_context():
    from app.rag_generation import prepare_generation_sources

    rows = _rows()
    rows[0]["context_neighbors"] = [
        {
            "chunk_id": "CHK-23",
            "page_numbers": [75],
            "doc_items": ["#/texts/101"],
            "text": "Adjacent same-manual continuation with a measurement step.",
        }
    ]
    sources, scope = prepare_generation_sources(rows, "How do I adjust the joystick?", max_sources=5)
    assert scope["mode"] == "top_result_book"
    assert scope["book"] == "Instruction Manual"
    assert scope["includes_adjacent_context"] is True
    assert [s["source_filename"] for s in sources] == ["Instruction Manual.pdf", "Instruction Manual.pdf"]
    assert [s["chunk_id"] for s in sources] == ["CHK-22", "CHK-23"]
    assert sources[1]["evidence_role"] == "adjacent_context"


def test_equipment_scope_can_use_multiple_current_manuals_without_cross_book_refusal():
    from app.rag_generation import prepare_generation_sources

    rows = _rows()
    sources, scope = prepare_generation_sources(
        rows,
        "Compare the operating instruction with the maintenance instruction",
        max_sources=5,
        allowed_job_ids={7, 8},
        equipment_name="Deck Crane",
    )
    assert scope["mode"] == "equipment"
    assert scope["equipment"] == "Deck Crane"
    assert {s["source_filename"] for s in sources} == {"Instruction Manual.pdf", "Other Manual.pdf"}


def test_generation_sources_allow_cross_book_only_for_explicit_comparison():
    from app.rag_generation import prepare_generation_sources

    sources, scope = prepare_generation_sources(_rows(), "Compare the joystick instructions across manuals", max_sources=5)
    assert scope["mode"] == "cross_book"
    assert {s["source_filename"] for s in sources} == {"Instruction Manual.pdf", "Other Manual.pdf"}


def test_citation_audit_accepts_safe_not_enough_information_without_citation():
    sources = prepare_sources(_rows(), max_sources=1)
    result = citation_audit(
        "Not enough information in the retrieved sources. The actual adjustment steps are missing.",
        sources,
    )
    assert result["insufficient_evidence"] is True
    assert result["grounding_warning"] is None


def test_local_connection_failure_names_provider_and_endpoint(monkeypatch):
    import httpx

    class FailingClient:
        def __init__(self, base_url, timeout_seconds=180):
            self.base_url = base_url

        async def chat_text(self, system, user, model=None, max_tokens=160):
            request = httpx.Request("POST", f"{self.base_url}/v1/chat/completions")
            raise httpx.ConnectError("All connection attempts failed", request=request)

    monkeypatch.setattr("app.rag_generation.OpenAICompatibleVerifier", FailingClient)
    config = AppConfig(pi5_url="http://192.168.68.55:8080")
    config.validate()
    with pytest.raises(RuntimeError, match=r"Pi5 is unreachable at http://192\.168\.68\.55:8080"):
        asyncio.run(generate_grounded_answer("pi5", config, "Question?", prepare_sources(_rows(), max_sources=1)))
