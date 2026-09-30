from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable


class BookLifecycleLocks:
    """Process-local per-book lifecycle exclusion without unbounded lock retention.

    Stage 2C/Stage 3 starts and destructive book deletion all run in the same
    application process. Sharing one asyncio lock per postprocess job closes the
    check-then-act window where a background builder could start while deletion
    is quarantining that book's files.

    Locks are held through weak references. Any caller entering ``async with``
    keeps a strong reference for the full critical section (and queued waiters do
    the same), while completely unused book ids can be garbage-collected instead
    of accumulating forever in long-lived processes.
    """

    def __init__(self) -> None:
        self._locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()

    def get(self, postprocess_job_id: int) -> asyncio.Lock:
        key = int(postprocess_job_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def cached_lock_count(self) -> int:
        """Testing/diagnostic helper; unused locks naturally disappear after GC."""
        return len(self._locks)


LifecycleLockGetter = Callable[[int], asyncio.Lock]
