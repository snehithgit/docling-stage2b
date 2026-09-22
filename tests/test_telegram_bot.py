import json

import pytest

from app.telegram_bot import TelegramBotService


class _Config:
    telegram_enabled = True
    telegram_bot_token_env = "TELEGRAM_BOT_TOKEN"
    telegram_allowed_chat_ids = [123]
    telegram_notifications = True
    telegram_controls = True


@pytest.mark.asyncio
async def test_telegram_ignores_unauthorized_chat_and_dispatches_authorized_monitoring_command(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    calls = []
    sent = []

    async def handler(command, args):
        calls.append((command, args))
        return "status reply"

    service = TelegramBotService(lambda: _Config(), handler)

    async def fake_send(text, chat_id=None):
        sent.append((text, chat_id))

    service.send = fake_send
    await service._handle(999, "/status")
    assert calls == [] and sent == []

    await service._handle(123, "/status extra")
    assert calls == [("/status", ["extra"])]
    assert sent == [("status reply", 123)]


@pytest.mark.asyncio
async def test_telegram_strips_bot_suffix_and_ignores_non_commands(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    calls = []
    async def handler(command, args):
        calls.append((command, args)); return "ok"
    service = TelegramBotService(lambda: _Config(), handler)
    service.send = lambda *args, **kwargs: None  # not reached for plain text
    await service._handle(123, "hello")
    assert calls == []

    async def fake_send(*args, **kwargs): return None
    service.send = fake_send
    await service._handle(123, "/status@marine_bot")
    assert calls == [("/status", [])]


@pytest.mark.asyncio
async def test_telegram_stop_closes_persistent_http_client(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    async def handler(command, args): return "ok"
    service = TelegramBotService(lambda: _Config(), handler)

    class Client:
        def __init__(self): self.closed = False
        async def aclose(self): self.closed = True

    client = Client()
    service._client = client
    await service.stop()
    assert client.closed is True
    assert service._client is None


def test_telegram_service_is_monitoring_plane_not_direct_database_writer():
    import inspect
    import app.telegram_bot as module
    source = inspect.getsource(module)
    assert "sqlite3" not in source
    assert "correction_ledger.json" not in source
    assert "jobs.db" not in source

@pytest.mark.asyncio
async def test_telegram_text_audit_sends_one_card_then_next_after_decision(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    decisions = []
    next_calls = 0

    async def handler(command, args):
        return "monitor"

    async def audit_handler(action, kind, payload):
        nonlocal next_calls
        assert kind == "text"
        if action == "next":
            next_calls += 1
            if next_calls == 1:
                return {
                    "key": {"entry_id": "e1"},
                    "caption": "first text audit",
                    "image": b"png",
                    "mime_type": "image/png",
                    "options": [
                        {"label": "Apply", "value": "apply"},
                        {"label": "Keep", "value": "keep_original"},
                    ],
                }
            return {"done": True, "remaining": 0}
        decisions.append((payload["key"], payload["decision"]))
        return {"ok": True, "message": "saved"}

    service = TelegramBotService(lambda: _Config(), handler, audit_handler=audit_handler)
    sent_photos = []
    api_calls = []

    async def fake_photo(image, mime_type, caption, chat_id, reply_markup=None):
        sent_photos.append((caption, chat_id, reply_markup))
        return {"result": {"message_id": 77}}

    async def fake_api(method, payload=None):
        api_calls.append((method, payload or {}))
        return {"ok": True}

    service.send_photo = fake_photo
    service._api = fake_api
    await service._handle(123, "/text audit")
    assert len(sent_photos) == 1
    assert "Stop verify audit" in str(sent_photos[0][2])
    assert service._audit_sessions[123]["current_message_id"] == 77

    await service._handle_callback({
        "id": "cb1",
        "data": "aud:d:apply",
        "message": {"message_id": 77, "chat": {"id": 123}},
    })
    assert decisions == [({"entry_id": "e1"}, "apply")]
    assert next_calls == 2
    assert 123 not in service._audit_sessions
    assert any(method == "answerCallbackQuery" for method, _ in api_calls)


@pytest.mark.asyncio
async def test_telegram_audit_aliases_include_visual_artifact_and_article(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    started = []

    async def handler(command, args):
        return "monitor"

    async def audit_handler(action, kind, payload):
        if action == "next":
            started.append(kind)
            return {"done": True, "remaining": 0}
        return {"ok": True}

    service = TelegramBotService(lambda: _Config(), handler, audit_handler=audit_handler)

    async def fake_send(*args, **kwargs):
        return {}

    service.send = fake_send
    await service._handle(123, "/visionaudit")
    await service._handle(123, "/visual audit")
    await service._handle(123, "/artifact audit")
    await service._handle(123, "/articleaudit")
    assert started == ["vision", "vision", "artifact", "artifact"]


@pytest.mark.asyncio
async def test_telegram_stop_verify_audit_stops_future_cards(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")

    async def handler(command, args):
        return "monitor"

    async def audit_handler(action, kind, payload):
        return {
            "key": {"entry_id": "v1"},
            "caption": "vision",
            "image": b"image",
            "mime_type": "image/png",
            "options": [{"label": "Useful", "value": "useful"}],
        }

    service = TelegramBotService(lambda: _Config(), handler, audit_handler=audit_handler)
    messages = []

    async def fake_photo(*args, **kwargs):
        return {"result": {"message_id": 88}}

    async def fake_send(text, chat_id=None, **kwargs):
        messages.append((text, chat_id))
        return {}

    async def fake_api(method, payload=None):
        return {"ok": True}

    service.send_photo = fake_photo
    service.send = fake_send
    service._api = fake_api
    await service._handle(123, "/vision audit")
    assert 123 in service._audit_sessions
    await service._handle_callback({
        "id": "cb-stop",
        "data": "aud:s",
        "message": {"message_id": 88, "chat": {"id": 123}},
    })
    assert 123 not in service._audit_sessions
    assert any("audit stopped" in text.lower() for text, _ in messages)


class _NoControlsConfig(_Config):
    telegram_controls = False


@pytest.mark.asyncio
async def test_telegram_controls_false_blocks_audit_start(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    started = []
    sent = []

    async def handler(command, args):
        return "monitor"

    async def audit_handler(action, kind, payload):
        started.append((action, kind))
        return {"done": True, "remaining": 0}

    service = TelegramBotService(lambda: _NoControlsConfig(), handler, audit_handler=audit_handler)

    async def fake_send(text, chat_id=None, **kwargs):
        sent.append((text, chat_id))
        return {}

    service.send = fake_send
    await service._handle(123, "/textaudit")
    assert started == []
    assert 123 not in service._audit_sessions
    assert any("controls are disabled" in text.lower() for text, _ in sent)


@pytest.mark.asyncio
async def test_telegram_controls_false_blocks_existing_decision(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    decisions = []

    async def handler(command, args):
        return "monitor"

    async def audit_handler(action, kind, payload):
        decisions.append((action, kind, payload))
        return {"ok": True}

    service = TelegramBotService(lambda: _NoControlsConfig(), handler, audit_handler=audit_handler)
    service._audit_sessions[123] = {
        "type": "text",
        "reviewed": 0,
        "current": {"entry_id": "e1"},
        "current_message_id": 77,
    }
    api_calls = []

    async def fake_api(method, payload=None):
        api_calls.append((method, payload or {}))
        return {"ok": True}

    service._api = fake_api
    await service._handle_callback({
        "id": "cb-disabled",
        "data": "aud:d:apply",
        "message": {"message_id": 77, "chat": {"id": 123}},
    })
    assert decisions == []
    assert any("controls are disabled" in str(payload.get("text") or "").lower() for method, payload in api_calls if method == "answerCallbackQuery")


@pytest.mark.asyncio
async def test_send_splits_formatted_messages_and_preserves_entities(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    async def handler(command, args):
        return "monitor"
    service = TelegramBotService(lambda: _Config(), handler)
    api_calls = []

    def fake_chunks(value, max_utf16_len=4000):
        assert max_utf16_len == 4000
        return [
            ("first", [{"type": "bold", "offset": 0, "length": 5}]),
            ("second", []),
        ]

    async def fake_api(method, payload=None):
        api_calls.append((method, payload or {}))
        return {"result": {"message_id": len(api_calls)}}

    monkeypatch.setattr("app.telegram_bot._markdown_chunks", fake_chunks)
    service._api = fake_api
    await service.send("**first** second", 123, reply_markup={"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]})
    assert [payload["text"] for method, payload in api_calls if method == "sendMessage"] == ["first", "second"]
    assert api_calls[0][1]["entities"][0]["type"] == "bold"
    assert "reply_markup" not in api_calls[0][1]
    assert "reply_markup" in api_calls[1][1]


@pytest.mark.asyncio
async def test_send_photo_uses_caption_entities_and_keeps_keyboard_on_image(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    async def handler(command, args):
        return "monitor"
    service = TelegramBotService(lambda: _Config(), handler)
    multipart_calls = []
    api_calls = []

    def fake_chunks(value, max_utf16_len=4000):
        assert max_utf16_len == 1024
        return [
            ("caption", [{"type": "bold", "offset": 0, "length": 7}]),
            ("more detail", []),
        ]

    async def fake_multipart(method, data, files):
        multipart_calls.append((method, data, files))
        return {"result": {"message_id": 9}}

    async def fake_api(method, payload=None):
        api_calls.append((method, payload or {}))
        return {"ok": True}

    monkeypatch.setattr("app.telegram_bot._markdown_chunks", fake_chunks)
    service._api_multipart = fake_multipart
    service._api = fake_api
    keyboard = {"inline_keyboard": [[{"text": "✅ Useful", "callback_data": "aud:d:useful"}]]}
    result = await service.send_photo(b"png", "image/png", "**caption**", 123, reply_markup=keyboard)
    assert result["result"]["message_id"] == 9
    assert json.loads(multipart_calls[0][1]["caption_entities"])[0]["type"] == "bold"
    assert json.loads(multipart_calls[0][1]["reply_markup"]) == keyboard
    assert api_calls[0][0] == "sendMessage"
    assert api_calls[0][1]["text"] == "more detail"


@pytest.mark.asyncio
async def test_event_loop_marks_failure_as_critical_and_recovery_as_routine(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    async def handler(command, args):
        return "monitor"

    async def stream():
        yield 'event: refresh\ndata: {"reason":"processing_failed"}\n\n'
        yield 'event: refresh\ndata: {"reason":"stage2b_pi5_endpoint_circuit_closed"}\n\n'

    service = TelegramBotService(lambda: _Config(), handler, event_stream_factory=stream)
    sent = []

    async def fake_send(text, chat_id=None, **kwargs):
        sent.append(text)
        return {}

    service.send = fake_send
    await service._event_loop()
    assert any("🔴 **ALERT**" in text and "Conversion failed" in text for text in sent)
    assert any("🟢" in text and "Pi5 verifier circuit recovered" in text for text in sent)
    assert all("Marine Pipeline Studio" not in text for text in sent)


def test_requirements_pin_telegramify_markdown():
    from pathlib import Path
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
    assert "telegramify-markdown==1.2.0" in requirements
