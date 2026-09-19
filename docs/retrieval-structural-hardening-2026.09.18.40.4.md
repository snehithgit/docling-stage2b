# Retrieval Structural Hardening — 2026.09.18.40.4

## Goal

Harden retrieval structure without changing the core trust model: immutable Docling source, strict stage ordering, one physical-machine RAG boundary, one explicitly selected generator, and complete source provenance.

## Implemented

### Cross-page table continuity

Derived table evidence can now span an adjacent page boundary when all of these remain true:

- the chunks are consecutive in Stage 3 order;
- the Docling table reference is identical (`#/tables/N`);
- page progression is same page or exactly the next page;
- explicit fragmentation evidence exists.

Canonical `CHK-*` chunks are not rewritten. Reconstructed `TBL-*` evidence records all contributing chunks and page numbers.

### Stage 3 table context anchors

Retrieval metadata now records safe table continuation context such as:

- `table_ref`;
- `table_continuation`;
- `table_header_anchor`;
- `table_header_page`;
- `table_header_distance`;
- `table_header_cells`.

The anchor is bounded by table identity, chunk distance, and same/adjacent page. It is metadata, not a mutation of source text.

### Technical prose signal

Stage 2C's deterministic technical profile now recognizes non-numeric engineering prose such as:

- conditional fault language (`if/when ... fails/trips/does not ...`);
- imperative checks (`check`, `inspect`, `verify`, `measure`, `reset`, `replace`, etc.);
- cause/remedy/troubleshooting language.

This is a preservation/risk signal. It does **not** authorize an LLM to rewrite engineering instructions.

### Verification review priority

Stage 2A calculates deterministic per-page OCR risk and each route receives `review_priority_score` (0–100). Existing `high/medium/low/info` bands remain authoritative; Stage 2B uses the score only to order jobs within a band.

Factors include technical route type, fault/safety/value context, OCR risk, table-cell issues, technical visual classes and decorative-image reduction. The stored metric is explicitly OCR **risk**, not a claimed OCR confidence probability.

### Hybrid retrieval hardening

- Structured-ID protection now weights query identifiers by likely subject role and position rather than treating every identifier equally.
- The existing BGE query vector is reused to compare against cached semantic-intent prototypes (`value`, `procedure`, `troubleshooting`, `definition`, `cross_reference`, `identifier`, `safety`, `diagram`).
- Semantic intent only breaks near-equal fused candidates; it cannot override a clear RRF score gap.
- No extra Pi5/OnePlus/Groq call is made for intent classification.

### Incremental machine embeddings

The production artifact is still **one complete machine embedding corpus**. `.40.4` does not revert to independent production book indexes.

On rebuild:

1. the current complete machine corpus is fingerprinted;
2. unchanged row embeddings are reused when model/prefix/dimension and exact row embedding key match;
3. only changed/new rows are sent to TEI;
4. a complete new machine matrix is assembled;
5. rows/vectors/metadata are atomically replaced;
6. Machine RAG becomes ready only after the complete new index is committed.

Metadata reports `reused_vectors`, `embedded_vectors`, and `incremental_rebuild`.

### Manual revision authority groundwork

Equipment assignments can carry:

- `revision`;
- `revision_date`;
- `authority_status`: `authoritative`, `historical`, or `draft`;
- optional `supersedes_postprocess_job_id`.

Only authoritative/current manuals participate in normal Machine RAG and machine embeddings. Historical and draft manuals remain in the registry for audit. Supersession relation support exists in the API/data model; a dedicated relation picker remains future UI work.

### Equipment-aware benchmark path

Saved expected-source cases can record `expected_equipment_id`. The lexical benchmark respects that machine scope when available. A separate explicit endpoint/UI action runs a **fresh machine-hybrid benchmark** against the configured BGE/TEI service and current machine indexes.

A legacy case without an explicit machine can be mapped only when its expected source belongs unambiguously to one current configured machine. Ambiguous/unassigned cases are skipped, not guessed.

## Strict pipeline remains

`Docling -> 2A -> 2B -> 2C -> Stage 3 canonical chunks -> table/context reconstruction -> retrieval index -> complete machine embedding -> Machine RAG -> optional selected generator`

No downstream stage is considered ready against stale upstream inputs.

## Not claimed by this release

The ChatGPT build environment cannot reach the user's LAN N150 TEI service. Therefore `.40.4` does **not** claim a new fresh N150 hybrid accuracy number. The release includes the runtime path to run it explicitly on the deployed N150. The historical 83.46% Top-1 guarded result remains a deterministic frozen-candidate replay reference only.
