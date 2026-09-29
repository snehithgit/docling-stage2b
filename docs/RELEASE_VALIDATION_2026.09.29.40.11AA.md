# Marine Pipeline Studio 2026.09.29.40.11AA — Release validation

## Purpose

Fix human visual **Recover evidence** returning:

`{"detail":"Vision verification job not found"}`

when the authoritative human visual ledger entry still points at a historical Stage 2B verification row.

## Root cause in 40.11Z

Human review is intentionally preserved across verifier-generation changes. Older pipeline behavior could therefore leave `correction_ledger.json` with a valid human-approved `vision_enrichment` entry whose `verification_job_id` points to a row that still exists in SQLite but has `is_current=0`.

40.11Z's durable recovery queue required the supplied verifier row itself to be current:

`SELECT ... FROM verification_jobs WHERE id=? AND is_current=1`

so a valid preserved human decision could not enqueue evidence recovery even though the same physical picture had a current verifier route.

## 40.11AA behavior

- The requested historical verifier row is loaded without silently discarding it.
- Its source must still be a Docling `picture`; non-picture recovery remains rejected.
- If the row is current, recovery behaves exactly as in Z.
- If the row is historical, AA searches only current rows for the same post-process book and the same picture `source.index`.
- Candidate selection prefers, in order of confidence:
  - the same `route_id`;
  - the same Artifact-vs-normal-Vision lane;
  - the same logical Stage 2B target;
  - a completed verifier row.
- Internal human-recovery rows are excluded from origin matching.
- The new durable recovery row is created under the resolved **current generation**, not the historical generation.
- Recovery provenance records both:
  - `requested_origin_verification_job_id`; and
  - `origin_verification_job_id` (the resolved current row).
- If no current verifier route for that same picture exists, AA fails closed with an explicit stale-route error rather than attaching recovery to unrelated evidence.

## Pipeline effect

This is a queue-origin compatibility repair only. It does not change Stage 1, Stage 2A extraction, completed normal Stage 2B results, Stage 2C semantics, Stage 3 rules, raw Docling data, or existing human decisions.

After upgrading Z -> AA, a visual that still shows evidence recovery required can be retried by clicking **Recover evidence**. A real durable Vision recovery row should then appear in the shared Start/Stop/Auto scheduler.

## Validation

- focused human visual recovery tests — PASS, including a historical-origin/current-picture remap regression;
- full pytest suite — **619 passed, 1 skipped**;
- Python `compileall` — PASS;
- all frontend JavaScript `node --check` — PASS.
