# Release validation — 2026.09.24.40.11R

Table-structure integrity release based on 40.11Q.

## Trigger / regression case

A real AIR COND.PLANT FINAL PLAN table was extracted with dozens of logical room rows collapsed into a handful of one-row Docling cells. One source cell contains the visible sequence beginning `1 WHEEL HOUSE ... 34 SEAMAN 5`, while a neighboring cell contains the corresponding deck/category stream (`BRIDGE`, repeated `C`, `B`, `A`, `UPPER`, ...). Treating this as ordinary text would preserve words while losing row relationships.

The fix deliberately does **not** guess those relationships. Raw Docling stays immutable and a human-authoritative whole-table overlay is required before current Stage 3 output can be built.

## Implemented

- Added `app/table_repair.py`.
  - Conservative `TABLE_ROW_COLLAPSE` detector for a one-row/one-column Docling cell containing a long monotonic numbered-record sequence.
  - Detection is review-only; it never auto-pairs neighboring column values or rewrites Docling source.
  - Stable immutable-table source signatures protect repairs against source/version drift.
  - TSV matrix parsing/validation, repair persistence, application, dismissal, and compatibility backfill.
- Stage 2A diagnostics/routes:
  - Emits high-priority `TABLE_ROW_COLLAPSE` / `HUMAN_REVIEW` findings.
  - Groups multiple collapsed cells from the same physical table into one structural repair obligation.
  - Plain `Accept` is forbidden for this failure; the reviewer must save a repair or explicitly dismiss it as a false positive.
- Added durable `table_structure_repairs.json` overlay.
  - Saves a human-verified whole-table matrix with header-row metadata and immutable source signature.
  - Raw converted Docling ZIP is never modified.
  - Dismissing a collapse finding deactivates any saved overlay for that table.
- Added table repair UI:
  - `/table-repair?job=<id>&route=<route-id>`.
  - Shows the highlighted original source page, immutable raw table matrix, detected numbered-record evidence, editable TSV matrix, header-row control, note, save, reset, and false-positive dismissal.
  - Book structural-review UI shows `Repair table` instead of ordinary `Accept` for row-collapse routes.
- Stage 3 integration:
  - `STAGE3_RULE_VERSION` bumped to `stage3-canonical-integrity-v4`.
  - Human table overlays are applied only to the in-memory working Docling document before chunking.
  - Repair source signatures are revalidated against immutable Docling source.
  - If a repaired table has current human-approved table-cell text corrections, Stage 3 fails closed unless their corrected text is represented in the human repair.
  - Chunk metadata records applied table-structure repairs.
  - `table_structure_repairs.json` participates in the Stage-2C output signature, so changing a repair makes Stage 3 stale/rebuildable.
- Upgrade compatibility / no forced Stage-2A rerun:
  - Existing Q-era result directories are scanned from their immutable converted Docling ZIP before Stage 3.
  - Missing row-collapse structural routes are appended to the current `routes.json` without creating a new Stage-2A/Stage-2B generation.
  - Scan state is persisted in `table_row_collapse_scan.json` and reused on later polling.
  - Existing verification results, correction ledgers, human decisions, and artifact-sweep work are preserved.
  - Compatibility route backfill is serialized with structural-review decisions through the per-book lifecycle lock so an initial scan cannot overwrite a simultaneous human decision.
- Stage 3 and the automatic pipeline sequencer both honor newly backfilled structural blockers before starting current chunking.
- Artifact API allow-list includes `table_structure_repairs.json` and `table_row_collapse_scan.json` for diagnostics/export.

## Real-source validation

Tested against the immutable AIR COND.PLANT FINAL PLAN Docling ZIP:

- detector findings: **3**
- affected physical tables: **1** (`table_index=13`)
- collapsed cell indexes: **23, 44, 64**
- compatibility backfill result: **1** blocking `TABLE_ROW_COLLAPSE` route for the whole affected table
- the detector does not attempt to infer which `C/B/A/UPPER` value belongs to which room; that mapping remains a human decision made against the source page.

## Automated validation

- `PYTHONPATH=. pytest -q`: **592 passed, 1 skipped**
- `python -m compileall -q app`: **PASS**
- `node --check app/static/*.js`: **17/17 PASS**

Key regression coverage includes:

- long numbered records in one logical table cell are detected;
- a short four-step list is not promoted by this detector;
- human table repair round-trip and application;
- stale source signatures are rejected;
- table repairs change Stage-2C output freshness;
- row-collapse routes cannot be plain-accepted;
- compatibility backfill adds one blocker per affected table and is idempotent;
- false-positive dismissal deactivates an existing repair;
- Stage-2A route generation groups collapsed cells by table;
- repair page / book action wiring.

## Migration

**Do not rerun Stage 1, Stage 2A, or completed Stage 2B work solely for this release.**

Upgrade Q → R with the same persistent database, output directory, and processed/result directories. For an existing completed Stage-2A book, the normal book/structural-review view or the automatic pipeline sequencer runs the compatibility scan. If a collapsed table is found, Stage 3 remains blocked for that book until the table is repaired or explicitly dismissed as a false positive.

For the current AIR COND case, expect one structural repair obligation representing table 13, with collapsed source cells 23/44/64 grouped into the same table repair.

## Safety boundaries

- No automatic reconstruction of row-to-column relationships.
- No mutation of immutable Docling JSON/ZIP.
- No silent use of a stale repair after source change.
- No plain `Accept` path for a confirmed row-collapse route.
- No Stage-3 build past an unresolved structural blocker.
- No silent loss of an already approved table-cell text correction when a structural table overlay replaces the raw grid.
