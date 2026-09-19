# Artifact audit foundation — 2026.09.16.36

This release adds a first dedicated artifact inventory surface for Stage 2B / Stage 2C picture review.

## What is new

- `/artifact-audit` lists Docling picture artifacts from processed books.
- `/api/stage2b/artifact-audit` returns artifact inventory rows and summary counts.
- `/api/postprocess/jobs/{job_id}/picture/{picture_index}` serves the original artifact image from the immutable converted Docling ZIP.
- `/api/stage2b/artifact-audit/start-pending` authorizes pending current vision jobs.
- `/api/stage2b/artifact-audit/retry-failed` retries failed current vision jobs.

## Notes

- This release is intentionally inventory-first. It helps answer: how many artifacts exist, which were queued, which still have no route, and what output each verified artifact produced.
- Queue buttons work only on the current vision queue. They do **not** create new routes or rerun Stage 2A.
- The page remains read-only with respect to Docling source artifacts.
