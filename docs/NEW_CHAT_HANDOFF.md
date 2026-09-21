# `.40.8B` handoff

`.40.8B` is the current implementation baseline. It closes the user-selected safety/concurrency backlog while retaining `.40.8A.2` route coverage and `.40.8A` trusted-decision guarantees.

## Closed in this release

- Git publishing: root `.gitignore`, no `.git` deletion, no force-push, and refusal if secret/runtime/manual/database paths are tracked or staged.
- Local verifier outages: transport/timeouts propagate to the existing Pi5/OnePlus endpoint circuit breaker instead of becoming fake completed `UNREADABLE` evidence.
- Rerun reconciliation: one active automatic correction/enrichment per logical Docling source item; older generations remain superseded audit history; human-verified decisions stay authoritative.
- Watcher race: same `(filename, source_sha256)` discovery is atomically get-or-created under a SQLite write transaction.
- Manual cross-check: read/modify/upsert now uses the shared Stage 2C ledger lock.
- Groq free-tier guard: request/token estimates are reserved in-flight before HTTP so concurrent calls cannot all pass the same reserve snapshot.
- Book workflow: **Bypass audit for testing** is visible for every book, requires explicit confirmation, is disabled until required verification work completes, and never accepts unresolved evidence.

## Explicitly omitted

The SQLite-on-SMB finding from the external review is not treated as a runtime defect because the inspected share was only a review/export path; the actual application runs in the container deployment.

## Already closed before this release

- `.40.8A.2`: Stage 2A collects/sorts the complete candidate set before its 5000-route runaway safety ceiling; deferred overflow is retained/reported instead of silently lost.
- `.40.8A`: human-authority preservation, cross-check safety, counted troubleshooting-action obligations, and table row/column provenance.
- `.40.7.1`: monitoring-first Telegram dashboard.

## Still open

- Atomic immutable retrieval/index generations with a single current pointer.
- Explicit Stage 3 / retrieval / embedding freshness versions/fingerprints.
- Persisted table structural-binding enforcement when overlays are consumed.
- Docling forgotten-task (404) resubmission.
- Config list-type validation.
- Stage 3 table-cell correction provenance.
- Equipment-scope allow-list enforcement on anchor/neighbors.
- Blocking async endpoint cleanup, more exact token budgeting, HTTP-client lifetime, recursion guard, verifier schema robustness, Telegram tests.
- Stronger claim ↔ cited-evidence support checking.

Do not publish the final production benchmark until the remaining correctness/freshness work is closed, then run **Revalidate all + rebuild** and the fresh N150 machine-scoped BGE benchmark/holdout.

Current release target: `2026.09.21.40.8B`
