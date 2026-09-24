# Retrieval scope safety hotfix — 2026.09.18.40.2

## Purpose

Remove the remaining global all-books retrieval path from normal RAG use and make hybrid readiness proactive per selected scope.

## Changes

- Removed `All books · diagnostic` from the RAG scope selector.
- Normal RAG page now starts with `Choose equipment or book…`; no equipment/book is silently auto-selected.
- `/retrieval?job=<id>` remains the only automatic scope selection because it is an explicit book deep link.
- Search, answer generation, prompt export, and hybrid-index build now require **exactly one** scope at API validation level: one book or one equipment.
- Internal retrieval code also rejects an unscoped call; there is no `all_books` fallback.
- Hybrid index build can no longer embed the entire corpus from a null scope.
- Hybrid mode is disabled for a selected scope until that scope's vector index is ready.
- Selecting a scope without a hybrid index switches to `Lexical only` and shows a visible instruction to build the hybrid index.
- Static Hybrid label is now `Hybrid · loading model…` until status returns the configured model.
- Search and hybrid-build buttons start disabled and stay disabled until a valid searchable scope is selected.
- An equipment scope is considered searchable only when **all assigned manuals** have their Stage 3 text retrieval index ready, preventing silent partial-equipment search.

## Preserved behavior

- One equipment may still contain multiple manuals; this is intentional equipment-scoped retrieval, not global multibook search.
- Single-book retrieval remains supported.
- Same-book follow-reference remains supported.
- Fixed retrieval benchmark infrastructure is unchanged.
- Raw Docling data, Stage 2/3 overlays, vector files, and generation providers are unchanged.

## Regression coverage

Added tests for missing/both-scope rejection, valid book/equipment scopes, internal no-all-books fallback, explicit scope UI, proactive hybrid readiness, no all-books option, and all-manual text-index readiness for equipment scopes.
