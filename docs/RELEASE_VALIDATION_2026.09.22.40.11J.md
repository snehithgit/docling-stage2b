# Release validation — 2026.09.22.40.11J

## Scope
Phase A production-integrity fixes from the 2026-09-22 deep bug audit, applied on top of 40.11I.

## Fixed
- Human text-correction REST save now holds the Stage 2C ledger lock for the complete correction-ledger read/modify/write transaction.
- Human visual-audit decision REST save now uses the same Stage 2C ledger lock as automated writers.
- Stage 2B protects all post-claim setup inside `_run_job`; an exception after an artifact has atomically entered `processing` is handled by the job cleanup path instead of escaping and orphaning the row.
- The same Stage 2B protected boundary now covers ordinary `mark_processing()` plus worker-state/event setup.
- Stage 1 conversion worker now catches unexpected `Exception` subclasses after entering processing, including SQLite `OperationalError`, and records a terminal failure instead of leaving the row stuck at `processing`.

## Recovery behavior
No destructive database rewrite is bundled. On deployment/restart, the existing startup `recover_interrupted()` path requeues rows abandoned in `processing`, including the previously observed MacGregor artifact-sweep orphans. The new Stage 2B boundary prevents the confirmed post-claim exception path from recreating them.

## Regression coverage
- Injected Stage 2B failure immediately after a preclaimed job enters `_run_job`; asserts the row does not remain `processing` and worker ownership is cleared.
- Injected Stage 1 `sqlite3.OperationalError` while persisting the Docling task id; asserts zero `processing` rows and a recorded failed job.

## Validation
- `python -m pytest -q`
- Result: **546 passed, 1 skipped**
- `python -m compileall -q app tests`
