## .40.6 completed — Verification status breakdown

- [x] Separate Text verification, normal Vision verification, and full technical Artifact sweep counts in the per-book Verification table.
- [x] Show completed/total plus pending/processing/failed for each logical work type.
- [x] Show exact Text / Vision / Artifact pending counts beside **Verify book**.
- [x] Preserve legacy worker-lane counters and shared artifact work-stealing behavior.

## .40.5.1 completed — Convert queue renderer hotfix

- Restored Recent documents rendering on the Convert / Folder watcher page.
- Removed the fatal assumption that the legacy `#failed-nav` sidebar badge always exists.
- Applied the same optional-element guard to Manual Convert and Errors pages.
- Added regression tests covering the stale-DOM-reference class of failure.

## .40.5 completed — Source Fidelity & Retrieval Integrity

- [x] Block automatic Stage 2C source replacement when critical technical tokens change.
- [x] Block automatic corrections that drop troubleshooting/remedy actions.
- [x] Detect source contraction/incomplete transcription.
- [x] Deterministically revalidate legacy unreviewed applied corrections without model calls.
- [x] Preserve human-verified corrections as highest authority.
- [x] Version Stage 2C and retrieval rules and propagate staleness downstream.
- [x] Refresh retrieval artifacts from canonical chunks without rewriting Stage 3.
- [x] Audit/repair unambiguous persisted job-identity metadata.
- [x] Remove benchmark all-books fallback in machine-scoped mode.
- [x] Diversify duplicate synthetic table evidence in Top-K.
- [x] Add targeted maintenance/value/parts-item/run-in ranking tests.
- [x] Add 12-case electrical troubleshooting holdout.

# Project completed implementation

Last updated: 2026-09-19  
Current release: `2026.09.19.40.6`

Do not redesign/reacquire these items without a specific regression or requirement.

## Core pipeline

- [x] Immutable Docling conversion artifacts.
- [x] Stage 2A deterministic profiling/routing.
- [x] Deterministic page OCR-risk signal.
- [x] Stage 2B text/vision verification with explicit provider selection.
- [x] Coarse route priority plus deterministic 0–100 review-priority ordering inside each band.
- [x] Stage 2C correction/enrichment overlays with human precedence.
- [x] Stage 2C numeric/identifier technical profiling plus conditional/imperative troubleshooting prose signals.
- [x] Stage 3 Docling HybridChunker + deterministic validators.
- [x] Strict Stage 2B -> 2C -> Stage 3 -> retrieval -> machine-index freshness chain.
- [x] Failed/incomplete/stale upstream work blocks downstream readiness.

## Retrieval structure

- [x] Deterministic lexical technical retrieval.
- [x] Table label-density and `TABLE_DATA_WITHOUT_HEADER` safety.
- [x] Flattened parts/spec table recognition.
- [x] Conservative `TBL-*` reconstruction without mutating canonical chunks.
- [x] Same-page and adjacent-page reconstruction when chunks are contiguous and share one Docling table ref.
- [x] Derived table-header anchor/continuation metadata.
- [x] `table_evidence.jsonl` audit artifact with source chunk/page/table provenance.
- [x] Chunk Viewer + original PDF page inspection.
- [x] `[S#]` text and `[V#]` visual evidence.

## Machine RAG / hybrid

- [x] Physical-machine registry with 1..N manuals and unique ownership.
- [x] No normal unrestricted all-books RAG.
- [x] TEI CPU 1.9 + BGE-small-v1.5 chosen by N150 benchmark.
- [x] One persisted production embedding corpus per physical machine.
- [x] Lexical + BGE RRF.
- [x] Structured-ID guard with subject/position weighting.
- [x] Cached BGE semantic-intent prototypes as a near-tie signal; no extra LLM query.
- [x] Incremental reuse of unchanged vectors while still atomically publishing one complete machine index.
- [x] Machine stays not-ready until the complete replacement index is committed.
- [x] Generation may combine multiple current manuals only inside the explicitly selected machine.
- [x] Exactly one selected Pi5/OnePlus/Groq generator; no silent fallback.

## Manual revisions

- [x] Revision/revision-date/authority metadata in equipment assignments.
- [x] Current/authoritative manuals included in machine RAG.
- [x] Historical/draft manuals retained but excluded from normal machine RAG.
- [x] Supersedes relation supported in data/API groundwork.

## Benchmark and audit

- [x] Frozen 133-question source benchmark retained.
- [x] Saved benchmark cases can carry expected machine ID.
- [x] Lexical benchmark can respect expected machine scope.
- [x] Explicit fresh machine-hybrid benchmark endpoint/UI using the deployed BGE/TEI runtime.
- [x] SWL/table, cross-page table, prose-technical, priority-order, revision and incremental-vector regression tests.

## UI / accessibility

- [x] Machine RAG setup/readiness/search guidance.
- [x] Current/Historical/Draft revision controls and explanatory copy.
- [x] Hybrid build feedback reports vector reuse vs new embedding work.
- [x] Retrieval result audit shows exact-ID protection and semantic intent when present.
- [x] Modal/nav focus traps, focus restoration and skip links retained.

## `.40.4.1` — Vision provider routing hotfix

- Full technical-artifact sweeps now obey the selected Vision processor.
- Legacy physical-device processor hints no longer override the operator selection.
- Artifact Audit no longer advertises round-robin Pi5/OnePlus execution.
- 399/399 source tests pass.

## `.40.4.2` — shared artifact work stealing

- Full technical-artifact sweeps use one atomic shared Pi5 + OnePlus pending pool.
- Each device claims work only when idle; there is no fixed ratio.
- Per-device artifact cooldown prevents an offline/hanging worker from repeatedly consuming new rows after a transport/server failure.
- Retries can move from OnePlus to Pi5 or from Pi5 to OnePlus.
- Manual device Pause is respected by artifact Start/Retry.
- Normal Vision provider selection remains unchanged and has no fallback.
- 403/403 source tests pass.
