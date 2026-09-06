from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator


class EventBroker:
    """In-process signal fan-out for dashboard refreshes; SQLite remains the source of truth."""

    def __init__(self) -> None:
        self._listeners: set[asyncio.Queue[str]] = set()

    def notify(self, reason: str = "state") -> None:
        payload = json.dumps({"reason": reason})
        for listener in list(self._listeners):
            if not listener.full():
                listener.put_nowait(payload)

    async def stream(self) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
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
