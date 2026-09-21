# Marine Pipeline Studio v2026.09.21.40.8B

## Scope

This release closes the selected safety/concurrency backlog from the September 21 source audit while preserving the `.40.8A.2` Stage 2A route-coverage and trusted-decision baseline. The reported SQLite-on-SMB item is intentionally omitted because it came from the review/share environment and is not the real container deployment.

## Closed findings

### 1. Git publication safety
- Added a root `.gitignore` for `.env`, runtime/manual directories, databases, caches, logs and release archives.
- `upload-github.sh`, `upload-github.ps1` and `upload-github-stage2c.ps1` no longer delete `.git` and never force-push.
- Existing remote history is attached on first initialization and normal commits/pushes are used afterward.
- Defense-in-depth staged-file checks abort publication if secrets/runtime/manual paths are staged.

### 2. Local verifier outage is no longer fake evidence
- `httpx.TransportError`, timeout and connection failures from source-image transcription now propagate to the Stage 2B worker.
- `_run_job` can therefore defer the route, open the Pi5/OnePlus endpoint circuit breaker and retry without consuming the job retry budget.
- Transport failure is never converted into a completed `UNREADABLE`/`UNCERTAIN` evidence result.

### 4. Correction-generation reconciliation
- Added logical source-key reconciliation across Stage 2B/2C rerun generations.
- One active automatic text correction / vision enrichment remains per Docling source item.
- Older generations are retained as `superseded` audit history.
- Human-verified decisions outrank every automatic rerun.
- Vision ties retain the existing evidence rule: applied evidence wins; normal route wins an equal-status tie over artifact-sweep evidence.

### 5. Duplicate watcher submission race
- File discovery now uses `JobStore.create_pending_once()`.
- The source-identity lookup and insert execute under one SQLite `BEGIN IMMEDIATE` transaction, so periodic discovery and a manual Start cannot both submit the same `(filename, source_sha256)` document.
- Existing queue/database format is preserved.

### 8. Manual cross-check ledger write locking
- `_persist_manual_crosscheck()` now performs its read-modify-upsert inside the same `_stage2c_ledger_lock` used by normal Stage 2C worker writes.
- This prevents a manual crossover and concurrent worker completion from silently losing one another's ledger update.

### 11. Groq in-flight quota reservations
- Groq calls now reserve one request plus their conservative token estimate atomically before starting HTTP.
- Concurrent calls see existing reservations, closing the previous check-then-act race around the configured 90% reserve.
- A completed response converts the reservation to actual usage; transport/cancellation paths release the reservation without charging usage.
- Snapshot output now reports in-flight request/token reservations separately from actual rolling-24h usage.

## Book-flow Verifier Audit bypass
- **Bypass audit for testing** is now always visible in a dedicated control panel at the top of every Book workflow page.
- Before Stage 2B is complete it remains visible but disabled; the server also refuses to enable it while any current verification/artifact-sweep row is unfinished.
- Enabling or removing the bypass requires an explicit Yes/No browser confirmation.
- Bypass state and unresolved count are shown directly in Stage 2C.
- The bypass never accepts unresolved evidence; it only removes the audit block for downstream testing.

## Deliberately not changed
- The audit report's SQLite/SMB concern is not treated as a product defect because the real application database runs in the container deployment, not the review share.
- Route-priority truncation was already closed in `.40.8A.2`.
- Config type validation, Stage 3 table-cell provenance, equipment-scope hardening, async endpoint hardening, exact token budgeting and stronger claim-to-citation entailment remain follow-up work.
- Prior transactional retrieval-generation/freshness findings remain tracked separately and are not claimed fixed here.

## Regression coverage

Direct tests cover:
- publish scripts retaining Git history and refusing staged secret/runtime paths;
- local transport failure propagating instead of manufacturing `UNREADABLE`;
- current-generation correction reconciliation and human-authority precedence;
- duplicate same-file submission across independent `JobStore` instances;
- manual cross-check taking the shared ledger lock;
- concurrent Groq reservation protection and release on failed transport;
- Book workflow bypass visibility, confirmation and non-acceptance wording.

Validation before packaging: **463/463 tests pass** (`pytest -q`). The exact packaged ZIP is revalidated before release.
