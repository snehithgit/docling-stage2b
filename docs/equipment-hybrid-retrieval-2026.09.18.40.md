# Equipment-scoped BGE hybrid retrieval — 2026.09.18.40

## Production retrieval

`.40` adds local CPU semantic retrieval while preserving the `.39` equipment boundary.

Flow:

`selected equipment -> lexical candidates + BGE vector candidates -> RRF -> structured-ID guard -> [S#] text evidence + scoped [V#] evidence -> selected generator`

Pinned embedding runtime/model:

- image: `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9`
- model: `BAAI/bge-small-en-v1.5`
- dimensions: 384
- BGE query prefix: `Represent this sentence for searching relevant passages: `
- RRF candidate depth: 60
- RRF k: 60

Document vectors are persisted beside each book's Stage 3 retrieval index under `embedding_index/BAAI_bge-small-en-v1.5/`. This means a manual is embedded once even when an equipment contains several manuals.

## Scope and safety

- equipment scope searches only manuals assigned to that physical equipment;
- single-book scope remains available;
- all-books search remains diagnostic;
- raw Docling, Stage 2C overlays and Stage 3 chunks are not rewritten;
- vector rebuilds do not rerun Docling or vision;
- no automatic embedding/model fallback is used;
- Lexical only is an explicit operator-selected diagnostic mode.

## Structured-ID guard

After RRF, a generic deterministic guard protects exact structured technical identifiers such as valve IDs, alarm codes, cable/article identifiers and equipment codes when lexical rank 1 contains all of the structured IDs but semantic fusion demotes it.

Plain engineering numbers such as `25 bar` are deliberately not treated as structured IDs by this guard.

## N150 Phase 2A benchmark basis

Frozen 133-question scoped benchmark:

- lexical: Top-1 70.68%, Top-3 87.22%, Top-5 91.73%, Top-10 94.74%, MRR 0.80072
- BGE vector-only: Top-1 63.16%
- BGE raw RRF hybrid: Top-1 79.70%, Top-3 90.98%, Top-5 96.24%, Top-10 97.74%, MRR 0.86456

BGE raw hybrid fixed 19 lexical Top-1 misses and regressed 7 lexical Top-1 hits. The structured-ID guard is intended to recover exact-ID regressions without weakening semantic recall.

## UI/API

RAG Quality now exposes:

- Hybrid or Lexical retrieval mode;
- Hybrid index count;
- `Build hybrid for scope` action;
- per-book/equipment hybrid readiness;
- explicit errors if the embedding service/index is unavailable in Hybrid mode.

New API:

- `POST /api/retrieval/hybrid-index`

Existing search/generation/prompt endpoints accept `retrieval_mode: hybrid|lexical`.
