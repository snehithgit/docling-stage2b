# Marine Pipeline Studio — 2026.09.28.40.11T validation

## Scope

40.11T is a structural-review UI/workflow repair on top of 40.11S. It addresses the broken review layout shown after deploying S and removes the remaining blind Stage 2A structural Accept/Dismiss paths.

## Root causes confirmed

1. The standalone `reading-order-review.html` and `table-repair.html` pages used the normal `.app-shell` grid while omitting the global sidebar. Their only `<main>` therefore occupied the sidebar-width grid column.
2. Those review pages also reused `.stage-card`, whose 58px + content grid is intended only for workflow step cards. Ordinary review-page children were consequently distributed into a narrow first column.
3. The Book workflow still exposed direct Accept/Dismiss actions for Stage 2A structural findings other than reading order and collapsed-table repair, so geometry/table-grid/heading/archive/graph findings could still be resolved without viewing their persisted evidence.

## Implemented behavior

### Shared review workspace

- Standalone structural review pages use `review-workspace-shell` and `review-workspace-card`, independent of the sidebar and workflow-step grid.
- Source pages and diagnostic evidence use a responsive two-column workspace at desktop widths and a single-column layout on smaller screens.
- `reading-order-review.html` and `table-repair.html` were migrated to that layout.

### Evidence-first routing

The Book workflow now has no direct Stage 2A structural Accept/Dismiss buttons.

- `TABLE_ROW_COLLAPSE` -> dedicated **Review / repair** workspace.
- `READING_ORDER_ANOMALY` -> dedicated **Review anomaly** workspace.
- `TABLE_GRID_ANOMALY` -> generic **Review finding** workspace.
- `DOCLING_GEOMETRY_ANOMALY` -> generic **Review finding** workspace.
- `HEADING_HIERARCHY_INCONSISTENCY` -> generic **Review finding** workspace.
- `ARCHIVE_ARTIFACT_MISSING` -> generic **Review finding** workspace.
- `DOCLING_GRAPH_INTEGRITY` -> generic **Review finding** workspace.

### Server-side evidence gates

UI gating is not trusted as the sole safety control.

- Generic structural decisions require every persisted evidence ID to be submitted as reviewed.
- Reading-order decisions continue to require every flagged page to be submitted as reviewed.
- A collapsed-table false-positive dismissal requires an explicit source-page review marker.
- Saving a table repair requires `source_reviewed=true`.
- Missing diagnostic evidence fails closed; the route stays unresolved.

### Existing R/S processed data

Detailed Stage 2A evidence is recovered from the existing `diagnostics.json` associated with each result directory. Existing R/S books therefore do not need Stage 2A or Stage 2B rerun solely for this review UI upgrade. If an old result genuinely lacks the needed diagnostic evidence, the route cannot be blindly accepted; it remains unresolved.

Raw Docling output remains immutable. No technical row/value/model relationship is guessed by this release.

## Validation

Executed against the 40.11T source tree:

```text
PYTHONPATH=. pytest -q tests/test_pipeline_state.py tests/test_static_ui.py
78 passed

PYTHONPATH=. pytest -q
592 passed, 1 skipped

python -m compileall -q app tests
PASS

node --check app/static/*.js
PASS (all frontend JavaScript files)
```

Static regression coverage verifies that the three standalone structural workspaces use the full-width review shell and do not use `.stage-card`, and that the Book workflow no longer exposes direct structural-decision controls.

## Deliberately not changed

- No Stage 2A detector thresholds or anomaly algorithms were changed.
- No Stage 2B verifier behavior was changed.
- No Stage 2C or Stage 3 rule version was changed.
- No retrieval ranking/index logic was changed.
- 40.11T does not auto-repair a genuine reading-order, geometry, graph, or table-grid defect. Such a finding should remain unresolved until a safe repair workflow exists.
