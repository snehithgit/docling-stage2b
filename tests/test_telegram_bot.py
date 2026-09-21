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
