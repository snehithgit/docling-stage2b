# Marine Pipeline Studio — 2026.09.28.40.11U validation

## Scope

40.11U adds a dedicated human-assisted Docling page reconstruction workspace on top of 40.11T. Its purpose is to let an operator compare Docling geometry/content against the original PDF page, repair a bad bbox or draw a region that Docling missed, re-read that exact source crop with the already-configured local model roles, and approve a downstream overlay without modifying the converted Docling source.

## Implemented workflow

### Page-level Docling inspection

A new `/docling-review?job=<id>&page=<n>` workspace is linked from the Stage 2A book card. It provides:

- original PDF page rendering;
- Docling text, heading, table and picture bboxes overlaid on the page;
- per-type visibility controls;
- a coverage view intended to make unboxed source content visually apparent;
- page navigation and zoom;
- selection of existing Docling regions;
- drag-to-move and corner-resize of the selected bbox;
- drawing a new missing paragraph/heading/table bbox.

The page API is backed by immutable converted Docling JSON plus the original PDF. Bboxes are normalized to a top-left 0..1 UI coordinate system and converted back to Docling page coordinates only when an approved overlay is persisted.

### Exact source crop and model re-extraction

The selected bbox can be rendered as an exact crop from the original PDF. `/docling-review/reextract` then sends that crop through the normal Stage 2B model-selection/locking infrastructure rather than introducing a separate cloud/API path.

- Paragraph/heading: transcribe all and only visible crop text; do not summarize, normalize or infer missing words.
- Table: reconstruct visible physical rows as TSV; preserve visible blanks, values, units and row order; do not guess cells.
- Existing picture: literal visible-text transcription is available for inspection, but picture semantics are not inferred by this path.

`auto` uses the configured text-role processor for text/heading/picture crops and the configured vision-role processor for table crops. Pi5/OnePlus can also be selected explicitly. The helper shares the existing device locks/governor so interactive review does not race normal verifier work.

Re-extraction output is not authoritative by itself. It is placed in an editable review field and must be compared with the source crop and explicitly approved by the human reviewer.

### Table reconstruction

For table regions the proposal is TSV and is rendered as an editable grid preview. The reviewer sets how many leading rows are column headers (including zero). When an existing Docling table is approved with a reconstructed matrix, the matrix is rebuilt only in the Stage 3 working copy. The reviewer-approved physical bbox is then restored over that reconstructed table.

If a new human-drawn table is approved, it is represented as a provenance-rich dedicated Stage 3 table chunk rather than being spliced blindly into Docling's internal body graph.

If a new region is marked `picture`, approval fails closed in 40.11U. A missing picture needs a separate visual-enrichment representation; this release does not invent one merely to make the UI permissive. Existing picture bboxes can still be adjusted.

Existing Docling object classes are also fixed in v1: an existing text region cannot be silently reclassified as a table/picture, and an existing table/picture cannot be converted into another object class. A newly drawn missing region is the safe path when a different object type is needed.

### Immutable repair ledger

Approved changes are written to `docling_page_repairs.json` using schema `docling-page-repairs/v1` and rule `docling-page-review-v1`. Each repair records, as applicable:

- repair ID and applied/inactive/superseded state;
- source page and region type;
- original Docling ref/index;
- source-item signature;
- source converted-ZIP SHA when available;
- original bbox and reviewer-approved bbox;
- original/proposed text;
- reconstructed table matrix and header-row count;
- model/extraction metadata;
- reviewer note and approval timestamp;
- explicit `human_verified=true` and `raw_docling_immutable=true` markers.

Only active, human-verified repairs whose source identity still matches are used. If the immutable Docling item or recorded converted source changes, the old repair is not silently applied.

### Stage 3 integration

Stage 2A and Stage 2B are not invalidated by a page repair. `docling_page_repairs.json` is included in the downstream output signature used by Stage 3 freshness, so saving or deactivating a repair makes existing canonical chunks stale.

Stage 3 rule version is now `stage3-canonical-integrity-v5`.

For existing Docling refs, approved page repairs are applied to an in-memory copy after existing Stage 2C and table-structure overlays. Whole-table page repairs are checked so they cannot silently discard an existing human-approved table-cell correction. For human-drawn missing paragraph/heading/table regions, Stage 3 emits dedicated derived chunks with `repair://<repair-id>` provenance, page number, bbox and `human_bbox_source_reconstruction` metadata.

The immutable converted Docling ZIP is never rewritten.

## Validation

Executed against the 40.11U source tree:

```text
PYTHONPATH=. pytest -q tests/test_docling_review.py tests/test_pipeline_state.py tests/test_stage3.py tests/test_static_ui.py
108 passed

PYTHONPATH=. pytest -q
603 passed, 1 skipped

python -m compileall -q app
PASS

node --check app/static/*.js
PASS (all frontend JavaScript files)
```

Regression coverage includes normalized Docling/PDF bbox conversion, immutable-source behavior, existing text repair, table matrix+bbox reconstruction, explicit zero-header tables, missing-region Stage 3 chunks, rejection of content-less tables and unsafe new pictures, stale source-item protection, Stage 3 freshness signatures, and static UI/API contracts.

## Deployment / compatibility

Existing 40.11T processed books can be upgraded in place with the same database, input/output/processed directories and converted ZIPs. No Stage 2A or Stage 2B rerun is required solely to use the page-review workspace. After an approved repair, rebuild Stage 3 for that book so chunks/retrieval reflect the overlay.

Because the sandbox used for this build cannot access the user's live Pi5/OnePlus endpoints, the model re-extraction transport is validated by source/test contracts rather than a live hardware inference run. The deployed app should therefore be smoke-tested once against the configured local processors before using reconstructed content operationally.

## Deliberately deferred

40.11U is the first operational slice, not the final graphical editor. It does not yet provide arbitrary bbox merge/split operations, manual table cell-border drawing, automatic OCR-gap detection for scanned pages, or creation of entirely new picture/diagram evidence objects. Those can be added on top of the repair ledger without changing raw Docling.
