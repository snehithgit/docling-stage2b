import json
import unittest
from unittest.mock import patch

import httpx

from app.verifier_clients import OpenAICompatibleVerifier
from app.stage2b import _inspect_vision_region


class FakeStreamResponse:
    def __init__(self, request: httpx.Request, lines):
        self.request = request
        self._lines = list(lines)
        self.status_code = 200

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aclose(self):
        return None


class FakeAsyncClient:
    lines = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def build_request(self, method, url, json=None):
        return httpx.Request(method, url, json=json)

    async def send(self, request, stream=False):
        return FakeStreamResponse(request, type(self).lines)


class VerifierStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconstructs_stream_and_detects_explicit_completion(self):
        payload = {
            "verdict": "TECHNICAL_USEFUL",
            "confidence": 0.9,
            "visible_labels": ["K59"],
            "summary": "controls",
            "unresolved": False,
            "unresolved_reason": "",
        }
        text = json.dumps(payload)
        midpoint = len(text) // 2
        FakeAsyncClient.lines = [
            'data: {"id":"x","model":"m","choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}',
            'data: ' + json.dumps({"id":"x","model":"m","choices":[{"delta":{"content":text[:midpoint]},"finish_reason":None}]}),
            'data: ' + json.dumps({"id":"x","model":"m","choices":[{"delta":{"content":text[midpoint:]},"finish_reason":None}]}),
            'data: ' + json.dumps({"id":"x","model":"m","choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"completion_tokens":42,"total_tokens":142}}),
            'data: [DONE]',
        ]
        progress = []
        client = OpenAICompatibleVerifier("http://phone:8080")
        with patch("app.verifier_clients.httpx.AsyncClient", FakeAsyncClient):
            raw = await client.inspect_image_stream(
                b"img", "prompt", model="m", max_tokens=240,
                first_token_timeout_seconds=1200,
                idle_timeout_seconds=300,
                on_progress=lambda item: progress.append(dict(item)),
            )
        self.assertEqual(json.loads(raw["choices"][0]["message"]["content"]), payload)
        self.assertEqual(raw["choices"][0]["finish_reason"], "stop")
        self.assertTrue(raw["_stream"]["protocol_complete"])
        self.assertTrue(raw["_stream"]["done_received"])
        self.assertEqual(raw["_stream"]["completion_tokens"], 42)
        self.assertEqual(raw["_stream"]["content_chunk_count"], 2)
        self.assertTrue(any(item.get("phase") == "streaming" for item in progress))
        self.assertEqual(progress[-1]["phase"], "complete")

    async def test_stream_without_finish_or_done_is_protocol_error(self):
        FakeAsyncClient.lines = [
            'data: ' + json.dumps({"choices":[{"delta":{"content":"{\"x\":1}"},"finish_reason":None}]})
        ]
        client = OpenAICompatibleVerifier("http://phone:8080")
        with patch("app.verifier_clients.httpx.AsyncClient", FakeAsyncClient):
            with self.assertRaises(httpx.RemoteProtocolError):
                await client.inspect_image_stream(
                    b"img", "prompt", first_token_timeout_seconds=1200, idle_timeout_seconds=300
                )

    async def test_length_finish_triggers_json_repair_stream(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def inspect_image_stream(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "choices": [{"message": {"content": '{"verdict":"TECHNICAL_USEFUL"}'}, "finish_reason": "length"}],
                        "_stream": {"finish_reason": "length", "done_received": True, "protocol_complete": True},
                    }
                return {
                    "choices": [{"message": {"content": json.dumps({
                        "verdict": "TECHNICAL_USEFUL", "confidence": 0.8,
                        "visible_labels": [], "summary": "ok", "unresolved": False,
                        "unresolved_reason": "",
                    })}, "finish_reason": "stop"}],
                    "_stream": {"finish_reason": "stop", "done_received": True, "protocol_complete": True},
                }

        client = FakeClient()
        parsed, _, attempts = await _inspect_vision_region(
            client, b"image", "prompt", "image/png", "model", 240,
            first_token_timeout_seconds=1200,
            stream_idle_timeout_seconds=300,
        )
        self.assertEqual(client.calls, 2)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(parsed["verdict"], "TECHNICAL_USEFUL")


if __name__ == "__main__":
    unittest.main()
