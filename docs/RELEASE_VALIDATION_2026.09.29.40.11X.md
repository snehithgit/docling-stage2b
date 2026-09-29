# Marine Pipeline Studio — 2026.09.29.40.11X validation

## Scope

40.11X fixes a pipeline-order regression in which resolving Stage 2A structural human-review routes changed `routes.json`, changed the Stage 2B generation hash, and incorrectly made already-completed Text/Vision/Artifact verification historical.

## Root cause

40.11W defined the Stage 2B generation as:

`sha256(converted_zip_sha256 + NUL + raw routes.json bytes)`

`routes.json` contains both machine verification routes and mutable structural-review state. Structural review writes fields such as `status`, `human_decision`, reviewed page/evidence IDs, notes and timestamps. Those human-only changes therefore generated a new Stage 2B generation even when every Pi5/OnePlus route was identical.

## Fix

40.11X separates the Stage 2B machine-verification contract from structural human-review state:

- New generations hash the immutable converted-source SHA plus a canonical representation of only `pi5` / `oneplus` verification routes.
- Human-only routes are excluded from the generation identity.
- On upgrade from W or older, if the currently persisted normal verification rows match the current machine-route contract, their existing generation is retained. This prevents the generation-algorithm change itself from replaying completed work.
- A genuine machine verification route change still produces a new generation and invalidates downstream verification as intended.
- Artifact sweep rows remain tied to the preserved/current machine generation and are not recreated by human structural-review decisions.

## Expected pipeline behavior

For a book where Stage 2B is complete, Stage 2C is current/finalized, and Stage 3 is blocked only by structural review:

1. Human reviews/accepts/dismisses the structural findings.
2. `routes.json` human metadata changes.
3. Stage 2B current generation remains unchanged if the machine routes are unchanged.
4. Stage 2C remains current because its verification signature is unchanged.
5. After the final structural blocker is resolved, the existing strict pipeline sequencer advances directly to Stage 3 on its next polling pass.

No Stage 2A rerun, Text rerun, Vision rerun, Artifact rerun, or Stage 2C rebuild is required merely because structural-review metadata changed.

## Regression tests

Added tests verify that:

- human structural-review metadata does not change the machine generation;
- a real machine-route change does change the generation;
- W-or-older current generations are preserved across the upgrade when their machine contract still matches;
- a mismatching machine contract is not preserved;
- an end-to-end route-file mutation from pending human review to accepted human review creates no second Stage 2B row/generation.

## Validation

Working tree full suite:

- `614 passed, 1 skipped`

Additional package checks are recorded after packaging.

## Packaged archive validation

Fresh extraction of `marine-pipeline-studio-v2026.09.29.40.11X.zip`:

- pytest: `614 passed, 1 skipped`
- Python `compileall`: PASS
- every frontend `app/static/*.js` with `node --check`: PASS
