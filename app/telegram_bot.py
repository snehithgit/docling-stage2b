from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Awaitable, Callable, Any

import httpx


class TelegramBotService:
    """Optional thin Telegram control plane.

    It never reads/writes pipeline files or SQLite directly. Commands are handed
    to the application service callback, keeping all pipeline invariants in one
    place. Unknown chat IDs are ignored.
    """

    def __init__(self, config_getter: Callable[[], Any], command_handler: Callable[[str, list[str]], Awaitable[str]], event_stream_factory: Callable[[], Any] | None = None) -> None:
        self._config_getter = config_getter
        self._command_handler = command_handler
        self._event_stream_factory = event_stream_factory
        self._task: asyncio.Task | None = None
        self._event_task: asyncio.Task | None = None
        self._offset = 0
        self._client: httpx.AsyncClient | None = None

    def _settings(self) -> tuple[bool, str, set[int], bool, bool]:
        cfg = self._config_getter()
        token = os.environ.get(str(getattr(cfg, "telegram_bot_token_env", "TELEGRAM_BOT_TOKEN")), "").strip()
        chats = {int(x) for x in (getattr(cfg, "telegram_allowed_chat_ids", []) or []) if str(x).strip().lstrip("-").isdigit()}
        return bool(getattr(cfg, "telegram_enabled", False)), token, chats, bool(getattr(cfg, "telegram_notifications", True)), bool(getattr(cfg, "telegram_controls", True))

    async def start(self) -> None:
        enabled, token, chats, notifications, _ = self._settings()
        if not enabled or not token or not chats or self._task:
            return
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=10.0))
        self._task = asyncio.create_task(self._poll_loop(), name="telegram-bot-poll")
        if notifications and self._event_stream_factory:
            self._event_task = asyncio.create_task(self._event_loop(), name="telegram-bot-events")

    async def stop(self) -> None:
        for task in (self._task, self._event_task):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (self._task, self._event_task) if t), return_exceptions=True)
        self._task = self._event_task = None
        if self._client:
            await self._client.aclose()
        self._client = None

    async def _api(self, method: str, payload: dict | None = None) -> dict:
        _, token, _, _, _ = self._settings()
        if not token or not self._client:
            return {}
        response = await self._client.post(f"https://api.telegram.org/bot{token}/{method}", json=payload or {})
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    async def send(self, text: str, chat_id: int | None = None) -> None:
        _, _, chats, _, _ = self._settings()
        targets = [chat_id] if chat_id is not None else sorted(chats)
        for target in targets:
            try:
                await self._api("sendMessage", {"chat_id": int(target), "text": str(text)[:3900]})
            except (httpx.HTTPError, ValueError):
                pass

    async def _poll_loop(self) -> None:
        while True:
            try:
                data = await self._api("getUpdates", {"offset": self._offset, "timeout": 25, "allowed_updates": ["message"]})
                for update in data.get("result") or []:
                    self._offset = max(self._offset, int(update.get("update_id") or 0) + 1)
                    message = update.get("message") or {}
                    chat_id = int((message.get("chat") or {}).get("id") or 0)
                    text = str(message.get("text") or "").strip()
                    await self._handle(chat_id, text)
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, ValueError, TypeError):
                await asyncio.sleep(5)

    async def _handle(self, chat_id: int, text: str) -> None:
        _, _, chats, _, _ = self._settings()
        if chat_id not in chats or not text.startswith("/"):
            return
        parts = text.split()
        command = parts[0].split("@", 1)[0].lower()
        try:
            reply = await self._command_handler(command, parts[1:])
        except Exception as exc:  # command boundary: return a safe error, keep bot alive
            reply = f"Command failed: {type(exc).__name__}: {exc}"
        await self.send(reply, chat_id)

    async def _event_loop(self) -> None:
        if not self._event_stream_factory:
            return
        important = {
            "stage2b_pi5_endpoint_circuit_open", "stage2b_pi5_endpoint_circuit_closed", "stage2b_oneplus_endpoint_circuit_open", "stage2b_oneplus_endpoint_circuit_closed",
            "stage2b_artifact_sweep_released", "stage3_chunking_completed",
            "verifier_audit_decision", "verifier_audit_bypass_updated",
            "pipeline_retrieval_refresh_completed", "stage2c_human_correction",
        }
        async for frame in self._event_stream_factory():
            if not isinstance(frame, str) or '"reason":' not in frame:
                continue
            reason = next((r for r in important if f'"{r}"' in frame), None)
            if reason:
                timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
                label = reason.replace("_", " ").capitalize()
                await self.send(
                    f"[{timestamp}]\n\n⚓ Marine Pipeline Studio\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\nUPDATE\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"{label}\n\nUse /status for the current pipeline state."
                )
