# Release validation — 2026.09.22.40.11I

## Scope

`.40.11I` fixes contradictory Telegram human-review counts observed in production-like processed data: `/audit` could report unresolved visual items, `/errors` could aggregate them as human-review blockers, while `/visionaudit` reported `Remaining: 0`.

## Root cause

Three different notions of review state were being mixed:

1. `/audit` and `/errors` read the current Stage 2C correction ledger through `verifier_audit_summary()`.
2. `/visionaudit` and `/artifactaudit` enumerated persisted Stage 2B verification rows first and then attempted to find an exact current-ledger entry by the old row generation/route identity.
3. Evidence-recovery work for already accepted visuals was counted together with human decisions.

After a rerun, the current ledger could legitimately retain an unresolved visual entry whose `verification_job_id` pointed to evidence created in an older result directory. The ledger therefore reported review work while the Stage2B-row-driven Telegram queue could not rediscover it.

## Implementation

- Telegram Text/Vision/Artifact audit queues are now current-ledger-first.
- Stage 2B verification rows are used only as evidence/image sources by saved `verification_job_id`.
- Visual review state is collapsed by physical Docling `source_index` so normal Vision, artifact sweep and rerun duplicates cannot create multiple human-review subjects.
- A saved human decision wins over any duplicate pending visual route.
- If there is no human decision and both normal Vision and artifact-sweep duplicates are unresolved, the physical image is assigned to the normal Vision queue so it appears exactly once.
- `verifier_audit_summary()` now exposes separate `vision_route_review_required` and `artifact_review_required` counts while retaining the backward-compatible combined `vision_review_required` field.
- `/audit` now renders separate Text / Vision / Artifact / Recovery counts.
- `/errors` reports blocking human decisions separately from evidence-recovery blockers.
- Optional text audit work is not converted into a pipeline-blocker count.

## Uploaded processed-snapshot check

Using the supplied `_processed (13)(1).zip` and selecting the newest run per post-process job, the corrected authority calculation reports:

- 3 unresolved normal Vision decisions.
- 0 unresolved Artifact-sweep decisions.
- 10 accepted visuals awaiting evidence recovery.
- 13 total blocking visual/recovery items.

The important distinction is that the 10 recovery items are not human decisions and are therefore no longer presented as if `/visionaudit` should contain them.

## Regression tests

New coverage includes:

- normal Vision and Artifact review counts are split correctly;
- a human-reviewed duplicate visual wins over another pending duplicate for the same physical image;
- Telegram can open a current-ledger visual review even when its `verification_job_id` references an evidence row persisted against an older result directory.

Full suite:

- `545 passed`
- `1 skipped` — optional real `telegramify-markdown` integration test in the isolated validation environment.

## Static validation

- Python byte-compilation: pass.
- JavaScript syntax: 16/16 files pass `node --check`.
- YAML parse: pass for `config.yaml` and `docker-compose.yml`.
- Shell syntax: pass.

