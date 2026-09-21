from __future__ import annotations

import asyncio
import base64
import inspect
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .groq_quota import CloudQuotaPausedError, GroqQuotaGuard


@dataclass
class EndpointHealth:
    reachable: bool
    model: str | None = None
    detail: str = ""


ProgressCallback = Callable[[dict[str, Any]], Any]


class GroqStructuredVerifier:
    """Minimal Groq/OpenAI-compatible text client with strict JSON Schema.

    Secrets are supplied only by environment.  This client is text-only and is
    intentionally separate from the local OnePlus vision client.
    """

    supports_strict_json_schema = True

    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout_seconds: int = 120,
        reasoning_effort: str = "low",
        quota_guard: GroqQuotaGuard | None = None,
        usage_context: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.default_model = default_model
        self.reasoning_effort = reasoning_effort
        self.quota_guard = quota_guard
        self.usage_context = dict(usage_context or {})
        self.timeout = httpx.Timeout(connect=10.0, read=float(timeout_seconds), write=30.0, pool=10.0)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ValueError("Groq API key is not configured")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def health(self) -> EndpointHealth:
        if not self.api_key:
            return EndpointHealth(False, model=self.default_model, detail="GROQ_API_KEY not configured")
        try:
            if self.quota_guard is not None:
                await self.quota_guard.before_request(0)
            async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers()) as client:
                started = time.monotonic()
                response = await client.get(f"{self.base_url}/models/{self.default_model}")
                if self.quota_guard is not None:
                    await self.quota_guard.record_response(
                        response, model=self.default_model, usage_tokens=0, call_kind="health",
                        latency_seconds=time.monotonic() - started, context={**self.usage_context, "purpose": "health"},
                    )
                if response.status_code == 429:
                    snap = await self.quota_guard.snapshot() if self.quota_guard is not None else {}
                    raise CloudQuotaPausedError("Groq rate limit active", snap)
                if not response.is_success:
                    return EndpointHealth(False, model=self.default_model, detail=f"models HTTP {response.status_code}")
                body = response.json() if response.content else {}
                return EndpointHealth(True, model=str(body.get("id") or self.default_model), detail="Groq ready · strict JSON · quota guard active")
        except CloudQuotaPausedError as exc:
            return EndpointHealth(False, model=self.default_model, detail=str(exc))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return EndpointHealth(False, model=self.default_model, detail=str(exc))

    async def chat_text(
        self,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 220,
        *,
        schema_name: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "temperature": 0,
            "max_completion_tokens": int(max_tokens),
            "stream": False,
            "reasoning_effort": self.reasoning_effort,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name or "structured_response",
                    "strict": True,
                    "schema": schema,
                },
            }
        else:
            payload["response_format"] = {"type": "json_object"}
        # Conservative preflight estimate: UTF-8 technical text averages more
        # than four characters/token in many cases, so use 3 chars/token plus
        # the full configured completion ceiling. It is intentionally biased
        # toward stopping early on the free tier.
        estimated_tokens = max(1, (len(system) + len(user) + 2) // 3) + int(max_tokens)
        if self.quota_guard is not None:
            await self.quota_guard.before_request(estimated_tokens)
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers()) as client:
            started = time.monotonic()
            response = await client.post(f"{self.base_url}/chat/completions", json=payload)
            latency = time.monotonic() - started
            body: dict[str, Any] = {}
            if response.content:
                try:
                    candidate = response.json()
                    if isinstance(candidate, dict):
                        body = candidate
                except (ValueError, TypeError):
                    body = {}
            usage = body.get("usage") or {} if isinstance(body, dict) else {}
            try:
                input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                usage_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
            except (TypeError, ValueError, AttributeError):
                input_tokens = output_tokens = usage_tokens = 0
            error = body.get("error") if isinstance(body, dict) else None
            error_code = None
            if isinstance(error, dict):
                error_code = error.get("code") or error.get("type") or error.get("message")
            if self.quota_guard is not None:
                await self.quota_guard.record_response(
                    response, model=str(payload.get("model") or self.default_model), usage_tokens=usage_tokens,
                    input_tokens=input_tokens, output_tokens=output_tokens, call_kind="text",
                    latency_seconds=latency, request_id=str(body.get("id") or "") or None,
                    error_code=str(error_code or "")[:200] or None, context=self.usage_context,
                )
            if response.status_code == 429:
                snap = await self.quota_guard.snapshot() if self.quota_guard is not None else {}
                raise CloudQuotaPausedError(
                    str((snap or {}).get("message") or "Groq rate limit active; cloud verification paused"), snap
                )
            response.raise_for_status()
            return body or response.json()


class GroqVisionVerifier:
    """Groq image verifier using the same free-tier quota/usage guard.

    The historical Stage 2B route key remains ``oneplus`` in storage, but this
    client is selected only when the user explicitly chooses Cloud Vision.
    It never falls back to the phone.
    """

    supports_strict_json_schema = True

    VISION_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {"type": "string", "enum": ["TECHNICAL_USEFUL", "DECORATIVE_OR_LOW_VALUE", "UNCERTAIN"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "visible_text": {"type": "array", "items": {"type": "string"}},
            "visible_objects": {"type": "array", "items": {"type": "string"}},
            "diagram_category": {"type": "string"},
            "summary": {"type": "string"},
            "unresolved": {"type": "boolean"},
            "unresolved_reason": {"type": "string"},
        },
        "required": ["verdict", "confidence", "visible_text", "visible_objects", "diagram_category", "summary", "unresolved", "unresolved_reason"],
    }

    def __init__(
        self, base_url: str, api_key: str, default_model: str, timeout_seconds: int = 120,
        quota_guard: GroqQuotaGuard | None = None, usage_context: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.default_model = default_model
        self.quota_guard = quota_guard
        self.usage_context = dict(usage_context or {})
        self.timeout = httpx.Timeout(connect=10.0, read=float(timeout_seconds), write=60.0, pool=10.0)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ValueError("Groq API key is not configured")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def health(self) -> EndpointHealth:
        if not self.api_key:
            return EndpointHealth(False, model=self.default_model, detail="GROQ_API_KEY not configured")
        # Do not spend a second health call when cloud text and cloud vision share
        # the same Groq account. Status can safely report configured/selected.
        return EndpointHealth(True, model=self.default_model, detail="Groq Vision configured · strict JSON")

    async def inspect_image_stream(
        self, image_bytes: bytes, prompt: str, mime_type: str = "image/png", model: str | None = None,
        max_tokens: int = 220, *, first_token_timeout_seconds: int = 120, idle_timeout_seconds: int = 120,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        del first_token_timeout_seconds, idle_timeout_seconds
        if not self.api_key:
            raise ValueError("Groq API key is not configured")
        selected_model = str(model or self.default_model)
        b64 = base64.b64encode(image_bytes).decode("ascii")
        direct_transcription_mode = "exactly two keys: status and corrected_text" in prompt
        crosscheck_mode = "exactly two keys: verdict and corrected_text" in prompt
        if direct_transcription_mode:
            response_schema = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {"type": "string", "enum": ["READABLE", "UNREADABLE"]},
                    "corrected_text": {"type": "string"},
                },
                "required": ["status", "corrected_text"],
            }
        elif crosscheck_mode:
            response_schema = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "verdict": {"type": "string", "enum": ["AGREES", "DISAGREES", "UNREADABLE"]},
                    "corrected_text": {"type": "string"},
                },
                "required": ["verdict", "corrected_text"],
            }
        else:
            response_schema = self.VISION_SCHEMA
        payload = {
            "model": selected_model,
            "temperature": 0,
            "max_completion_tokens": int(max_tokens),
            "stream": False,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                ],
            }],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "vision_direct_transcription" if direct_transcription_mode else ("vision_text_crosscheck" if crosscheck_mode else "vision_verification"),
                    "strict": True, "schema": response_schema,
                },
            },
        }
        # Qwen vision image input is conservatively budgeted as 2300 tokens plus
        # prompt/completion allowance. This is intentionally an overestimate for
        # the free-tier pre-limit guard.
        estimated_tokens = 2300 + max(1, (len(prompt) + 2) // 3) + int(max_tokens)
        if self.quota_guard is not None:
            await self.quota_guard.before_request(estimated_tokens)
        if on_progress is not None:
            result = on_progress({"phase": "waiting_first_output", "chunk_count": 0, "content_chunk_count": 0})
            if inspect.isawaitable(result):
                await result
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers()) as client:
            started = time.monotonic()
            response = await client.post(f"{self.base_url}/chat/completions", json=payload)
            latency = time.monotonic() - started
            body: dict[str, Any] = {}
            if response.content:
                try:
                    candidate = response.json()
                    if isinstance(candidate, dict):
                        body = candidate
                except (ValueError, TypeError):
                    body = {}
            usage = body.get("usage") or {} if isinstance(body, dict) else {}
            try:
                input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                usage_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
            except (TypeError, ValueError, AttributeError):
                input_tokens = output_tokens = usage_tokens = 0
            error = body.get("error") if isinstance(body, dict) else None
            error_code = None
            if isinstance(error, dict):
                error_code = error.get("code") or error.get("type") or error.get("message")
            if self.quota_guard is not None:
                purpose = str((self.usage_context or {}).get("purpose") or "")
                if purpose == "text_source_reconstruction":
                    call_kind = "text_reconstruction"
                elif crosscheck_mode:
                    call_kind = "vision_crosscheck"
                else:
                    call_kind = "vision"
                await self.quota_guard.record_response(
                    response, model=selected_model, usage_tokens=usage_tokens, input_tokens=input_tokens,
                    output_tokens=output_tokens, call_kind=call_kind, latency_seconds=latency,
                    request_id=str(body.get("id") or "") or None, error_code=str(error_code or "")[:200] or None,
                    context=self.usage_context,
                )
            if response.status_code == 429:
                snap = await self.quota_guard.snapshot() if self.quota_guard is not None else {}
                raise CloudQuotaPausedError(str((snap or {}).get("message") or "Groq rate limit active"), snap)
            response.raise_for_status()
            choices = body.get("choices") or []
            finish = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None
            body["_stream"] = {
                "finish_reason": finish, "done_received": True, "content_chunk_count": 1,
                "first_content_seconds": round(latency, 4), "completion_tokens": output_tokens,
                "provider": "groq", "streaming": False,
            }
            if on_progress is not None:
                progress = on_progress({
                    "phase": "complete", "chunk_count": 1, "content_chunk_count": 1,
                    "first_content_seconds": latency, "finish_reason": finish, "done_received": True,
                    "completion_tokens": output_tokens,
                })
                if inspect.isawaitable(progress):
                    await progress
            return body

    async def inspect_image(self, *args, **kwargs) -> dict[str, Any]:
        return await self.inspect_image_stream(*args, **kwargs)


class OpenAICompatibleVerifier:
    """Small wrapper for the Pi5 and OnePlus llama.cpp servers.

    Text verification keeps the normal non-streaming OpenAI-compatible API.
    OnePlus vision can use SSE streaming so a slow phone remains observable and
    a request is considered finished only when llama.cpp emits finish_reason or
    the OpenAI-compatible ``[DONE]`` sentinel.
    """

    def __init__(self, base_url: str, timeout_seconds: int = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(connect=10.0, read=float(timeout_seconds), write=30.0, pool=10.0)

    async def health(self) -> EndpointHealth:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/health")
                if not response.is_success:
                    return EndpointHealth(False, detail=f"health HTTP {response.status_code}")
                model = await self._discover_model(client)
                return EndpointHealth(True, model=model, detail="ok")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return EndpointHealth(False, detail=str(exc))

    async def _discover_model(self, client: httpx.AsyncClient) -> str | None:
        response = await client.get(f"{self.base_url}/v1/models")
        if not response.is_success:
            return None
        body = response.json()
        data = body.get("data") or [] if isinstance(body, dict) else []
        if data and isinstance(data[0], dict):
            return data[0].get("id")
        models = body.get("models") or [] if isinstance(body, dict) else []
        if models and isinstance(models[0], dict):
            return models[0].get("model") or models[0].get("name")
        return None

    async def verify_text(self, source: str, candidate: str, model: str | None = None) -> dict[str, Any]:
        """Strict source-vs-candidate verification validated in the Pi5 benchmark."""
        system = (
            "You are a strict engineering-document verifier. Compare CANDIDATE only "
            "against SOURCE EVIDENCE. Return JSON only with verdict SUPPORTED, "
            "CONTRADICTED, or NOT_ENOUGH_EVIDENCE. Never use outside knowledge; "
            "preserve numbers, units, inequalities, tolerances and identifiers."
        )
        payload: dict[str, Any] = {
            "temperature": 0,
            "max_tokens": 120,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"SOURCE EVIDENCE:\n{source}\n\nCANDIDATE:\n{candidate}"},
            ],
        }
        if model:
            payload["model"] = model
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/v1/chat/completions", json=payload)
            response.raise_for_status()
            return response.json()

    async def chat_text(
        self,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 160,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if model:
            payload["model"] = model
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/v1/chat/completions", json=payload)
            response.raise_for_status()
            return response.json()

    def _vision_payload(
        self,
        image_bytes: bytes,
        prompt: str,
        mime_type: str,
        model: str | None,
        max_tokens: int,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        payload: dict[str, Any] = {
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": stream,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                ],
            }],
        }
        if model:
            payload["model"] = model
        return payload

    async def inspect_image(
        self,
        image_bytes: bytes,
        prompt: str,
        mime_type: str = "image/png",
        model: str | None = None,
        max_tokens: int = 220,
    ) -> dict[str, Any]:
        """Compatibility non-streaming image call.

        Stage 2B OnePlus production calls use ``inspect_image_stream`` below.
        Keeping this method makes the client useful for tests and other callers.
        """
        payload = self._vision_payload(image_bytes, prompt, mime_type, model, max_tokens, stream=False)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/v1/chat/completions", json=payload)
            response.raise_for_status()
            return response.json()

    async def _emit_progress(self, callback: ProgressCallback | None, data: dict[str, Any]) -> None:
        if callback is None:
            return
        result = callback(data)
        if inspect.isawaitable(result):
            await result

    async def inspect_image_stream(
        self,
        image_bytes: bytes,
        prompt: str,
        mime_type: str = "image/png",
        model: str | None = None,
        max_tokens: int = 220,
        *,
        first_token_timeout_seconds: int = 1200,
        idle_timeout_seconds: int = 300,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Stream an OpenAI-compatible llama.cpp vision response.

        There are deliberately two inactivity rules:
        * before the first generated content delta, allow a long vision/prompt
          evaluation window (phone inference can be slow);
        * after generation starts, only fail if the stream goes completely idle
          for ``idle_timeout_seconds``.

        The returned object has the same ``choices[0].message.content`` shape as
        a non-streaming chat completion, plus ``_stream`` audit metadata.
        """
        first_token_timeout_seconds = max(30, int(first_token_timeout_seconds))
        idle_timeout_seconds = max(30, int(idle_timeout_seconds))
        payload = self._vision_payload(image_bytes, prompt, mime_type, model, max_tokens, stream=True)
        started = time.monotonic()
        content_parts: list[str] = []
        chunk_count = 0
        content_chunk_count = 0
        first_event_seconds: float | None = None
        first_content_seconds: float | None = None
        last_activity_seconds: float | None = None
        last_meaningful_monotonic: float | None = None
        finish_reason: str | None = None
        done_received = False
        final_usage: dict[str, Any] | None = None
        final_timings: dict[str, Any] | None = None
        response_model = model
        response_id: str | None = None
        response_created: Any = None

        # Disable httpx's body read timeout; the explicit per-stream timers
        # below are more meaningful for a slow phone than one fixed ReadTimeout.
        stream_timeout = httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0)
        async with httpx.AsyncClient(timeout=stream_timeout) as client:
            request = client.build_request("POST", f"{self.base_url}/v1/chat/completions", json=payload)
            try:
                async with asyncio.timeout(first_token_timeout_seconds):
                    response = await client.send(request, stream=True)
            except TimeoutError as exc:
                raise httpx.ReadTimeout(
                    f"No response headers from OnePlus within {first_token_timeout_seconds}s",
                    request=request,
                ) from exc

            try:
                response.raise_for_status()
                iterator = response.aiter_lines().__aiter__()
                while True:
                    now = time.monotonic()
                    if first_content_seconds is None:
                        remaining = first_token_timeout_seconds - (now - started)
                        if remaining <= 0:
                            raise httpx.ReadTimeout(
                                f"No generated content from OnePlus within {first_token_timeout_seconds}s",
                                request=request,
                            )
                        wait_seconds = remaining
                    else:
                        # Idle timeout is based on meaningful model output, not
                        # SSE comments/keep-alives. A server can keep a socket
                        # alive while generation is actually wedged.
                        anchor = last_meaningful_monotonic or (started + first_content_seconds)
                        remaining = idle_timeout_seconds - (now - anchor)
                        if remaining <= 0:
                            raise httpx.ReadTimeout(
                                f"OnePlus stream produced no model output for {idle_timeout_seconds}s",
                                request=request,
                            )
                        wait_seconds = remaining

                    try:
                        line = await asyncio.wait_for(iterator.__anext__(), timeout=wait_seconds)
                    except StopAsyncIteration:
                        break
                    except TimeoutError as exc:
                        phase = "first generated content" if first_content_seconds is None else "next stream event"
                        raise httpx.ReadTimeout(
                            f"Timed out waiting for {phase} from OnePlus after {int(wait_seconds)}s",
                            request=request,
                        ) from exc

                    # Blank lines delimit SSE events. Comments/keep-alives still
                    # prove the connection is alive, but do not count as tokens.
                    if not line:
                        continue
                    elapsed = time.monotonic() - started
                    last_activity_seconds = elapsed
                    if line.startswith(":"):
                        await self._emit_progress(on_progress, {
                            "phase": "waiting_first_output" if first_content_seconds is None else "streaming",
                            "chunk_count": chunk_count,
                            "content_chunk_count": content_chunk_count,
                            "first_content_seconds": first_content_seconds,
                            "last_activity_seconds": last_activity_seconds,
                            "finish_reason": finish_reason,
                            "done_received": done_received,
                        })
                        continue
                    if not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    if data == "[DONE]":
                        done_received = True
                        await self._emit_progress(on_progress, {
                            "phase": "complete",
                            "chunk_count": chunk_count,
                            "content_chunk_count": content_chunk_count,
                            "first_content_seconds": first_content_seconds,
                            "last_activity_seconds": last_activity_seconds,
                            "finish_reason": finish_reason,
                            "done_received": True,
                            "usage": final_usage,
                            "timings": final_timings,
                        })
                        break
                    if not data:
                        continue

                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise httpx.RemoteProtocolError(
                            f"OnePlus returned malformed SSE JSON: {data[:160]}", request=request
                        ) from exc
                    if not isinstance(event, dict):
                        continue

                    chunk_count += 1
                    if first_event_seconds is None:
                        first_event_seconds = elapsed
                    response_id = str(event.get("id") or response_id or "") or None
                    response_created = event.get("created", response_created)
                    response_model = event.get("model") or response_model
                    if isinstance(event.get("usage"), dict):
                        final_usage = event["usage"]
                    if isinstance(event.get("timings"), dict):
                        final_timings = event["timings"]

                    choices = event.get("choices") or []
                    if choices and isinstance(choices[0], dict):
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        piece: Any = delta.get("content") if isinstance(delta, dict) else None
                        if piece is None and isinstance(choice.get("text"), str):
                            piece = choice.get("text")
                        if isinstance(piece, list):
                            piece = "".join(
                                str(item.get("text") or "") if isinstance(item, dict) else str(item)
                                for item in piece
                            )
                        if piece:
                            piece = str(piece)
                            content_parts.append(piece)
                            content_chunk_count += 1
                            if first_content_seconds is None:
                                first_content_seconds = elapsed
                            last_meaningful_monotonic = time.monotonic()
                        if choice.get("finish_reason") is not None:
                            finish_reason = str(choice.get("finish_reason"))
                            last_meaningful_monotonic = time.monotonic()

                    completion_tokens = None
                    if isinstance(final_usage, dict):
                        completion_tokens = final_usage.get("completion_tokens")
                    if completion_tokens is None and isinstance(final_timings, dict):
                        completion_tokens = final_timings.get("predicted_n")
                    await self._emit_progress(on_progress, {
                        "phase": "streaming" if first_content_seconds is not None else "waiting_first_output",
                        "chunk_count": chunk_count,
                        "content_chunk_count": content_chunk_count,
                        "first_content_seconds": first_content_seconds,
                        "last_activity_seconds": last_activity_seconds,
                        "finish_reason": finish_reason,
                        "done_received": done_received,
                        "completion_tokens": completion_tokens,
                        "output_chars": sum(len(part) for part in content_parts),
                    })
                    if finish_reason is not None:
                        break
            finally:
                await response.aclose()

        total_seconds = time.monotonic() - started
        # Normal OpenAI streams terminate with finish_reason and [DONE]. Some
        # compatible servers omit [DONE], so finish_reason is also authoritative.
        if not done_received and finish_reason is None:
            raise httpx.RemoteProtocolError(
                "OnePlus stream ended without finish_reason or [DONE]", request=request
            )

        content = "".join(content_parts)
        completion_tokens = None
        prompt_tokens = None
        total_tokens = None
        if isinstance(final_usage, dict):
            completion_tokens = final_usage.get("completion_tokens")
            prompt_tokens = final_usage.get("prompt_tokens")
            total_tokens = final_usage.get("total_tokens")
        if completion_tokens is None and isinstance(final_timings, dict):
            completion_tokens = final_timings.get("predicted_n")
        if prompt_tokens is None and isinstance(final_timings, dict):
            prompt_tokens = final_timings.get("prompt_n")

        stream_meta = {
            "streamed": True,
            "protocol_complete": bool(done_received or finish_reason is not None),
            "done_received": done_received,
            "finish_reason": finish_reason,
            "truncated": finish_reason == "length",
            "chunk_count": chunk_count,
            "content_chunk_count": content_chunk_count,
            "first_event_seconds": round(first_event_seconds, 4) if first_event_seconds is not None else None,
            "first_content_seconds": round(first_content_seconds, 4) if first_content_seconds is not None else None,
            "last_activity_seconds": round(last_activity_seconds, 4) if last_activity_seconds is not None else None,
            "total_seconds": round(total_seconds, 4),
            "output_chars": len(content),
            "completion_tokens": completion_tokens,
            "prompt_tokens": prompt_tokens,
            "total_tokens": total_tokens,
            "usage": final_usage,
            "timings": final_timings,
        }
        await self._emit_progress(on_progress, {"phase": "complete", **stream_meta})
        result: dict[str, Any] = {
            "id": response_id,
            "object": "chat.completion",
            "created": response_created,
            "model": response_model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }],
            "_stream": stream_meta,
        }
        if final_usage is not None:
            result["usage"] = final_usage
        if final_timings is not None:
            result["timings"] = final_timings
        return result
