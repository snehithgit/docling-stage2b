from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator


class EventBroker:
    """In-process signal fan-out for dashboard refreshes; SQLite remains the source of truth."""

    def __init__(self) -> None:
        self._listeners: set[asyncio.Queue[str]] = set()

    def notify(self, reason: str = "state", **context: object) -> None:
        """Publish a lightweight refresh event with optional alert context.

        SQLite/files remain the source of truth. Context is deliberately copied
        from the failure/completion source so notification transports can name
        the affected manual without querying pipeline databases themselves.
        """
        event: dict[str, object] = {"reason": str(reason or "state")}
        for key, value in context.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                event[str(key)] = value
            else:
                event[str(key)] = str(value)
        payload = json.dumps(event, ensure_ascii=False)
        for listener in list(self._listeners):
            if not listener.full():
                listener.put_nowait(payload)

    async def stream(self, maxsize: int = 1) -> AsyncIterator[str]:
        """Stream events. Dashboard callers use coalescing size=1; alert transports may request a deeper queue."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max(0, int(maxsize)))
        self._listeners.add(queue)
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"event: refresh\ndata: {payload}\n\n"
                except TimeoutError:
                    yield "event: keepalive\ndata: {}\n\n"
        finally:
            self._listeners.discard(queue)
