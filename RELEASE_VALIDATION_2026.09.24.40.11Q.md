# Release validation — 2026.09.24.40.11Q

Focused repair for the missing Stage-2C human-review entry exposed by completed text-verifier job 137813 (`AIR COND.PLANT FINAL PLAN`, page 72, route `R00001`).

## Fixed

- Stage-2B verifier completion and Stage-2C review-entry publication now have separate persisted lifecycle state:
  - `publishing`
  - `ready`
  - `not_required`
  - `error`
- Existing `verification_jobs` databases migrate additively with:
  - `stage2c_entry_state`
  - `stage2c_entry_id`
  - `stage2c_entry_error`
- A valid completed verifier result remains `completed` even if correction-ledger publication fails. Ledger failure can no longer overwrite or invalidate the verifier result.
- Missing review entries can be deterministically republished from the saved Stage-2B `request_json`/`result_json`; repair makes **no Pi5, OnePlus, Groq, or other model call**.
- Added `POST /api/postprocess/jobs/{job_id}/corrections/{entry_id}/repair` for idempotent review-record recovery.
- The Review page now auto-repairs a missing ledger entry before loading immutable Docling context. If repair is unavailable, the editor is disabled instead of presenting a blank but apparently valid Save/Keep-original form.
- Verification results expose review-entry readiness and show `Preparing review…`, `Manual override`, or `Repair / review` according to actual publication state.
- Text Audit keeps a completed `UNCERTAIN`/`LIKELY_CORRUPT` result visible as a human-review obligation even when its ledger entry is missing, and routes the user through repair rather than issuing a direct correction request that would 404.
- Stage-2C freshness blocks Stage 3 while a completed verifier result has a `publishing`/`error` review publication state.
- Backward compatibility: completed uncertain/corrupt text-verifier rows from 40.11P or older are also treated as blocking when their deterministic expected ledger entry is absent, even though their new publication-state columns are initially null. The normal Stage-2C backfill/sequencer can therefore republish them without model inference.
- Existing human-reviewed ledger decisions remain authoritative during idempotent republishing.

## Migration / operator impact

- **No Stage 1 rerun required.**
- **No Stage 2A rerun required for this Q fix.**
- Upgrade from 40.11P using the same persistent database and processed/output directories.
- Existing databases are migrated in place by the additive schema migration.
- For an already-broken 40.11P review URL, opening the same URL after upgrading to Q triggers deterministic ledger repair from the stored completed verifier result and then loads the immutable Docling context.
- Stage-2C backfill can also repair legacy missing entries automatically when the pipeline sequencer evaluates freshness.

## Regression coverage

Added/extended tests cover:

- additive migration of the three Stage-2C publication columns;
- repair of a completed text-verifier result whose correction-ledger entry is missing;
- publication failure recorded separately while verifier status remains `completed`;
- Stage-2C freshness rejection of explicit publication errors;
- Stage-2C freshness rejection of legacy completed uncertain text rows whose expected ledger entry is absent;
- repair API response from persisted verification data;
- Text Audit preserving the human-review obligation when publication is missing;
- Review UI auto-repair and disabled blank-editor fail-safe;
- Verification/Text Audit readiness labels.

## Validation

- `PYTHONPATH=. pytest -q`: **580 passed, 1 skipped**
- `python -m compileall -q app`: **PASS**
- `node --check app/static/*.js`: **PASS for all JavaScript files**
