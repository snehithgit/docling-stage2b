# Equipment-scoped retrieval foundation — 2026.09.18.39

## Goal

Make the physical equipment/machine the normal RAG boundary instead of the PDF file or the full library.

One equipment may have one or many manuals, for example:

- description
- operation
- maintenance
- electrical
- hydraulic
- parts
- tools
- service
- installation

The manuals stay separate and keep their original provenance. They are only grouped into one logical retrieval scope.

## Why

The 133-question priority benchmark showed that simultaneous retrieval across unrelated manuals creates avoidable wrong-book competition, especially between similar crane manuals. In real troubleshooting the operator normally already knows the equipment being worked on, so retrieval should use that known context rather than rediscovering it from every question.

## Phase 1 implementation

`.39` adds an equipment registry stored at:

`<processed_dir>/equipment_registry.json`

Schema:

`docling-equipment-registry/v1`

Each equipment group records:

- stable `equipment_id`
- equipment name
- optional manufacturer
- optional model
- optional notes
- one or more assigned Stage 3 manuals
- manual type for every assigned manual

A manual can belong to only one equipment scope at a time. This prevents accidental cross-equipment contamination.

## Retrieval modes

### Equipment scope — recommended/default when configured

Searches only manuals assigned to the selected equipment.

Text `[S#]` and visual `[V#]` evidence may come from different manuals inside the same equipment group.

Example:

`Crane No. 1 -> Description + Operation + Maintenance + Electrical + Hydraulic + Parts + Tools`

### Single-book scope

Searches exactly one manual. This remains useful for audit or when the operator intentionally wants one document only.

### All-books diagnostic scope

Retained for regression/debug/comparison. This is not the recommended troubleshooting mode because unrelated equipment can compete.

## Generation safety

When equipment scope is selected, answer generation can combine evidence from multiple manuals only when all evidence belongs to the selected equipment group.

The generator cannot silently borrow a result from another equipment group.

Legacy single-book behavior is unchanged outside equipment scope.

## UI

The RAG quality page now contains:

- an Equipment/Book/All scope selector;
- Equipment groups manager;
- manual-type assignment;
- create/edit/delete equipment grouping;
- equipment scope selected by default when at least one group exists;
- explicit warning when using all-books diagnostic mode.

Deleting an equipment group only removes the grouping metadata. It does not delete the source book, Stage 3 chunks, Stage 2C ledger, visual evidence, or raw Docling output.

## No embeddings in Phase 1

`.39` intentionally does not add vector dependencies yet. The equipment boundary is established and benchmarked first. Hybrid lexical + embedding retrieval is Phase 2 so its effect can be measured cleanly against the same fixed benchmark.

## Raw-source guarantees

- Raw Docling output remains immutable.
- Existing Stage 2C overlays remain unchanged.
- Existing Stage 3 per-book indexes remain valid.
- Existing `[V#]` visual evidence remains valid.
- No Docling or vision rerun is required.
