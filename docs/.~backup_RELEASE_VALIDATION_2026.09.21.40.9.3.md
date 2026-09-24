# Release validation — 2026.09.21.40.9.3

## Scope

Pi5 correction-backfill outage hardening. This release changes failure handling only; correction eligibility, fidelity, human authority, and raw Docling immutability are unchanged.

## Confirmed failure mode

`_attempt_pi5_correction()` previously converted transport/time-out failures into a normal `status=pending` correction object. Both the manual correction-suggestion loop and automatic Stage 2C backfill therefore continued to the next row. With a 240-second inference read timeout, a 150-row backfill could theoretically spend up to about 10 hours walking a dead endpoint one row at a time.

## Fix

- Retryable transport/liveness failures propagate out of `_attempt_pi5_correction()`.
- Non-retryable model/JSON/fidelity failures remain row-local.
- Both correction loops stop after the first retryable endpoint failure.
- The current row is not counted as processed and all remaining rows stay eligible for later recovery.
- The existing Pi5 endpoint circuit is opened and normal exponential probe/backoff is reused.
- Backfill state becomes `waiting_for_pi5` with remaining count and retry metadata rather than `completed`/`partial`.
- When the Pi5 circuit closes, waiting backfills are restarted through their existing idempotent entry points, so already-current ledger entries are skipped and remaining corrections resume.
- A short `/health` preflight prevents a known-dead Pi5 from entering a long correction loop.
- Persisted Stage 2B model identity is reused for local Pi5 corrections, avoiding a redundant model-discovery health round trip.

## Quality invariants

- No correction candidate is discarded because of an outage.
- No human-verified entry is changed.
- Fidelity/scope/reverification gates are unchanged.
- Parse/model-format errors do not open the endpoint circuit or abort unrelated rows.
- Raw converted Docling artifacts are untouched.

## Regression coverage

1. Pi5 transport failure is re-raised instead of converted to a pending correction result.
2. Malformed model output remains a row-local pending/unparseable result.
3. Manual correction-suggestion backfill attempts only the first row, opens the Pi5 circuit, marks `waiting_for_pi5`, and leaves all rows unprocessed.
4. Automatic Stage 2C correction backfill does the same and persists `waiting_for_pi5` to `stage2c_backfill.json`.

## Validation

- Working-tree full pytest suite: **492/492 passed**.
- Final-package compile/static/YAML/ZIP checks and extracted-package pytest are required before distribution.
