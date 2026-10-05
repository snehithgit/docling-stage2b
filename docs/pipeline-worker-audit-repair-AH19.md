# Pipeline and worker audit disposition — AH19

Reviewed the user-supplied 2026-10-05 audit against 82c206e. Its referenced patch was not supplied; these fixes were implemented independently and exercised with the repository tests.

## Confirmed findings repaired

1. Colab supervisors retain active lanes until their current job finishes. Removed/ineligible lanes retire between jobs. Shutdown cancellation requeues only current processing rows, without retry penalties or reviving terminal rows.
2. Transport failures use the physical dispatch provider. Colab gateway/auth/tunnel errors open that worker's circuit and defer work.
3. Review/anomaly endpoint outages defer without consuming attempts, stop that drain, and cool down that worker for 30–300 seconds. Invalid model/crop results retain bounded retries. Cancellation releases its queue claim.
4. Stage 2C/3 retries use per-book exponential backoff (60–1800 seconds); Pi5 waiting delays are respected. Ready stages clear backoff. Manual starts remain available.
5. Failed automatic runner recovery affects only its account and records a safe error. Other accounts keep refreshing.
6. Runner controls reserve only their physical worker. Network calls run outside the global dispatch lock; release uses existing drain-safe removal.
7. Failed runner controls restore the previous pause state. A successful Stop remains paused.
8. Book exceptions are isolated and reported in pipeline book_errors. Busy lifecycle locks are skipped, including the compatibility-scan race; later books can advance.
9. Finalization gates use the same effective verification rows as Stage 2C, excluding additive human recovery and optional unfinished artifact sweep rows.
10. Docling poll 404 has a distinct lost-task exception. Conversion failure clears that task ID so Retry resubmits instead of polling the lost task forever.
11. Startup recovers interrupted deletion reservations to failed/DeletionInterrupted. Conversion-lookup failure cancels the reservation. Interrupted filesystem quarantine is not silently reversed; inspect quarantined files before retrying deletion.
12. Superseded visual entries reject human decisions and undo, preserving current visual authority.
13. One book's structural discovery error is logged and does not abort other books' review discovery.

## Additional findings repaired

- Colab inference releases the book lifecycle lock. Publication reacquires it and rechecks book existence and the existing evidence/human-decision signature before writing, for text, vision, and structural audits.
- Candidate discovery and queue synchronization share the ledger publication lock, closing stale-snapshot rearming of completed anomaly audits.
- Terminal primary failures no longer block every review GPU. Pending/processing primary work retains priority. Failed rows remain visible and continue blocking that book's Stage 2C/3 publication.
- Stage 3 uses a semantic Stage 2C fingerprint that excludes backfill timestamps/counters. Proven-current legacy indexes are migrated by the pipeline under locks, without rechunking. Human corrections and structural repairs still invalidate chunks.
- Backfill repairs leftover publishing/error markers for already-present ledger entries.
- A zero-route book can advance only after successful, still-current route AND artifact discovery; missing/changed discovery cannot be treated as a clean book.
- Reindex-all checks book availability and active Stage 3 work under the lifecycle lock. Derived JSONL writers use unique temporary files.
- Registry, runner credentials/configuration, and ledger JSON are flushed before atomic replacement.
- Safety-refresh waits report Pi5 waiting/cancellation immediately instead of waiting for their timeout.
- Structural audit discovery reuses its loaded route/diagnostics context instead of reparsing files per route. Pipeline freshness hashing and expensive Stage 3 document loading, token validation, retrieval annotation, and JSONL writing run outside the event loop.

## Audit qualifications

The claimed repeated ZIP validation is already prevented by the converted-output registration checks and rejected-file signature cache in the audited baseline; no redundant replacement was needed. The retrieval_rules_stale branch is unreachable under current ranking-only compatibility semantics; it is removed in this repair. Worker status already reports per-worker ineligibility reasons in AH12 onward.

These repairs do not establish a hard job-start SLA, restore exhausted Colab quota, solve Google login challenges, or certify absence of every performance bottleneck. Large CPU loops and network/model latency still merit profiling under production load. Raw Docling and human decisions remain authoritative and unchanged by advisory reviews.
