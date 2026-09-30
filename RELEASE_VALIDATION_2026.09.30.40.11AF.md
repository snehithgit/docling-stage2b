# Marine Pipeline Studio — Release Validation — 2026.09.30.40.11AF

## Scope

40.11AF is a correctness/performance hardening release built from the user-supplied `40.11AE-patched` tree. It completes the confirmed deep-worker fixes plus the performance follow-ups that had remained pending from the 40.11AB audit.

No Docling extraction rule, Stage 2A route rule, Stage 2C correction rule, Stage 3 chunk rule, retrieval rule version, or human-decision authority rule is changed by this release.

## Correctness fixes completed

### Worker administration vs scheduler dispatch

- Colab Update and Remove are now coordinated by the same Stage2B dispatch lock used for physical-provider reservation.
- A worker selected by a scheduler task just before an admin action is revalidated under the dispatch lock before it can be reserved.
- A removed/stopped dynamic Colab worker therefore cannot be claimed by a stale scheduler selection after the admin mutation completes.
- Local Pi5/OnePlus Stop/Resume/Artifact-participation changes use the same dispatch interlock.
- Colab Test now owns an explicit provider reservation for the entire health/generation probe, so a normal Text/Vision/Artifact/review job cannot start on that physical worker concurrently.
- Busy removal remains drain-safe: the current inference is allowed to finish; no new job is accepted; removal completes before the reservation is released.

### Dynamic Colab failure isolation

- `colab:<worker-id>` 401/403/404 Artifact failures open that exact worker's circuit breaker and defer the queue row instead of permanently failing it.
- Removed Colab workers have device-lock/circuit/cooldown/runtime-state entries cleared so a later worker cannot inherit stale outage state.

### AI review-assistant stale-result protection

- Review jobs revalidate that their ledger entry still needs AI assistance before spending an inference call.
- Immediately before ledger publication, the current ledger is checked again while holding the Stage2C ledger lock.
- If the human resolved the text/vision item while inference was running, the AI result is marked discarded and is not attached to the authoritative ledger entry.
- Human decisions remain authoritative; the assistant still cannot set `human_verified` or a human visual decision.

### Book lifecycle write safety

The previously-audited missing book lifecycle locks are included:

- vision evidence-recovery waiver;
- visual-decision undo;
- correction-entry repair;
- Docling page-review save now holds the lifecycle lock across source reads and the final write.

This prevents a concurrent confirmed book deletion from being followed by a late ledger/page-repair write that recreates an orphaned result directory.

## Performance work completed

### Hot worker/status paths

- Worker registry status now performs one `worker_registry.json` read per snapshot and one secret-file read per Colab worker.
- Repeated `snapshot()` / `configured_colabs()` / `read_api_key()` / `api_key_error()` hot-path reads were removed.
- Worker-management and review-worker filesystem I/O runs through `asyncio.to_thread()` rather than blocking the uvicorn event loop.
- Review-assistant polling reuses the registry snapshot already loaded for that cycle and reads independent book ledgers concurrently.

### Stage2B result-list payload reduction

- Public `list_jobs`, `list_results`, and `list_remaining` queries no longer select the potentially large `request_json` / `result_json` columns only to discard them in Python.
- A new additive `execution_provider` column stores the actual executing provider separately.
- Existing rows are backfilled once from persisted request/result provenance during the additive database migration.
- New normal jobs persist the physical dispatch provider at claim/completion time; dynamic Colab workers can therefore retain exact provenance such as `colab:colab-1` without returning raw model payloads to list endpoints.
- Raw internal/audit methods still deliberately read request/result JSON where required.

### Verification-book aggregation

- `/api/stage2b/books` now uses a shared 4-second TTL cache so multiple open pages do not repeat the same large aggregate every poll cycle.
- Additive covering indexes support public result scans and the current-book aggregate.

### Site-wide error/status cost

The earlier AB audit fixes that had not been present in the AE tree are now included:

- `/api/errors` has a shared 4-second TTL cache with single-flight recomputation.
- Independent per-book audit reads run concurrently.
- `correction_ledger.json` is loaded once inside `verifier_audit_summary()` and shared with `human_review_summary()`.
- Remaining per-book post-process enrichment filesystem work runs through a bounded 8-worker pool while preserving input/output order.

### Browser polling

- The Verification page no longer runs the full seven-request refresh every 3 seconds forever.
- Visible-tab cadence is 5 seconds.
- Hidden tabs skip the heavy refresh and only wake the scheduler every 15 seconds.
- Returning to the tab triggers an immediate refresh.
- Existing `refreshInFlight` protection still prevents overlapping refreshes.

### Lifecycle lock retention

- Per-book lifecycle locks are now held in a `WeakValueDictionary`.
- Active/waiting users retain strong references for the full critical section, while unused historic book IDs no longer accumulate lock objects for the life of the process.

## Retrieval correction retained

The deep-audit regex correction is retained: incidental `Page` / `Rev` / `Date` identifier lines now use real regex word-boundary/digit tokens instead of double-escaped literals, restoring the intended small lexical penalty for boilerplate identifier collisions.

## Database compatibility

The Stage2B database migration is additive:

- adds `execution_provider` if absent;
- performs a one-time provenance backfill without model calls;
- adds performance indexes;
- does not invalidate or recreate Stage2B rows.

Existing converted outputs, processed results, human ledgers, Docling page repairs, Stage2C state and Stage3 chunks remain intact.

## Regression coverage added

New/expanded tests cover:

- registry hot snapshot performs one registry read and one key read per worker;
- worker Update holds the dispatch interlock against a concurrent scheduler reservation;
- worker Delete prevents a stale scheduler task from reserving the removed provider;
- dynamic Colab Artifact 401 opens the worker-specific circuit and defers rather than fails the row;
- public Stage2B select lists exclude raw request/result payload columns;
- AI review-assistant eligibility rejects already human-resolved text and vision entries;
- lifecycle lock cache releases unused lock objects;
- Verification polling remains non-overlapping and uses visibility-aware backoff.

## Validation

Executed from the 40.11AF working source tree:

- `python -m pytest -q` → **642 passed, 1 skipped**
- `python -m compileall -q app` → **PASS**
- every `app/static/*.js` with `node --check` → **PASS**

Fresh extraction of the packaged `marine-pipeline-studio-v2026.09.30.40.11AF.zip`:

- `python -m pytest -q` → **642 passed, 1 skipped**
- `python -m compileall -q app` → **PASS**
- every `app/static/*.js` with `node --check` → **PASS**

## Upgrade

Upgrade from 40.11AE/40.11AE-patched using the same persistent database, `processed/`, converted output, review ledgers, page repairs and equipment state.

No Stage 1, Stage 2A, completed Stage 2B, Stage 2C or Stage 3 rerun is required solely for 40.11AF. The additive Stage2B schema/index/provenance migration runs automatically at startup.
