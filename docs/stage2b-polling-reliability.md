# Stage 2B polling and queue reliability

This build removes route-discovery work from read-only Verification GET endpoints.

## Route sync

- The background discovery loop owns normal route discovery.
- Manual Start / Verify book may request an immediate sync before authorizing work.
- `Stage2BWorker.sync_routes_once()` is protected by one asyncio lock.
- Each completed Stage 2A result is cached by `routes.json` and `source_manifest.json` file metadata (path, mtime_ns, size).
- Unchanged files are not reread, rehashed, reparsed, or written back to `verification_jobs`.
- A changed route/manifest invalidates the signature and is synchronized normally.

## Verification page polling

The page no longer uses `setInterval`. Polling is self-scheduled with `setTimeout` only after the previous refresh finishes. `refreshInFlight` is a second guard against accidental overlapping refreshes.

## Manual Verify book and retry backoff

Pressing Verify book authorizes all current pending routes for that book and clears their `next_attempt_at` delay. Retry counters/history are preserved. This means a route already authorized but sleeping in backoff is explicitly woken by a new manual Verify request.

## Container log visibility

Retryable and terminal Stage 2B errors are logged through `uvicorn.error` with target, job ID, route ID, active full-image/crop/text stage, retry delay, HTTP status when applicable, and exception text. Error audit JSON remains written under each book's `verification/` directory.
