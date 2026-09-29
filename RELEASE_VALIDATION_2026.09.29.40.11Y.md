# Marine Pipeline Studio — Release Validation — 2026.09.29.40.11Y

## Scope

This release fixes evidence-recovery navigation and makes human visual-review decisions reversible without rerunning inference.

### Changes

- Errors/diagnostics deep-link to the affected Evidence Recovery queue and, when available, the exact ledger entry.
- Vision Audit supports `view=evidence_recovery` and `entry=<ledger-entry-id>` deep links.
- `Recover evidence` now has a dedicated backend action; it no longer replays the human classification endpoint.
- Added **Use existing evidence** for human-approved Useful/Technical visuals that already contain usable visual evidence but were flagged because verifier parsing/crop coverage was incomplete.
- Recovery waiver is fail-closed: it is rejected when summary, visible text, and visible objects are all empty.
- Added **Undo human decision**. It reopens the visual in the human-review queue without rerunning the vision model.
- Waivers are persisted as audit metadata while original verifier parse-failure metadata remains intact.
- Stage 3 remains blocked for evidence-empty Useful/Technical visuals until evidence is recovered or the human decision is changed.

## Validation

- Full pytest suite: **617 passed, 1 skipped**
- Python `compileall`: **PASS**
- All frontend JavaScript `node --check`: **PASS**

## Upgrade behavior

- No Stage 1 / 2A / 2B rerun is required solely for this release.
- Existing correction ledgers remain compatible.
- Undoing a human visual decision reopens only that review subject; it does not queue another model inference.
- Waiving recovery does not remove machine-error provenance. It only records that a human judged the existing evidence sufficient.

## Safety invariants

- Raw Docling output remains immutable.
- A recovery waiver cannot make an evidence-empty visual eligible.
- Human decisions remain authoritative and explicitly reversible.
- No technical evidence is invented during waiver or undo.
