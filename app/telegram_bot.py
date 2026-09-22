from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Awaitable, Callable, Any

import httpx


def _utf16_len(value: str) -> int:
    return len(str(value).encode("utf-16-le")) // 2


def _plain_text_chunks(value: str, max_utf16_len: int) -> list[tuple[str, list[dict[str, Any]]]]:
    """Dependency-missing safety fallback; production installs telegramify-markdown."""
    remaining = str(value or "")
    chunks: list[tuple[str, list[dict[str, Any]]]] = []
    while remaining:
        if _utf16_len(remaining) <= max_utf16_len:
            chunks.append((remaining, []))
            break
        lo, hi = 1, len(remaining)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _utf16_len(remaining[:mid]) <= max_utf16_len:
                lo = mid
            else:
                hi = mid - 1
        cut = max(1, lo)
        newline = remaining.rfind("\n", 0, cut + 1)
        if newline >= max(1, cut // 2):
            cut = newline + 1
        chunk = remaining[:cut].rstrip()
        if chunk:
            chunks.append((chunk, []))
        remaining = remaining[cut:].lstrip("\n")
    return chunks or [("", [])]




def _markdown_code(value: object, limit: int = 300) -> str:
    text = " ".join(str(value or "—").split())
    if len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "…"
    longest = 0
    run = 0
    for char in text:
        if char == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    fence = "`" * max(1, longest + 1)
    return f"{fence}{text}{fence}"

def _markdown_chunks(value: str, max_utf16_len: int = 4000) -> list[tuple[str, list[dict[str, Any]]]]:
    """Convert Markdown source to Telegram text/entities and split at safe boundaries."""
    source = str(value or "")
    try:
        from telegramify_markdown import convert, split_entities

        text, entities = convert(source, latex_escape=False)
        chunks = split_entities(text, entities, max_utf16_len=max_utf16_len)
        return [
            (chunk_text, [entity.to_dict() for entity in chunk_entities])
            for chunk_text, chunk_entities in chunks
        ] or [("", [])]
    except ImportError:
        return _plain_text_chunks(source, max_utf16_len)
    except (TypeError, ValueError, RuntimeError):
        # Formatting must never prevent a health alert or audit decision from
        # reaching Telegram. Fall back to literal text if conversion rejects a
        # malformed Markdown edge case.
        return _plain_text_chunks(source, max_utf16_len)


def _telegramify_contract_probe() -> tuple[bool, str]:
    """Exercise the installed formatter API with representative mobile content."""
    try:
        from telegramify_markdown import convert, split_entities

        sample = (
            "🔴 **ALERT**\n"
            "`GE_9FA-VoltageRegulator (v2).pdf`\n\n"
            "```\nPIPELINE\nBooks  12\nFailed  2\n```"
        )
        text, entities = convert(sample, latex_escape=False)
        chunks = split_entities(text, entities, max_utf16_len=48)
        if not text or not entities or not chunks:
            return False, "formatter returned empty text/entities/chunks"
        serialized = []
        for chunk_text, chunk_entities in chunks:
            if not chunk_text or _utf16_len(chunk_text) > 48:
                return False, "formatter split exceeded Telegram UTF-16 limit"
            serialized.extend(entity.to_dict() for entity in chunk_entities)
        kinds = {str(item.get("type") or "") for item in serialized}
        if not ({"bold", "code"} & kinds):
            return False, "formatter did not preserve expected entities"
        return True, "telegramify-markdown convert/split_entities contract OK"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _event_payload(frame: str) -> dict[str, Any]:
    """Extract the JSON data object from one SSE frame."""
    if not isinstance(frame, str):
        return {}
    for line in frame.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip() or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _event_context_markdown(payload: dict[str, Any]) -> str:
    """Render only the small amount of context needed to act on a push alert."""
    lines: list[str] = []
    filename = str(payload.get("filename") or "").strip()
    if filename:
        lines.append(f"📘 Book: {_markdown_code(filename, 180)}")
    stage = str(payload.get("stage") or "").strip()
    if stage:
        lines.append(f"🧭 Stage: {_markdown_code(stage, 80)}")
    route_id = str(payload.get("route_id") or "").strip()
    if route_id and not filename:
        lines.append(f"🔎 Route: {_markdown_code(route_id, 120)}")
    error = str(payload.get("error") or "").strip()
    if error:
        lines.append(f"⚠️ Error: {_markdown_code(error, 240)}")
    return "\n".join(lines)


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
        self._formatter_ok = False
        self._formatter_detail = "not checked"

    def _settings(self) -> tuple[bool, str, set[int], bool, bool]:
        cfg = self._config_getter()
        token = os.environ.get(str(getattr(cfg, "telegram_bot_token_env", "TELEGRAM_BOT_TOKEN")), "").strip()
        chats = {int(x) for x in (getattr(cfg, "telegram_allowed_chat_ids", []) or []) if str(x).strip().lstrip("-").isdigit()}
        return bool(getattr(cfg, "telegram_enabled", False)), token, chats, bool(getattr(cfg, "telegram_notifications", True)), bool(getattr(cfg, "telegram_controls", True))

    @staticmethod
    def _command_menu(controls: bool) -> list[dict[str, str]]:
        commands = [
            {"command": "start", "description": "⚓ Open Docling Auto-Convert menu"},
            {"command": "status", "description": "📊 Pipeline overview and attention items"},
            {"command": "books", "description": "📚 Book progress, worst first"},
            {"command": "workers", "description": "⚙️ Pi5 and OnePlus health"},
            {"command": "audit", "description": "🔎 Human-review backlog"},
            {"command": "errors", "description": "🚨 Failures and blockers"},
            {"command": "help", "description": "ℹ️ Commands and usage"},
        ]
        if controls:
            commands.extend([
                {"command": "textaudit", "description": "📝 Review text corrections"},
                {"command": "visionaudit", "description": "🖼 Review vision evidence"},
                {"command": "artifactaudit", "description": "🔬 Review artifact sweep"},
                {"command": "stopaudit", "description": "⏹ Stop active audit session"},
            ])
        return commands

    async def _install_command_menu(self, controls: bool) -> None:
        """Publish the native Telegram slash-command menu without blocking bot startup."""
        try:
            await self._api("setMyCommands", {"commands": self._command_menu(controls)})
            await self._api("setChatMenuButton", {"menu_button": {"type": "commands"}})
        except (httpx.HTTPError, ValueError, TypeError):
            # A menu registration failure must not disable monitoring/auditing.
            pass

    async def start(self) -> None:
        enabled, token, chats, notifications, controls = self._settings()
        if not enabled or not token or not chats or self._task:
            return
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=10.0))
        self._formatter_ok, self._formatter_detail = _telegramify_contract_probe()
        await self._install_command_menu(controls)
        self._task = asyncio.create_task(self._poll_loop(), name="telegram-bot-poll")
        if notifications and self._event_stream_factory:
            self._event_task = asyncio.create_task(self._event_loop(), name="telegram-bot-events")
        if not self._formatter_ok:
            warning = (
                "🟡 Telegram formatter degraded\n"
                f"{self._formatter_detail}\n"
                "Messages will fall back to plain text until the dependency is fixed."
            )
            for chat_id in sorted(chats):
                await self._api("sendMessage", {"chat_id": int(chat_id), "text": warning})

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
        chunks = _markdown_chunks(str(text), max_utf16_len=4000)
        last: dict = {}
        for target in targets:
            for index, (chunk_text, entities) in enumerate(chunks):
                if not chunk_text:
                    continue
                try:
                    payload: dict[str, Any] = {"chat_id": int(target), "text": chunk_text}
                    if entities:
                        payload["entities"] = entities
                    if reply_markup and index == len(chunks) - 1:
                        payload["reply_markup"] = reply_markup
                    last = await self._api("sendMessage", payload)
                except (httpx.HTTPError, ValueError):
                    pass
        return last

    async def send_photo(self, image: bytes, mime_type: str, caption: str, chat_id: int, *, reply_markup: dict | None = None) -> dict:
        extension = ".jpg" if str(mime_type).lower() in {"image/jpeg", "image/jpg"} else ".png"
        caption_chunks = _markdown_chunks(str(caption), max_utf16_len=1024)
        caption_text, caption_entities = caption_chunks[0]
        data = {"chat_id": str(int(chat_id)), "caption": caption_text}
        if caption_entities:
            data["caption_entities"] = json.dumps(caption_entities, separators=(",", ":"), ensure_ascii=False)
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup, separators=(",", ":"))
        try:
            result = await self._api_multipart(
                "sendPhoto",
                data,
                {"photo": (f"audit{extension}", bytes(image), str(mime_type or "image/png"))},
            )
            # Captions cannot span messages. If a caption had to be split, send
            # the remaining detail as formatted follow-up messages without the
            # decision keyboard; the decision remains attached to the image.
            for extra_text, extra_entities in caption_chunks[1:]:
                payload: dict[str, Any] = {"chat_id": int(chat_id), "text": extra_text}
                if extra_entities:
                    payload["entities"] = extra_entities
                await self._api("sendMessage", payload)
            return result
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
        decision_row = [
            {"text": str(option["label"]), "callback_data": f"aud:d:{option['value']}"}
            for option in options
        ]
        rows = [decision_row] if decision_row else []
        rows.append([{"text": "⏹ Stop review", "callback_data": "aud:s"}])
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
        _, _, chats, _, controls = self._settings()
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
            if not controls:
                await self.send(
                    "🔒 **Telegram audit controls are disabled**\n\n"
                    "Use the Docling Auto-Convert web review queue, or enable `telegram_controls` in `config.yaml`.",
                    chat_id,
                )
                return
            await self._start_audit(chat_id, audit_type)
            return

        try:
            reply = await self._command_handler(command, args)
        except Exception as exc:  # command boundary: return a safe error, keep bot alive
            reply = f"🔴 **Command failed**\n\n{_markdown_code(type(exc).__name__, 80)}: {_markdown_code(exc, 300)}"
        await self.send(reply, chat_id)

    async def _start_audit(self, chat_id: int, audit_type: str) -> None:
        if not self._audit_handler:
            await self.send("🟡 **Human audit is unavailable in this build.**", chat_id)
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
                f"⏹ **{name} review stopped**\n"
                f"Reviewed this session: `{reviewed}`\n\n"
                "Use the Menu button when you want to continue.",
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
            await self.send(f"🔴 **Audit failed**\n\n{_markdown_code(type(exc).__name__, 80)}: {_markdown_code(exc, 300)}", chat_id)
            return

        if not item or item.get("done"):
            reviewed = int(session.get("reviewed") or 0)
            remaining = int((item or {}).get("remaining") or 0)
            self._audit_sessions.pop(chat_id, None)
            title = {"text": "Text", "vision": "Vision", "artifact": "Artifact"}.get(audit_type, "Verifier")
            await self.send(
                f"✅ **{title} review complete**\n"
                f"Reviewed this session: `{reviewed}` · Remaining: `{remaining}`",
                chat_id,
            )
            return

        session["current"] = item.get("key")
        keyboard = self._audit_keyboard(list(item.get("options") or []))
        caption = str(item.get("caption") or "🔎 **Verifier audit**")
        image = item.get("image")
        if isinstance(image, (bytes, bytearray)) and image:
            result = await self.send_photo(bytes(image), str(item.get("mime_type") or "image/png"), caption, chat_id, reply_markup=keyboard)
        else:
            result = await self.send(caption, chat_id, reply_markup=keyboard)
        message = result.get("result") if isinstance(result, dict) else None
        if isinstance(message, dict):
            session["current_message_id"] = int(message.get("message_id") or 0) or None

    async def _handle_callback(self, query: dict[str, Any]) -> None:
        _, _, chats, _, controls = self._settings()
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

        if data.startswith("aud:d:") and not controls:
            if callback_id:
                await self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Telegram audit controls are disabled."})
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
        critical = {
            "processing_failed": "Conversion failed",
            "postprocess_failed": "Stage 2A post-process failed",
            "postprocess_error": "Stage 2A worker error",
            "stage3_chunking_failed": "Stage 3 chunking failed",
            "stage2b_pi5_failed": "Text verifier job failed",
            "stage2b_oneplus_failed": "Vision verifier job failed",
            "stage2b_pi5_worker_error": "Text verifier worker error",
            "stage2b_oneplus_worker_error": "Vision verifier worker error",
            "worker_error": "Conversion worker error",
            "stage2c_ledger_error": "Correction ledger error",
            "pipeline_sequence_error": "Pipeline sequence error",
            "stage2b_cloud_quota_paused": "Cloud verifier quota paused",
            "stage2b_pi5_endpoint_circuit_open": "Pi5 verifier circuit open",
            "stage2b_oneplus_endpoint_circuit_open": "OnePlus verifier circuit open",
        }
        routine = {
            "stage2b_pi5_endpoint_circuit_closed": "Pi5 verifier circuit recovered",
            "stage2b_oneplus_endpoint_circuit_closed": "OnePlus verifier circuit recovered",
            "stage2b_artifact_sweep_released": "Artifact verification sweep released",
            "stage3_chunking_completed": "Stage 3 chunking completed",
            "verifier_audit_decision": "Human verifier decision saved",
            "verifier_audit_bypass_updated": "Audit testing bypass updated",
            "pipeline_retrieval_refresh_completed": "Retrieval index refreshed",
            "stage2c_human_correction": "Human correction saved",
        }
        known = set(critical) | set(routine)
        async for frame in self._event_stream_factory():
            payload = _event_payload(frame)
            reason = str(payload.get("reason") or "")
            if reason not in known:
                continue
            timestamp = datetime.now().astimezone().strftime("%d %b · %H:%M %Z")
            context = _event_context_markdown(payload)
            if reason in critical:
                lines = [f"🔴 **{critical[reason]}**", f"_{timestamp}_"]
                if context:
                    lines += ["", context]
                lines += ["", "🚨 `/errors` · 📊 `/status`"]
                await self.send("\n".join(lines))
            else:
                lines = [f"🟢 **{routine[reason]}**", f"_{timestamp}_"]
                if context:
                    lines += ["", context]
                lines += ["", "📊 `/status`"]
                await self.send("\n".join(lines))
