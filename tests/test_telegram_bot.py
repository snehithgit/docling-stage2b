import pytest

from app.telegram_bot import TelegramBotService


class _Config:
    telegram_enabled = True
    telegram_bot_token_env = "TELEGRAM_BOT_TOKEN"
    telegram_allowed_chat_ids = [123]
    telegram_notifications = True
    telegram_controls = False


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
