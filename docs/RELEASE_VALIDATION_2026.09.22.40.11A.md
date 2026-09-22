# Release validation — 2026.09.22.40.11A

## Scope

Daily-use UI/UX and operator-safety release derived from the source-level UI audit and the follow-up bypass/layout review. This release does not change raw Docling data, verifier model assignments, Stage 2C human-authority rules, Stage 3 semantics, retrieval ranking, embedding model, or OnePlus workload-protection thresholds.

## Managed Add Book flow

- `Add book` now opens a managed ingestion page instead of the watcher queue.
- File upload and public document URL both write into the configured managed input directory and create the normal pipeline job directly.
- Duplicate file content reuses the existing managed input/job instead of creating a second conversion.
- The old `/convert` tool remains available as an advanced one-off Docling ZIP converter and now says explicitly that it does not register a pipeline book.
- Advanced one-off conversion options start collapsed.

## Book workflow and rerun safety

- Verifier Audit testing bypass moved from the permanent page-top panel into the Stage 2C card.
- The bypass control is shown only when Stage 2B is complete and unresolved audit evidence is actually relevant, or while a bypass is active.
- Stage 2A reruns now require confirmation on both Queue and Quality pages and explain which downstream artifacts may need rebuilding while preserving historical runs and human decisions.
- Expensive Artifact Audit batch-start/retry actions and OnePlus Stop/Restart actions now require confirmation.

## Human review usability

- `/review` now uses the shared application shell and identifies the current manual.
- Added Previous / Next navigation, `N of M` queue progress, automatic advance after a human decision, and keyboard navigation/decision shortcuts.
- Table-cell corrections now expose immutable Docling table context: target cell, row peers, column headers, and row/column structural span.
- Removed the dead `Not Pi5` UI label.
- Vision Audit is evidence-first: image + verdict/summary + human action first; extracted labels/objects and raw verifier payloads are collapsed behind disclosures.
- The testing bypass was removed from the Vision Audit page because its correct home is the Stage 2C book workflow card.

## OnePlus verification visibility

- OnePlus page now shows SSH/server state separately from the Stage 2B vision-verifier endpoint circuit.
- Circuit open/closed state, active job, failure count, outage-open time, and last verifier error are visible beside the existing workload/cooldown governor state.

## Retrieval daily-use improvements

- Grounded answer generation now runs as a cancellable backend job.
- UI shows spinner/progress state and elapsed generation time.
- Cancel actually cancels the backend generation task rather than only abandoning the browser request.
- Machine deletion requires confirmation naming the machine and manual count.
- Retrieval benchmark tooling is moved into a collapsed developer/quality section below the main question flow.

## Diagnostics and layout cleanup

- Errors & diagnostics now aggregates conversion failures, Stage 2A failures, Stage 2B verification failures/circuits, Stage 2C failures, Stage 3 failures, and unresolved human-audit gates.
- Queue page has one primary document table rather than a second conflicting advanced table.
- Quality page moves raw Summary/Routes JSON behind a Details disclosure and gives Rerun warning prominence.
- Shared navigation exposes Add book and explains that `/convert` is one-off conversion only.
- Plain-language labels are used alongside technical stage names (for example `Correction finalization · Stage 2C`, `Machine search & answers · Stage 4`).

## Regression coverage

- Existing UI/source regression expectations were updated to the contextual Stage 2C bypass and shared review shell.
- Added direct regression coverage for true backend-generation cancellation, with static coverage for the start/status/cancel browser flow.
- Static coverage asserts managed Add Book, OnePlus circuit visibility, confirmations for destructive/expensive actions, review queue navigation, and evidence-first Vision Audit.

## Validation

Pre-package validation: **526/526 tests pass**. Final delivery additionally re-runs Python compilation, JavaScript syntax checks, shell syntax checks, YAML/Compose parsing, ZIP integrity, and the full test suite against the exact extracted ZIP.
