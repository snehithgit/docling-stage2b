# Mixed text + visual RAG evidence validation — 2026-10-02

## User-visible failure

Machine-scoped hybrid retrieval displayed both Stage-3 text results and resolved visual evidence, but answer generation could receive only `[V#]` visual records. This produced an incorrect “not enough information” response even when the retrieved text contained the answer-bearing engineering description.

## Root cause

Equipment generation used the current postprocess job IDs as a hard scope boundary. Some deterministic Stage-3 retrieval-index refresh paths could derive `postprocess_job_id` from the historical `__jobNN` portion of the result-directory name. After reset/reuse, that embedded ID could differ from the current database job ID.

Visual evidence is refreshed with the current database job ID, so the failure could present exactly as:

- text `[S#]` rows retrieved and displayed successfully,
- text rows rejected by the generation scope gate because of stale numeric provenance,
- visual `[V#]` rows accepted,
- generator receives a V-only packet.

## Fix

Source commit: `e9da1d8bd72f4344fdcc87ea74e5c1f028a6fd8e`

- Equipment-scoped generation now receives the authoritative selected book records.
- Text/visual rows are reconciled only against the selected machine using current job ID, exact result-directory identity, or a unique source filename.
- Reconciled rows are normalized to the current book provenance before `[S#]`/`[V#]` evidence allocation.
- If text was retrieved but cannot be safely reconciled to the selected machine, generation fails closed with `text_provenance_mismatch` instead of silently falling back to V-only evidence.
- Single-book generation remains single-book scoped; book metadata alone does not activate equipment scope.
- Retrieval-only reindex now accepts the authoritative current postprocess job ID and source filename.
- Deterministic Stage-3 maintenance prefers persisted Stage-3 job identity before the legacy directory-name fallback.

## Regression coverage

Added a regression reproducing the observed shape:

- stale Stage-3 text job ID,
- current machine/visual job ID,
- same selected Fire Alarm result directory,
- expected mixed evidence contains both `[S1]` and `[V1]`,
- `[S1]` provenance is normalized to the current job ID and retains the answer-bearing text.

Added fail-closed coverage proving an unrelated text row cannot be bypassed by sending visual-only evidence.

Focused RAG/retrieval regression suite passed. A complete-suite baseline-versus-after comparison also passed: the patch introduced no new full-suite failures; the existing unrelated embedding-compatibility/pipeline-state failures were unchanged.

## Expected runtime behavior

When both relevant text and visual evidence are available and `max_sources=5`, generation normally reserves text evidence and may include up to two visual records. The final prompt should therefore show `[S#]` and `[V#]` together rather than silently dropping all text.

This validation commit intentionally triggers the normal Docker publish workflow so `ghcr.io/snehithgit/docling-stage2b:latest` is rebuilt from the fixed source tree.
