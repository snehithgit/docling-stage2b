# Marine Pipeline Studio — 2026.09.28.40.11V validation

## Scope

40.11V adds exact navigation from existing human/verifier review surfaces into the 40.11U Docling Page Review workspace. The goal is to let a reviewer who is already looking at a suspicious text block, table cell, artifact or vision result jump directly to that same immutable Docling source object on the original PDF page with its bbox selected.

## Implemented navigation

The following review surfaces now expose **Open Docling PDF bbox**:

- Text Audit (`/text-audit`)
- full Human Text Review (`/review`)
- Artifact Audit (`/artifact-audit`)
- Vision Audit (`/vision-audit`)

Each link carries `job`, `page`, and an exact immutable Docling reference when available:

- text block → `#/texts/<source_index>`
- table-cell correction → parent `#/tables/<table_index>`
- artifact / visual verification → `#/pictures/<picture_index>`

Table-cell review intentionally targets the owning table object because the Page Review repair ledger works at Docling object/bbox level rather than inventing a separate mutable cell object. The existing text verifier crop remains available for cell-level evidence.

## Docling Page Review deep-link behavior

`/docling-review` now accepts `ref=<Docling self_ref>`. After the requested PDF page loads, the UI:

1. finds the matching object in the immutable page inventory;
2. selects it in the repair inspector;
3. renders the selected/editable bbox state;
4. scrolls the PDF viewport so the box is centered;
5. applies a temporary visible highlight so the reviewer can immediately locate the source object.

If the referenced Docling item exists but does not have a usable bbox, the page explicitly reports that condition and leaves the reviewer on the correct PDF page so a replacement region can be drawn. It does not fabricate geometry.

The review link also supplies a same-origin `return` path. Page Review accepts only paths beginning with `/` and rejects protocol-relative targets, then changes its back link to **Back to review**. This avoids an open-redirect style navigation path while preserving the reviewer workflow.

## Pipeline impact

This release changes navigation/UI behavior only. It does not change:

- converted Docling ZIP contents;
- Stage 2A diagnostics or routes;
- Stage 2B verifier data;
- Stage 2C correction semantics/rule version;
- `docling_page_repairs.json` schema/rule;
- Stage 3 rule version (`stage3-canonical-integrity-v5`);
- retrieval/index rules.

Therefore upgrading 40.11U → 40.11V does **not** require a Stage 2A, Stage 2B, Stage 2C or Stage 3 rerun solely because of this release. Existing processed data is used as-is.

## Validation

Executed against the 40.11V source tree:

```text
PYTHONPATH=. pytest -q tests/test_static_ui.py tests/test_release_version_sync.py
65 passed

PYTHONPATH=. pytest -q
604 passed, 1 skipped

python -m compileall -q app tests
PASS

node --check app/static/*.js
PASS (all frontend JavaScript files)
```

Static regression coverage verifies direct Docling links on all four review surfaces, text/table/picture ref mapping, Page Review `ref` handling, automatic selection/highlighting, and the return-to-review path.
