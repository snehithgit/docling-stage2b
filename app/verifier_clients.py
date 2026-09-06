from __future__ import annotations

import asyncio
import base64
import inspect
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx


@dataclass
class EndpointHealth:
    reachable: bool
    model: str | None = None
    detail: str = ""


ProgressCallback = Callable[[dict[str, Any]], Any]


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
