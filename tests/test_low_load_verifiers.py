import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from app.config import AppConfig
from app.stage2b import Stage2BWorker, _text_segments, _inspect_pi5_bounded, _merge_vision
from app.verifier_checkpoint import CheckpointVerifier
from app.verifier_clients import GroqVisionVerifier, OpenAICompatibleVerifier


@pytest.mark.asyncio
async def test_checkpoint_serializes_devices_and_survives_new_client(tmp_path):
    class Client:
        active = peak = calls = 0

        async def chat_text(self, *args, **kwargs):
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return {'response': 'ok'}

    client, lock = Client(), asyncio.Lock()
    a = CheckpointVerifier(client, lock, tmp_path / 'a.json', 'model:g1')
    b = CheckpointVerifier(client, lock, tmp_path / 'b.json', 'model:g1')
    await asyncio.gather(a.chat_text('a'), b.chat_text('b'))
    assert client.peak == 1
    restarted = CheckpointVerifier(client, lock, tmp_path / 'a.json', 'model:g1')
    assert (await restarted.chat_text('a'))['_checkpoint_reused']
    assert client.calls == 2
    await restarted.chat_text('changed input')
    assert client.calls == 3


@pytest.mark.asyncio
async def test_stream_returns_at_finish_without_reading_more():
    class Response:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': '{}'}, 'finish_reason': 'stop'}]})
            raise AssertionError('Must not wait for another event after completion')

        async def aclose(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def build_request(self, method, url, **kwargs):
            return httpx.Request(method, url)

        async def send(self, *args, **kwargs):
            return Response()

    with patch('app.verifier_clients.httpx.AsyncClient', Client):
        result = await OpenAICompatibleVerifier('http://phone').inspect_image_stream(b'image', 'prompt')
    assert result['_stream']['protocol_complete']


def test_segmentation_preserves_values_and_every_character():
    text = ('Set K1 to 24 V. Check pressure 12 bar.\n' * 200)
    segments = _text_segments(text)
    assert ''.join(segments) == text
    assert all(len(part) <= 1800 for part in segments)
    assert _text_segments('Pump 24 V ready', limit=9) == ['Pump ', '24 V ', 'ready']


@pytest.mark.asyncio
async def test_waiting_for_device_does_not_use_request_timeout(tmp_path):
    class Client:
        async def chat_text(self):
            return {'ok': True}

    lock = asyncio.Lock()
    await lock.acquire()
    verifier = CheckpointVerifier(Client(), lock, tmp_path / 'request.json', 'g', timeout=0.01)
    task = asyncio.create_task(verifier.chat_text())
    await asyncio.sleep(0.03)
    assert not task.done()
    lock.release()
    assert await task == {'ok': True}


@pytest.mark.asyncio
async def test_oversized_identifier_is_uncertain_without_inference():
    result, _, attempts = await _inspect_pi5_bounded(None, {}, 'X' * 3000, '', None, 160)
    assert result['verdict'] == 'UNCERTAIN'
    assert not result['coverage_complete']
    assert not attempts


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,expected_crops', [('CLASSIFICATION', 1), ('UNREADABLE_DETAILS', 4)])
async def test_vision_crops_stop_only_for_resolved_classification(tmp_path, monkeypatch, reason, expected_crops):
    worker, client, job = _vision_worker(tmp_path, monkeypatch, reason)
    _, result, _, _, _ = await worker._run_oneplus(job)
    assert result['parsed']['crop_count'] == expected_crops
    assert result['parsed']['summary'] == 'Visible diagram'
    assert result['parsed']['crop_early_stop'] == (expected_crops == 1)


def _vision_worker(tmp_path, monkeypatch, reason, fail_once=False):
    class Client:
        calls = []
        failed = False

        async def inspect_image_stream(self, data, prompt, **kwargs):
            self.calls.append(data)
            if fail_once and data == b'crop2' and not self.failed:
                self.failed = True
                raise httpx.ReadTimeout('temporary failure')
            full = data == b'full'
            parsed = dict(verdict='UNCERTAIN' if full else 'TECHNICAL_USEFUL', confidence=0.9,
                          visible_text=[], visible_objects=[], diagram_category='wiring_diagram',
                          summary='Visible diagram', unresolved=full, unresolved_reason=reason if full else '')
            return {'choices': [{'message': {'content': json.dumps(parsed)}, 'finish_reason': 'stop'}]}

    client = Client()
    cfg = AppConfig(processed_dir=str(tmp_path), output_dir=str(tmp_path))
    worker = Stage2BWorker(lambda: cfg, None, None, None)
    job = dict(id=1, result_dir='book', generation='g1', output_filename='book.zip',
               source_json='{"index": 0}', route_id='R1')

    async def document(_):
        return {}

    async def model(*_):
        return 'test-model'

    worker._document_for = document
    worker._model_for = model
    worker._vision_client_for_role = lambda *args: CheckpointVerifier(client, worker._device_locks['oneplus'],
                                                                     tmp_path / 'checkpoint.json', 'g1')
    worker._vision_client_for_role('oneplus', job).provider = 'oneplus'
    monkeypatch.setattr('app.stage2b._read_picture', lambda *args: (b'full', 'image/png', 'image.png'))
    monkeypatch.setattr('app.stage2b._vision_crops', lambda *args: [(str(i), f'crop{i}'.encode(), 'image/png') for i in range(4)])
    monkeypatch.setattr('app.stage2b.image_structure_evidence', lambda *_: {})
    return worker, client, job


@pytest.mark.asyncio
async def test_crop_retry_reuses_full_image_and_completed_crops(tmp_path, monkeypatch):
    worker, client, job = _vision_worker(tmp_path, monkeypatch, 'UNREADABLE_DETAILS', fail_once=True)
    with pytest.raises(httpx.ReadTimeout):
        await worker._run_oneplus(job)
    await worker._run_oneplus(job)
    assert client.calls == [b'full', b'crop0', b'crop1', b'crop2', b'crop2', b'crop3']


@pytest.mark.asyncio
async def test_groq_vision_uses_image_json_schema_and_records_usage(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        headers = {"x-request-id": "req_vision"}
        content = b"yes"
        is_success = True
        def json(self):
            return {
                "id": "req_vision",
                "choices": [{"message": {"content": json.dumps({
                    "verdict": "TECHNICAL_USEFUL", "confidence": 0.98,
                    "visible_text": ["K1"], "visible_objects": ["contactor"],
                    "diagram_category": "wiring_diagram", "summary": "Visible diagram",
                    "unresolved": False, "unresolved_reason": "",
                })}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2100, "completion_tokens": 80, "total_tokens": 2180},
            }
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, *args, **kwargs):
            captured["headers"] = kwargs.get("headers")
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, url, json=None):
            captured["url"] = url; captured["payload"] = json
            return Response()

    class Guard:
        def __init__(self): self.before = []; self.records = []
        async def before_request(self, tokens): self.before.append(tokens)
        async def record_response(self, response, **kwargs): self.records.append(kwargs); return {}
        async def snapshot(self): return {}

    guard = Guard()
    monkeypatch.setattr("app.verifier_clients.httpx.AsyncClient", Client)
    verifier = GroqVisionVerifier("https://api.groq.test/openai/v1", "secret", "qwen/qwen3.8-27b", quota_guard=guard,
                                  usage_context={"route_id": "V1", "book": "manual.zip"})
    body = await verifier.inspect_image_stream(b"png-bytes", "Read visible technical content only.")
    payload = captured["payload"]
    assert captured["url"].endswith("/chat/completions")
    assert payload["model"] == "qwen/qwen3.8-27b"
    assert payload["response_format"]["type"] == "json_schema"
    image = payload["messages"][0]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/png;base64,")
    assert guard.before and guard.records[0]["call_kind"] == "vision"
    assert guard.records[0]["input_tokens"] == 2100
    assert guard.records[0]["output_tokens"] == 80
    assert body["_stream"]["provider"] == "groq"


@pytest.mark.asyncio
async def test_groq_vision_logs_text_reconstruction_as_text_usage(monkeypatch):
    class Response:
        status_code = 200; headers = {}; content = b"yes"; is_success = True
        def json(self):
            return {"id": "req_text_crop", "choices": [{"message": {"content": '{"status":"READABLE","corrected_text":"Wind Speed"}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 2100, "completion_tokens": 20, "total_tokens": 2120}}
        def raise_for_status(self): return None
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, url, json=None): return Response()
    class Guard:
        def __init__(self): self.records = []
        async def before_request(self, tokens): return None
        async def record_response(self, response, **kwargs): self.records.append(kwargs); return {}
        async def snapshot(self): return {}
    guard = Guard()
    monkeypatch.setattr("app.verifier_clients.httpx.AsyncClient", Client)
    verifier = GroqVisionVerifier(
        "https://api.groq.test/openai/v1", "secret", "qwen/qwen3.8-27b",
        quota_guard=guard, usage_context={"purpose": "text_source_reconstruction"},
    )
    prompt = "Return ONLY one compact JSON object with exactly two keys: status and corrected_text."
    await verifier.inspect_image_stream(b"image", prompt)
    assert guard.records[0]["call_kind"] == "text_reconstruction"


@pytest.mark.asyncio
async def test_groq_vision_crosscheck_uses_direct_transcription_schema(monkeypatch):
    captured = {}
    class Response:
        status_code = 200; headers = {}; content = b"yes"; is_success = True
        def json(self):
            return {"id": "req_x", "choices": [{"message": {"content": '{"status":"READABLE","corrected_text":"hoisting"}'}, "finish_reason": "stop"}], "usage": {}}
        def raise_for_status(self): return None
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, url, json=None): captured["payload"] = json; return Response()
    monkeypatch.setattr("app.verifier_clients.httpx.AsyncClient", Client)
    verifier = GroqVisionVerifier("https://api.groq.test/openai/v1", "secret", "qwen/qwen3.8-27b")
    prompt = "Return ONLY one compact JSON object with exactly two keys: status and corrected_text. Read the printed text exactly."
    await verifier.inspect_image_stream(b"image", prompt)
    schema = captured["payload"]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["status"]["enum"] == ["READABLE", "UNREADABLE"]
    assert schema["required"] == ["status", "corrected_text"]
    assert captured["payload"]["response_format"]["json_schema"]["name"] == "vision_direct_transcription"
