# Recovered 40.11R build validation — 2026-09-25

## Why this file exists

The first 40.11R archive created in the earlier chat session was not durably available for download. The preserved 40.11Q source archive and the original 40.11R release-validation specification remained available. This package was reconstructed from that Q source according to the preserved R specification and revalidated before packaging.

This recovered build is intended to be the continuation baseline for the next chat. It is functionally reconstructed; it is not claimed to be byte-for-byte identical to the lost first R archive.

## Base

- Base source: `marine-pipeline-studio-v2026.09.24.40.11Q.zip`
- Target app version: `2026.09.24.40.11R`
- Preserved original R specification: `RELEASE_VALIDATION_2026.09.24.40.11R.md`

## Reconstructed R functionality

- Added `app/table_repair.py`.
- Conservative `TABLE_ROW_COLLAPSE` detector for numbered logical records collapsed into one Docling table cell.
- Multiple affected cells in one physical table are grouped into one structural-review obligation.
- Existing Q-era processed books can be compatibility-scanned without rerunning Stage 2A/2B.
- Structural-review UI shows `Repair table`; row-collapse findings cannot be plain-accepted.
- Human repair is saved as `table_structure_repairs.json`; raw Docling ZIP/JSON stays immutable.
- False-positive dismissal deactivates any table overlay.
- Table repairs participate in Stage-2C output freshness.
- Stage 3 rule version is `stage3-canonical-integrity-v4`.
- Stage 3 validates the immutable table signature before applying a repair.
- Stage 3 fails closed if a human-approved table-cell text correction would be lost by a structural table overlay.
- Canonical chunk provenance records applied table-structure repairs.
- Artifact allow-list includes `table_structure_repairs.json` and `table_row_collapse_scan.json`.
- Added `/table-repair` UI and table-repair API endpoints.

## Real AIR COND regression check

Input: preserved immutable `AIR COND.PLANT FINAL PLAN.zip`.

Observed by the recovered detector:

- affected table: `table_index=13`
- collapsed cells: `23`, `44`, `64`
- per-cell numbered-record evidence: 29, 6, and 5 detected monotonic markers
- grouped structural obligations: 1
- compatibility backfill: one `TABLE_ROW_COLLAPSE` human-review route for table 13
- raw table size before repair: 30 rows × 11 columns
- detector does not infer which `C/B/A/UPPER` value belongs to a room row

A repair round-trip was also exercised: save overlay → validate source signature → apply to an in-memory Docling document.

## Automated validation

- `PYTHONPATH=. pytest -q`: **588 passed, 1 skipped**
- `python -m compileall -q app`: **PASS**
- `node --check app/static/*.js`: **PASS**

The recovered package adds `tests/test_table_repair.py` covering:

- long numbered-record collapse detection
- four-step false-positive protection
- one obligation per physical table
- TSV parse/round-trip
- overlay save/application
- stale immutable-source signature rejection
- dismissal deactivation
- Stage-2C freshness change when a table repair changes

## Migration rule

Do **not** rerun Stage 1, Stage 2A, or completed Stage 2B solely because of this R build.

Use the same persistent DB, output directory and processed/result directories. Existing Q-era books are compatibility-scanned. A detected collapsed table blocks current Stage 3 until repaired or explicitly dismissed as a false positive.

## Important recovery note

Because this is a reconstruction from the preserved Q source + R specification, the next implementation chat should treat this recovered R package as the source baseline and keep future diffs/tests against this exact archive.
