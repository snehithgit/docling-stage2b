# Release validation — 2026.09.21.40.10

## Scope

Telegram human-audit transport for the existing Verifier Audit gate. No change to raw Docling data, retrieval architecture, or verifier model assignment.

## Human audit behavior

- Text: one target crop at a time; Apply verifier correction / Keep original / Stop.
- Vision: one normal visual route at a time; Useful / Not useful / Stop.
- Artifact: one FULL_TECHNICAL_VISUAL sweep image at a time; Technical / Decorative / Stop.
- Each saved decision automatically advances to the next unresolved item.
- `/stopaudit` ends the Telegram session without modifying the current unresolved item.
- Human decisions are written only through application service functions under the Stage 2C ledger lock.
- Accepted visual items with incomplete evidence reuse the existing human evidence-recovery queue.
- Telegram still has no pipeline start/stop/pause/retry control surface.

## Commands

- `/textaudit` or `/text audit`
- `/visionaudit`, `/vision audit`, or `/visual audit`
- `/artifactaudit`, `/artifact audit`, `/articleaudit`, or `/article audit`
- `/stopaudit`

## Validation

Pre-package full suite: **507/507 tests pass**. The exact packaged ZIP is re-extracted and tested again before delivery.
