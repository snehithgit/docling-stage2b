# Stage 3 / retrieval quality · 2026.09.14.30

This release moves the project beyond OCR correction into **RAG readiness**. It does not add a generative answer model. The goal is to make Stage 3 chunks safer and then measure whether the right manual passage can actually be retrieved before any LLM is allowed to answer.

## Stage 3 hardening

The `.27` post-validator reduced oversized chunks but could leave split children above the configured 256-token target because children were not validated recursively. `.30` re-validates every safe child and applies only deterministic, source-preserving reductions:

- Markdown separator/padding compaction while preserving `raw_text`.
- Compact handling for broken table fragments without guessing which column an orphan value belongs to.
- Separator-only Markdown noise is removed from retrieval `text` while raw source text is preserved for audit.
- Safe row/paragraph/sentence splitting is recursively rechecked.
- A meaningful table row that still has no safe boundary is retained rather than blindly cut.

`raw_text`, source Docling ZIP/JSON, Stage 2C overlays and provenance remain unchanged.

## Retrieval index

Every Stage 3 build now writes two additional derived artifacts:

- `retrieval_index.jsonl` — only chunks eligible for local retrieval.
- `retrieval_quality.json` — chunk counts, excluded noise, content types, warnings and remaining oversized searchable chunks.

`chunks.jsonl` also carries a `retrieval` block per chunk so an operator can see why a chunk is searchable or excluded.

The index excludes obvious retrieval noise such as table-header-only fragments, separator-only artifacts and strongly repetitive OCR blocks. It does **not** delete these from `chunks.jsonl`; they remain auditable derived chunks.

## Local retrieval quality page

Open `/retrieval` or **RAG quality** in the sidebar.

- **Optimize + index all** re-runs only deterministic Stage 3 post-processing and retrieval indexing on existing Stage 3 outputs. No Docling, Pi5, OnePlus or Groq call is made.
- Search runs a local BM25-style lexical/technical-token ranker across indexed chunks.
- Results show book, page, chunk, score, warnings, source text and Docling item references.
- **Use as expected** saves the selected result as ground truth for that question.
- **Run benchmark** reports Top-1, Top-3, Top-5 and MRR over saved cases.

Benchmark expectations use stable Docling item references when available, so a later chunk rebuild can still match the expected source even if `chunk_id` changes.

## Real `_processed (10)` replay

The deterministic `.30` replay of the seven existing books produced:

- 11,827 final Stage 3 chunks.
- oversized chunks: **627 → 2**.
- oversized **searchable** chunks: **1**.
- 10,320 searchable chunks.
- 1,507 formatting/repetition/noise chunks excluded from the retrieval index but retained in Stage 3 artifacts.
- No model calls and no source mutation.

The remaining meaningful 263-token Engine Room cause/remedy row is intentionally retained because cutting it would risk separating causes from their referenced remedies. The other remaining oversized chunk is strongly repetitive OCR/table noise and is excluded from retrieval.

A seven-query smoke benchmark—one representative technical query per book—returned the expected book at Top-1 for **7/7** cases. This is a smoke check, not a replacement for a user-built benchmark of real troubleshooting questions.

## Safety boundary

`.30` still does not generate technical answers. Retrieval quality is measured independently first:

`corrected manual → safe chunks → retrieval index → search/benchmark → later grounded answer model`

That separation is deliberate: a larger LLM cannot repair a retrieval system that selected the wrong source passage.
