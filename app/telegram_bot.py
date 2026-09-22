from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Awaitable, Callable, Any

import httpx


class TelegramBotService:
    """Optional Telegram monitoring + human-audit transport.

    The bot never reads/writes SQLite, correction ledgers, or pipeline files
    directly. Monitoring commands are delegated to ``command_handler`` and
    human audit decisions are delegated to ``audit_handler`` so the application
    service layer remains authoritative for every mutation.
    """

    def __init__(
        self,
        config_getter: Callable[[], Any],
        command_handler: Callable[[str, list[str]], Awaitable[str]],
        event_stream_factory: Callable[[], Any] | None = None,
        audit_handler: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._config_getter = config_getter
        self._command_handler = command_handler
        self._event_stream_factory = event_stream_factory
        self._audit_handler = audit_handler
        self._task: asyncio.Task | None = None
        self._event_task: asyncio.Task | None = None
        self._offset = 0
        self._client: httpx.AsyncClient | None = None
        self._audit_sessions: dict[int, dict[str, Any]] = {}

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
        self._audit_sessions.clear()
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

    async def _api_multipart(self, method: str, data: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> dict:
        _, token, _, _, _ = self._settings()
        if not token or not self._client:
            return {}
        response = await self._client.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def send(self, text: str, chat_id: int | None = None, *, reply_markup: dict | None = None) -> dict:
        _, _, chats, _, _ = self._settings()
        targets = [chat_id] if chat_id is not None else sorted(chats)
        last: dict = {}
        for target in targets:
            try:
                payload: dict[str, Any] = {"chat_id": int(target), "text": str(text)[:3900]}
                if reply_markup:
                    payload["reply_markup"] = reply_markup
                last = await self._api("sendMessage", payload)
            except (httpx.HTTPError, ValueError):
                pass
        return last

    async def send_photo(self, image: bytes, mime_type: str, caption: str, chat_id: int, *, reply_markup: dict | None = None) -> dict:
        extension = ".jpg" if str(mime_type).lower() in {"image/jpeg", "image/jpg"} else ".png"
        data = {"chat_id": str(int(chat_id)), "caption": str(caption)[:1024]}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup, separators=(",", ":"))
        try:
            return await self._api_multipart(
                "sendPhoto",
                data,
                {"photo": (f"audit{extension}", bytes(image), str(mime_type or "image/png"))},
            )
        except (httpx.HTTPError, ValueError):
            return await self.send(caption, chat_id, reply_markup=reply_markup)

    @staticmethod
    def _audit_command(command: str, args: list[str]) -> str | None:
        compact = command.replace("_", "")
        direct = {
            "/textaudit": "text",
            "/visionaudit": "vision",
            "/visualaudit": "vision",
            "/artifactaudit": "artifact",
            "/articleaudit": "artifact",  # tolerate the common typo/alias
        }
        if compact in direct:
            return direct[compact]
        if args and str(args[0]).strip().lower() == "audit":
            return {
                "/text": "text",
                "/vision": "vision",
                "/visual": "vision",
                "/artifact": "artifact",
                "/article": "artifact",
            }.get(command)
        return None

    @staticmethod
    def _audit_keyboard(options: list[dict[str, str]]) -> dict:
        rows = [[{"text": str(option["label"]), "callback_data": f"aud:d:{option['value']}"}] for option in options]
        rows.append([{"text": "⏹ Stop verify audit", "callback_data": "aud:s"}])
        return {"inline_keyboard": rows}

    async def _poll_loop(self) -> None:
        while True:
            try:
                data = await self._api(
                    "getUpdates",
                    {"offset": self._offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]},
                )
                for update in data.get("result") or []:
                    self._offset = max(self._offset, int(update.get("update_id") or 0) + 1)
                    if update.get("callback_query"):
                        await self._handle_callback(update.get("callback_query") or {})
                        continue
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
        args = parts[1:]

        if command in {"/stopaudit", "/stopverifyaudit"}:
            await self._stop_audit(chat_id, announce=True)
            return

        audit_type = self._audit_command(command, args)
        if audit_type:
            await self._start_audit(chat_id, audit_type)
            return

        try:
            reply = await self._command_handler(command, args)
        except Exception as exc:  # command boundary: return a safe error, keep bot alive
            reply = f"Command failed: {type(exc).__name__}: {exc}"
        await self.send(reply, chat_id)

    async def _start_audit(self, chat_id: int, audit_type: str) -> None:
        if not self._audit_handler:
            await self.send("Human audit is not available in this build.", chat_id)
            return
        self._audit_sessions[chat_id] = {
            "type": audit_type,
            "reviewed": 0,
            "current": None,
            "current_message_id": None,
        }
        await self._send_next_audit(chat_id)

    async def _stop_audit(self, chat_id: int, *, announce: bool) -> None:
        session = self._audit_sessions.pop(chat_id, None)
        if announce:
            name = str((session or {}).get("type") or "verify").capitalize()
            reviewed = int((session or {}).get("reviewed") or 0)
            await self.send(
                f"[{datetime.now().astimezone().isoformat(timespec='seconds')}]\n\n"
                f"⏹ {name} audit stopped\n\nReviewed this session   {reviewed}\n\n"
                "No more audit images will be sent. Start again whenever you want.",
                chat_id,
            )

    async def _send_next_audit(self, chat_id: int) -> None:
        session = self._audit_sessions.get(chat_id)
        if not session or not self._audit_handler:
            return
        audit_type = str(session.get("type") or "")
        try:
            item = await self._audit_handler("next", audit_type, {"chat_id": chat_id})
        except Exception as exc:
            await self._stop_audit(chat_id, announce=False)
            await self.send(f"Audit failed: {type(exc).__name__}: {exc}", chat_id)
            return

        if not item or item.get("done"):
            reviewed = int(session.get("reviewed") or 0)
            remaining = int((item or {}).get("remaining") or 0)
            self._audit_sessions.pop(chat_id, None)
            title = {"text": "Text", "vision": "Vision", "artifact": "Artifact"}.get(audit_type, "Verifier")
            await self.send(
                f"[{datetime.now().astimezone().isoformat(timespec='seconds')}]\n\n"
                f"✅ {title} audit complete\n\nReviewed this session   {reviewed}\nRemaining                {remaining}",
                chat_id,
            )
            return

        session["current"] = item.get("key")
        keyboard = self._audit_keyboard(list(item.get("options") or []))
        caption = str(item.get("caption") or "Verifier audit")
        image = item.get("image")
        if isinstance(image, (bytes, bytearray)) and image:
            result = await self.send_photo(bytes(image), str(item.get("mime_type") or "image/png"), caption, chat_id, reply_markup=keyboard)
        else:
            result = await self.send(caption, chat_id, reply_markup=keyboard)
        message = result.get("result") if isinstance(result, dict) else None
        if isinstance(message, dict):
            session["current_message_id"] = int(message.get("message_id") or 0) or None

    async def _handle_callback(self, query: dict[str, Any]) -> None:
        _, _, chats, _, _ = self._settings()
        callback_id = str(query.get("id") or "")
        message = query.get("message") or {}
        chat_id = int((message.get("chat") or {}).get("id") or 0)
        data = str(query.get("data") or "")
        if chat_id not in chats or not data.startswith("aud:"):
            if callback_id:
                try:
                    await self._api("answerCallbackQuery", {"callback_query_id": callback_id})
                except httpx.HTTPError:
                    pass
            return

        session = self._audit_sessions.get(chat_id)
        if not session:
            if callback_id:
                await self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "This audit session has expired."})
            return

        message_id = int(message.get("message_id") or 0)
        expected_message_id = session.get("current_message_id")
        if expected_message_id and message_id and int(expected_message_id) != message_id:
            if callback_id:
                await self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "This audit card is no longer active."})
            return

        if data == "aud:s":
            if callback_id:
                await self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Audit stopped."})
            if message_id:
                try:
                    await self._api("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})
                except httpx.HTTPError:
                    pass
            await self._stop_audit(chat_id, announce=True)
            return

        if not data.startswith("aud:d:") or not self._audit_handler:
            return
        decision = data.split(":", 2)[2]
        current = session.get("current")
        if current is None:
            if callback_id:
                await self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "No active audit item."})
            return

        try:
            result = await self._audit_handler(
                "decide",
                str(session.get("type") or ""),
                {"chat_id": chat_id, "key": current, "decision": decision},
            )
        except Exception as exc:
            if callback_id:
                await self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": f"Decision failed: {type(exc).__name__}"})
            return

        if not result.get("ok"):
            if callback_id:
                await self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": str(result.get("error") or "Decision was not saved.")[:180]})
            return

        session["reviewed"] = int(session.get("reviewed") or 0) + 1
        session["current"] = None
        session["current_message_id"] = None
        if callback_id:
            await self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": str(result.get("message") or "Decision saved.")[:180]})
        if message_id:
            try:
                await self._api("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})
            except httpx.HTTPError:
                pass
        await self._send_next_audit(chat_id)

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
