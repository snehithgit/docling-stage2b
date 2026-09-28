from __future__ import annotations

import asyncio
from collections.abc import Callable


class BookLifecycleLocks:
    """Process-local per-book lifecycle exclusion.

    Stage 2C/Stage 3 starts and destructive book deletion all run in the same
    application process. Sharing one asyncio lock per postprocess job closes the
    check-then-act window where a background builder could start while deletion
    is quarantining that book's files.
    """

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get(self, postprocess_job_id: int) -> asyncio.Lock:
        key = int(postprocess_job_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock


LifecycleLockGetter = Callable[[int], asyncio.Lock]
