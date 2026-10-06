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
    assert missing["grounding_passed"] is False
    assert missing["answer_usable"] is False
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


def test_equipment_scope_filters_out_of_scope_anchor_and_neighbors_before_source_selection():
    from app.rag_generation import prepare_generation_sources

    rows = [
        {
            "postprocess_job_id": 999,
            "source_filename": "Wrong Machine.pdf",
            "chunk_id": "BAD-1",
            "text": "Out of scope top result.",
            "context_neighbors": [{"chunk_id": "BAD-2", "text": "Out of scope neighbor."}],
        },
        {
            "postprocess_job_id": 7,
            "source_filename": "Deck Crane.pdf",
            "chunk_id": "GOOD-1",
            "text": "Deck crane brake inspection procedure.",
            "context_neighbors": [{"chunk_id": "GOOD-2", "text": "Inspect the brake lining clearance."}],
        },
    ]
    visuals = [
        {"postprocess_job_id": 999, "source_filename": "Wrong Machine.pdf", "chunk_id": "V-BAD", "summary": "wrong"},
        {"postprocess_job_id": 7, "source_filename": "Deck Crane.pdf", "chunk_id": "V-GOOD", "visible_text": ["BRAKE"], "summary": "brake diagram"},
    ]
    sources, scope = prepare_generation_sources(
        rows,
        "How do I inspect the deck crane brake?",
        visual_results=visuals,
        max_sources=5,
        allowed_job_ids={7},
        equipment_name="Deck Crane",
    )
    assert scope["mode"] == "equipment"
    assert sources
    assert all(int(source.get("postprocess_job_id") or 0) == 7 for source in sources)
    assert not any("Wrong Machine" in str(source.get("source_filename")) for source in sources)


def test_claim_grounding_rejects_existing_citation_that_does_not_support_technical_value():
    sources = prepare_sources(_rows(), max_sources=1)
    result = citation_audit("The neutral output is approximately +12 V [S1].", sources)
    assert result["citation_labels"] == ["S1"]
    assert result["grounding_passed"] is False
    assert result["answer_usable"] is False
    assert result["unsupported_claim_count"] == 1
    assert result["unsupported_claims"][0]["reason"] == "critical_token_not_in_cited_source"


def test_claim_grounding_rejects_uncited_technical_claim_even_when_other_claim_is_cited():
    sources = prepare_sources(_rows(), max_sources=1)
    answer = "The neutral output is approximately +6 V [S1].\nThe trip threshold is 12 bar."
    result = citation_audit(answer, sources)
    assert result["grounding_passed"] is False
    assert any(row["reason"] == "missing_claim_citation" for row in result["unsupported_claims"])

def test_equipment_scope_recovers_stale_text_job_id_and_keeps_mixed_s_v_evidence():
    from app.rag_generation import prepare_generation_sources

    books = [{
        "postprocess_job_id": 21,
        "source_filename": "Fire alarm panel",
        "result_dir": "Fire alarm panel__job12__run0",
    }]
    rows = [{
        "postprocess_job_id": 12,  # stale id embedded in an older retrieval index
        "source_filename": "Fire alarm panel",
        "result_dir": "Fire alarm panel__job12__run0",
        "chunk_id": "CHK-000171",
        "page_numbers": [47],
        "text": "The HC100 is a conventional heat detector. The sensor is a thermistor that reacts to changes in temperature.",
    }]
    visuals = [{
        "postprocess_job_id": 21,
        "source_filename": "Fire alarm panel",
        "result_dir": "Fire alarm panel__job12__run0",
        "visual_evidence_id": "VE-1",
        "page_numbers": [23],
        "picture_index": 57,
        "category": "electrical_schematic",
        "visible_text": ["Last detector", "End of line resistor"],
        "summary": "Detector wiring schematic",
    }]
    sources, scope = prepare_generation_sources(
        rows,
        "What sensor does the HC100 heat detector use?",
        visual_results=visuals,
        max_sources=5,
        allowed_job_ids={21},
        allowed_books=books,
        equipment_name="Fire alarm panel",
    )
    labels = [source["label"] for source in sources]
    assert "S1" in labels and "V1" in labels
    assert scope["text_evidence_count"] == 1
    assert scope["visual_evidence_count"] == 1
    s1 = next(source for source in sources if source["label"] == "S1")
    assert s1["postprocess_job_id"] == 21
    assert "thermistor" in s1["text"]


def test_equipment_scope_fails_closed_instead_of_silently_sending_visual_only():
    from app.rag_generation import prepare_generation_sources

    rows = [{
        "postprocess_job_id": 999,
        "source_filename": "Unrelated manual",
        "result_dir": "wrong__job999__run0",
        "chunk_id": "BAD",
        "text": "Unrelated text.",
    }]
    visuals = [{
        "postprocess_job_id": 21,
        "source_filename": "Fire alarm panel",
        "result_dir": "Fire alarm panel__job12__run0",
        "visual_evidence_id": "VE-1",
        "visible_text": ["detector"],
        "summary": "Detector wiring",
    }]
    sources, scope = prepare_generation_sources(
        rows,
        "What sensor is used?",
        visual_results=visuals,
        max_sources=5,
        allowed_job_ids={21},
        allowed_books=[{
            "postprocess_job_id": 21,
            "source_filename": "Fire alarm panel",
            "result_dir": "Fire alarm panel__job12__run0",
        }],
        equipment_name="Fire alarm panel",
    )
    assert sources == []
    assert scope["scope_error"] == "text_provenance_mismatch"


def test_refusal_phrase_cannot_hide_an_unsupported_claim():
    sources=[{'label':'S1','text':'Pump pressure is 12 bar.'}]
    result=citation_audit('Not enough information in the retrieved sources. Pump pressure is 999 bar [S1].',sources)
    assert result['answer_usable'] is False
    assert result['grounding_warning']

import pytest
@pytest.mark.parametrize('source,answer', [
    ('Pump pressure is 16 bar.', 'Pump pressure is 6 bar [S1].'),
    ('Neutral output is -6 V.', 'Neutral output is +6 V [S1].'),
    ('The valve opens at 25%.', 'The valve opens at 75% [S1].'),
    ('Detector HC1000 uses a thermistor.', 'Detector HC100 uses a thermistor [S1].'),
])
def test_critical_values_require_whole_tokens_and_preserve_sign(source,answer):
    result=citation_audit(answer,[{'label':'S1','text':source}])
    assert result['answer_usable'] is False
    assert result['unsupported_claim_count']==1

def test_citation_only_line_belongs_to_preceding_claim():
    result=citation_audit('The HC100 uses a thermistor.\n[S1]',[{'label':'S1','text':'The HC100 uses a thermistor.'}])
    assert result['answer_usable'] is True

def test_part_number_cannot_be_substring_of_another_number():
    result=citation_audit('The part number is 3800 [S1].',[{'label':'S1','text':'The part number is 38000.'}])
    assert result['answer_usable'] is False

def test_number_after_comma_is_not_a_complete_source_value():
    result=citation_audit('Change coolant every 200 hours [S1].',[{'label':'S1','text':'Change coolant every 1,200 hours.'}])
    assert result['answer_usable'] is False

@pytest.mark.asyncio
async def test_unknown_question_model_refuses_without_a_generation_call(monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('Must not call a generator')
    monkeypatch.setattr('app.rag_generation._generate_local',forbidden)
    result=await generate_grounded_answer('pi5',AppConfig(),'What sensor does HC9999 use?',[{'label':'S1','text':'The HC100 uses a thermistor.'}])
    assert result['insufficient_evidence'] is True
    assert result['model_called'] is False
    assert result['missing_question_identifiers']==['HC9999']

def test_citation_does_not_validate_reversed_source_prohibition():
    result=citation_audit('Run the pump dry [S1].',[{'label':'S1','text':'Do not run the pump dry.'}])
    assert result['answer_usable'] is False
    assert result['unsupported_claims'][0]['reason']=='source_prohibition_reversed'

def test_source_prohibition_can_be_quoted_faithfully():
    result=citation_audit('Do not run the pump dry [S1].',[{'label':'S1','text':'Do not run the pump dry.'}])
    assert result['answer_usable'] is True

def test_two_character_terminal_id_must_exist_in_its_citation():
    result=citation_audit('Terminal A2 supplies 24 V [S1].',[{'label':'S1','text':'Terminal A1 supplies 24 V.'}])
    assert result['answer_usable'] is False

@pytest.mark.parametrize('source,answer',[
 ('Turn the handle counterclockwise.','Turn the handle clockwise [S1].'),
 ('Use the N.C. contact.','Use the N.O. contact [S1].'),
 ('Use the normally closed contact.','Use the normally open contact [S1].'),
])
def test_rotation_and_contact_state_cannot_be_reversed(source,answer):
    assert citation_audit(answer,[{'label':'S1','text':source}])['answer_usable'] is False


def test_comma_separated_item_list_keeps_identical_source_tokens():
    answer = "Repair set for items 10, 17, 23, 71 is 0000020824 [S1]."
    source = "Repair set for items 10,17,23,71 is 0000020824."
    assert citation_audit(answer, [{"label": "S1", "text": source}])["answer_usable"] is True


def test_large_number_cannot_be_split_as_item_list():
    result = citation_audit("The capacity is 200 [S1].", [{"label": "S1", "text": "The capacity is 1,200,000."}])
    assert result["answer_usable"] is False


def test_uppercase_normally_open_contact_is_preserved():
    assert citation_audit("Use the normally open contact [S1].", [{"label": "S1", "text": "Use the NORMALLY OPEN contact."}])["answer_usable"] is True


def test_number_before_sentence_period_matches_source_value():
    assert citation_audit("The part number is 38000 [S1].", [{"label": "S1", "text": "The part number is 38000."}])["answer_usable"] is True


def test_unicode_nonbreaking_hyphen_preserves_technical_identifier():
    assert citation_audit("HE.CT1\u2011U1 is a power supply [S1].", [{"label": "S1", "text": "HE.CT1-U1 is a power supply."}])["answer_usable"] is True


def test_procedure_markdown_title_is_not_an_uncited_factual_claim():
    answer = "**Procedure for checking the pump**\nCheck the pump [S1]."
    assert citation_audit(answer, [{"label": "S1", "text": "Check the pump."}])["answer_usable"] is True


def test_bold_procedure_assertion_still_requires_a_citation():
    answer = "**Procedure requires disconnecting the pump**"
    assert citation_audit(answer, [{"label": "S1", "text": "Disconnect the pump."}])["answer_usable"] is False

@pytest.mark.parametrize('source,answer,reason', [
 ('Pump A1 pressure is 12 bar. Pump A2 pressure is 24 bar.', 'Pump A1 pressure is 24 bar [S1].', 'technical_association_not_located'),
 ('Oil overheats because the fan stops.', 'The fan stops because oil overheats [S1].', 'causal_direction_not_located'),
 ('When the oil overheats, press Start to cool the oil.', 'Press Start to cool the oil [S1].', 'source_condition_omitted'),
 ('Thermostat BT2 opens at 85°C. Press Start to cool the oil.', 'Oil overheats because thermostat BT2 opens at 85°C [S1].', 'causal_direction_not_located'),
])
def test_relationship_checks_reject_recombined_or_reversed_claims(source, answer, reason):
 result = citation_audit(answer, [{'label':'S1','text':source}])
 assert result['answer_usable'] is False
 assert result['unsupported_claims'][0]['reason'] == reason
 assert result['semantic_entailment_verified'] is False


def test_relationship_checks_preserve_direct_cause_and_condition():
 for text in ['Oil overheats because the fan stops.', 'When oil overheats, press Start to cool the oil.']:
  assert citation_audit(text+' [S1]', [{'label':'S1','text':text}])['answer_usable'] is True


def test_visual_summary_cannot_supply_causal_relationship():
 result = citation_audit('Oil overheats because the fan stops [V1].', [{'label':'V1','source_kind':'visual','visible_text':['Oil temperature'], 'summary':'Oil overheats because the fan stops.'}])
 assert result['answer_usable'] is False

def test_operating_hours_preserves_exact_numeric_interval():
 source='The coolant must be changed at intervals of 1,200 hours operation or six months whichever comes first.'
 assert citation_audit('Change coolant every 1,200 operating hours or six months whichever comes first [S1].',[{'label':'S1','text':source}])['answer_usable'] is True
 assert citation_audit('Change coolant every 200 operating hours [S1].',[{'label':'S1','text':source}])['answer_usable'] is False


def test_grounded_prompt_requests_single_sentence_cited_bullets():
 from app.rag_generation import build_grounded_user_prompt
 prompt=build_grounded_user_prompt('What should I do?',[{'label':'S1','text':'Disconnect the supply.'}])
 assert 'ONE factual sentence' in prompt
 assert 'without headings' in prompt

@pytest.mark.asyncio
async def test_generation_canonicalizes_only_explicit_citation_labels(monkeypatch):
 from app.rag_generation import GenerationResult
 async def fake(*args,**kwargs):
  return GenerationResult('pi5','model',None,'Disconnect the supply [ S1 ].',{},'stop',0)
 monkeypatch.setattr('app.rag_generation._generate_local',fake)
 result=await generate_grounded_answer('pi5',AppConfig(),'What should I disconnect?',[{'label':'S1','text':'Disconnect the supply.'}])
 assert result['answer']=='Disconnect the supply [S1].'
 assert result['answer_usable'] is True
