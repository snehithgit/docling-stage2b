# Stage-wise book and Machine RAG workflow

Version: `5.1.2.1`

This document describes the current operator-visible sequence and the backend
readiness contract. Raw Docling conversion output remains immutable; later
corrections and evidence are derived overlays/ledgers.

## Canonical hard sequence

1. **Ingest / conversion** — create the immutable Docling ZIP/JSON.
2. **Stage 2A · Analyze** — deterministic quality, OCR-risk, structural,
   reading-order and routing analysis. Human structural decisions are durable.
3. **Stage 2B · Verify** — run required Text/Vision verification routes.
   The Artifact Sweep is part of this dependency when
   `stage2b_artifact_sweep_required_for_finalize` is enabled. Optional artifact
   rows do not block Stage 2C.
4. **Stage 2C · Finalize** — build the authoritative correction/enrichment
   ledger and overlays from the current Stage 2B generation. Human decisions
   cannot be silently replaced by machine output.
5. **Required human review gates** — unresolved Stage 2A structural findings
   and blocking Verifier Audit findings must be resolved before Stage 3.
   A testing bypass may allow the verifier gate to be crossed, but it never
   converts unresolved evidence into accepted evidence.
6. **Stage 3 · Canonical chunks and retrieval index** — build searchable chunks
   from the current Stage 2C overlay. Any newer upstream semantic state makes
   Stage 3 stale.
7. **Machine assignment + embeddings** — all active manuals assigned to one
   physical machine must have current, review-clean Stage 3 indexes before the
   complete machine embedding corpus is considered ready.
8. **Ask / Machine RAG** — lexical or hybrid retrieval is explicitly scoped to
   one book for inspection or one configured physical machine for production
   hybrid RAG. There is no normal all-books fallback.

No downstream stage is allowed to remain production-ready against a newer
upstream generation or an active blocking human-review gate.

## Optional evidence and diagram enrichment

After Stage 3 exists, **Evidence & diagrams** can be used to improve retrieval
for diagrams, flowcharts, boxed notes and source items that ordinary text
chunking may not represent well.

This is not a blanket mandatory stage. A manual can have valid text search
without every technical image being manually graph-validated.

The evidence contracts are intentionally separate:

- `read_source_rows()` is the broader technical-evidence **candidate** set:
  Stage 3 text/table rows plus technical Stage 2C visual candidates.
- `read_search_rows()` is the narrower **actually searchable** set:
  Stage 3 retrieval rows plus `visual_evidence_index.jsonl` rows admitted to
  RAG.
- Source-coverage reporting uses the searchable set, so an unresolved technical
  picture is not falsely counted as represented in search.
- A human-validated `V-*` diagram record is rebound by exact evidence identity
  before its graph nodes/edges can support RAG generation.
- Active diagram worker identity includes book, evidence entry, picture and job
  kind; two evidence entries sharing the same source picture cannot reuse the
  wrong in-flight result.

Evidence coverage is still a source-reference measurement, not proof that every
semantic fact in the manual has been extracted.

## One pipeline truth

`required_verification_state()` is the canonical Stage 2B dependency
predicate. It handles required/optional artifact work, zero-route clean books,
and fails closed when summary counts claim rows exist but the raw verification
snapshot is temporarily missing.

`resolve_pipeline_stage()` defines the operator-facing order:

`stage2a -> stage2b -> stage2c -> stage2a_human_review -> verifier_audit -> stage3 -> post_stage3`

Machine assignment/embedding/RAG state is projected only from
`post_stage3`. Therefore a physically present old Stage 3 index or embedding
cannot overwrite a newer structural/verifier-review blocker.

## Retrieval safety

A book is `index_ready` only when:

- Stage 3 is current;
- the retrieval index exists; and
- there are zero blocking structural/verifier human-review decisions.

The same gate applies to visual retrieval rows. Machine hybrid readiness requires
every active manual assigned to that machine to satisfy the same current
post-Stage-3 contract. Existing index files are not treated as permission to
search through a newly detected review blocker.

## Revision authority

Each assigned manual may be Current/authoritative, Historical or Draft. Only
active Current manuals belong to the normal machine retrieval/embedding set.
Authority comes from operator knowledge, not filename guesses.

## Incremental machine rebuild

When one current manual changes, unchanged vectors may be reused only when
their exact embedding keys still match. The system constructs a complete new
machine matrix and atomically publishes it after the full corpus is ready.
Machine RAG remains stale/not-ready until that commit completes.

## Table continuity

Cross-page stitching is allowed only when chunks remain consecutive, share the
same Docling table reference and move at most to the adjacent page with
fragmentation evidence. Ambiguous continuations are not silently merged.

## UI ownership and normal operator path

- **Books** — library-level state. Open a book for its single next action.
  It polls the compact `/api/documents/summary` view. The server reuses the
  current snapshot while the EventBroker generation is unchanged and forces a
  bounded periodic rebuild, so idle polling does not repeatedly parse every
  book's ledgers.
- **Book workflow** — canonical per-book sequence and blocker explanation.
  It uses `/api/documents/{job_id}`, so polling one book does not recompute
  every unrelated manual in the library.
- **Processing** — conversion, verification and worker activity.
- **Review** — all required human decisions plus a normal entry point to
  Evidence & diagrams.
- **Evidence & diagrams** — optional post-Stage-3 enrichment, source-coverage
  inspection and validated diagram work.
- **Ask** — machine assignment, embeddings, retrieval and grounded generation.
- **Advanced** — diagnostics, chunk inspection and specialist audit surfaces.

A failed Review/Verifier-Audit status request is displayed as **unknown/error**,
never as zero unresolved items. Backend gates remain authoritative even when a
detail panel cannot load.

## Artifact worker pool

`FULL_TECHNICAL_VISUAL` sweep jobs use the shared eligible worker pool and are
separate from ordinary routed Vision verification. Claims are durable; an
in-flight request is not duplicated. Cooldown/outage handling returns eligible
work to the pool without downgrading evidence or consuming human authority.
