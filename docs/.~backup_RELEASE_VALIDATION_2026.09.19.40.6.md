# Release Validation — 2026.09.19.40.6

## Scope

Verification-page status visibility improvement. Per-book work is separated into Text verification, normal Vision verification, and the full technical Artifact sweep instead of exposing only historical Pi5/OnePlus worker-lane totals.

## Validation

- Full pytest suite: **421 passed**.
- Python compile: PASS.
- Static JavaScript syntax: PASS.
- YAML/config parse: PASS.
- Static asset cache-busting matches `APP_VERSION = 2026.09.19.40.6`.

## Regression coverage

- Logical book counters classify `FULL_TECHNICAL_VISUAL` as artifact work regardless of whether an old row is stored under Pi5 or OnePlus.
- Legacy worker-lane counters remain unchanged for backward compatibility.
- Verification UI includes a dedicated Artifact sweep column and explicit pending breakdown for Text / Vision / Artifact work.
- Existing **Verify book** behavior remains unchanged and continues to authorize all pending routes for the selected book.

## Pipeline impact

No conversion, verification inference, route selection, worker scheduling, Stage 2C, Stage 3, retrieval, embedding, equipment registry, or persisted job data is modified. This release adds accurate logical status aggregation and presentation only.
