# `.40.3` — Sequential pipeline, Machine RAG, Chunk Viewer and UI/UX hardening

Release: `2026.09.18.40.3`

## Purpose

`.40.3` turns the workflow into an explicit dependency chain and removes ambiguity between a finished book, a finished machine corpus, and a RAG-ready machine.

## Sequential pipeline invariant

The supported order is:

`Docling conversion -> Stage 2A -> Stage 2B verification -> Stage 2C overlays -> Stage 3 chunks/index -> Machine embedding index -> RAG retrieval -> optional generation`

A downstream stage is ready only when its saved input signature still matches the current upstream output.

- Stage 2C is blocked while any Stage 2B route is pending, processing or failed.
- Stage 2C stores the signature of the verification results it consumed.
- Stage 3 accepts only a fully completed/current Stage 2C build.
- Stage 3 stores the Stage 2C output signature it consumed.
- A machine embedding records every assigned manual retrieval-index signature and manual type.
- Any upstream change makes every affected downstream derived stage stale.
- The sequencer advances/rebuilds downstream stages in order. It never skips an invalid dependency.

Raw Docling output is still immutable.

## Machine-wise embeddings

Hybrid vectors are now persisted as **one corpus per physical machine/equipment**, not one production vector index per PDF.

A machine may contain description, operation, maintenance, electrical, hydraulic, parts, tools and other manuals. Every embedded row keeps the original manual/job/page/chunk/headings provenance.

Machine embedding readiness requires **all manuals assigned to that machine** to have current Stage 3 retrieval indexes. A missing/stale manual blocks the whole machine rather than silently searching a subset.

Single-book scope remains a lexical/audit surface. Hybrid RAG is machine-scoped.

## Chunk Viewer

New page: `/chunks`

Capabilities:

- search Stage 3 chunks within one machine or one book audit scope;
- search text, headings and chunk IDs;
- page-number filter;
- full chunk text and provenance;
- previous/next chunk navigation;
- source PDF page beside the chunk;
- page switcher for multi-page chunks;
- direct `Inspect/View chunk` links from retrieval results.

## UX changes

- RAG page is presented as **Machine RAG**, with a three-step explanation: select machine, ensure machine embeddings are current, search/generate.
- normal RAG has no all-books scope;
- normal page load requires an explicit machine/book choice; `?job=` deep links remain explicit choices;
- hybrid mode is disabled for scopes that are not ready and single-book audit scope;
- every main page receives a short purpose/next-action guide;
- My Books status now distinguishes verification, finalization, chunking, machine assignment, embedding rebuild and true RAG readiness;
- Book Workflow has a final Machine RAG stage after Stage 3;
- navigation includes Chunk Viewer;
- existing `.40.1` keyboard/focus/skip-link fixes are retained.

## Validation requirements

Release validation includes full pytest, compileall, JavaScript syntax, shell syntax, YAML/Compose parse, asset-version checks, unrestricted-all-books guard checks, and static desktop/mobile rendering checks before and after ZIP extraction.
