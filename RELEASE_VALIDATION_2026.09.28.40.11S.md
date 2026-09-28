# Release validation — 2026.09.28.40.11S

## Scope

Evidence-first human review for `READING_ORDER_ANOMALY` without mutating raw Docling output and without forcing a Stage 2A/2B rerun of existing 40.11R results.

## Problem fixed

40.11R detected page-level reading-order anomalies with useful evidence in `diagnostics.json`, but grouped the human route down to a count. The book workflow therefore offered plain **Accept** / **Dismiss** without exposing the affected pages or detector evidence.

## Changes

- Added `/reading-order-review?job=<id>&route=<route_id>`.
- Added `GET /api/postprocess/jobs/{job_id}/reading-order-review/{route_id}`.
- The context endpoint hydrates page-level evidence from the existing `diagnostics.json`, including legacy 40.11R grouped routes.
- Original PDF pages are shown through the existing source-page renderer with sampled Docling text/table items highlighted when provenance is available.
- The reviewer sees layout model, mismatch/inversion metrics, and the current Docling body-order sample.
- The book card no longer exposes direct Accept/Dismiss actions for `READING_ORDER_ANOMALY`; it exposes **Review anomaly** only.
- Every flagged page must be explicitly marked reviewed before either resolution decision becomes available.
- Backend `set_stage2a_human_review_decision()` independently rejects blind resolution and persists `human_reviewed_pages` for auditability.
- If the original page cannot be rendered, the review page does not allow that page to be marked reviewed.
- If the ordering is genuinely wrong, the route remains unresolved. No guessed reordering or raw Docling mutation is introduced.
- Future generated reading-order routes carry affected page-number hints; detailed evidence remains in `diagnostics.json`.

## Compatibility

Existing 40.11R processed directories are supported directly. No Stage 1, Stage 2A, Stage 2B, or Stage 2C rerun is required solely for this UI/workflow fix, provided the original `diagnostics.json` and source PDF remain available.

## Safety invariants

- Raw Docling source remains immutable.
- A human cannot resolve a reading-order anomaly without explicitly reviewing all flagged pages.
- Missing review evidence fails closed.
- Existing Stage 3 structural-review gate remains authoritative.

## Validation results

- `python -m compileall -q app tests`: PASS
- every `app/static/*.js` with `node --check`: PASS
- targeted structural/UI regression suite: **124 passed**
- full project regression suite: **591 passed, 1 skipped**
