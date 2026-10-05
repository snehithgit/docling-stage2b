# Book workflow audit — 2026-10-05

## Observed on the live deployment

19 processed books, zero current book retrieval indexes and zero current machine embedding indexes. All 19 book records reported identity mismatch. Review queue: 221 pending text opinions, 14 failed review jobs, no active reviewers. This is a snapshot, not proof that every book contains bad text.

## Fixed

1. The automatic downstream sequencer read `postprocess_job_id` from database rows that expose `id`. It used zero and never matched completed verification summaries. It now advances the actual processing job through correction finalization, canonical chunks and equipment embeddings while retaining existing human gates.
2. Result directory names contain conversion IDs. Integrity checking incorrectly compared these with processing IDs. It now validates the explicit database conversion mapping and checks derived metadata against the processing ID. Wrong conversion mappings still fail closed.
3. Retrieval status was modifying derived metadata during a GET. It now audits without repairing; repair remains in the sequencer.
4. Library structural-review blockers now display as needing a human decision instead of an unexplained pipeline state.
5. Shared navigation separates Library, Correct books, Questions and evidence, and Devices and advanced tools. Retrieval is labelled Ask your books.

## Correction and worker semantics reviewed

Raw Docling remains immutable. Correction overlays and human decisions remain authoritative. Normal verification, second opinions and anomaly opinions have separate queues; AI review is advisory. Physical-provider reservations prevent two simultaneous inferences on one endpoint. Review dispatch waits behind unfinished primary work. Existing cloud/local selection and GPU cooldown policies are retained.

## Retrieval and citation semantics reviewed

A current canonical index is required before a machine embedding can be considered ready. Equipment scopes combine assigned manuals and isolate unrelated machines. Retrieval combines lexical and embedding candidates; answer generation receives labelled evidence. Citation checks bind claims to cited source labels and verify critical tokens plus word overlap. These checks are conservative heuristics, not a guarantee of semantic correctness.

## Validation and remaining limits

751 backend tests passed locally; one skipped; two existing POSIX permission checks fail on Windows and are delegated to Linux CI. All 14 frontend behavioral tests passed. New regression tests cover distinct conversion/processing IDs, rejection of wrong mappings and sequencing with real database field names.

No blanket re-OCR, human decision reset or deletion was performed. Live answer quality has not yet been benchmarked after indexes become current. Review jobs that failed and unresolved source findings still require inspection. A complete redesign and exhaustive quality guarantee are not claimed by this repair.

## Colab artifact readiness follow-up

Book readiness and automatic finalization previously summed only Pi5 and OnePlus physical counters. Completed, processing, and failed artifact jobs claimed by Colab disappeared from those totals. Provider-independent current-job counts now drive book readiness, finalization blockers, and the library/book progress displays. Physical counters remain available for device-specific consumers. A SQLite regression test covers all four statuses for Colab artifact jobs.

## Worker status consistency

The optimized status snapshot counted stored endpoint credentials as eligible even when the runner was idle, its status was stale, or a worker was paused. It now applies the same runner freshness and pause rules as scheduler selection. Credentials remaining on disk no longer imply runtime availability. A regression compares both selection paths across these conditions.
