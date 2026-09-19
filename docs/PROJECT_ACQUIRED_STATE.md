## .40.5 acquired state

- Validated `.40.4.2` shared Pi5/OnePlus artifact work-stealing remains the artifact-sweep scheduler.
- `.40.5` source-fidelity and retrieval-integrity code is now part of the project state.
- Uploaded seven-manual processed corpus was audited; 400 automatic applied text corrections existed before `.40.5` replay, and the dry-run left 337 auto-applied while surfacing 64 unsafe/pending legacy entries for safe review.
- The frozen 133 benchmark remains a regression asset, but benchmark references must be validated against the current corpus/version and machine scope.
- Electrical troubleshooting holdout is stored separately from the frozen 133.

# Project acquired state

Last updated: 2026-09-19
Current code release: `2026.09.19.40.4.2`

## Already acquired — do not rebuild unnecessarily

- immutable Docling conversion ZIP/JSON;
- Stage 2A routing/profiling plus deterministic page OCR-risk;
- Stage 2B text/vision verification, full technical-artifact sweep and priority queue;
- Stage 2C correction/enrichment overlays with human precedence and technical-prose signal;
- Stage 3 Docling HybridChunker + canonical chunks;
- derived same/adjacent-page table evidence + table-header anchors;
- `[S#]` text and `[V#]` visual evidence;
- physical-machine registry with multi-manual ownership/types/revision authority;
- TEI CPU + BGE-small-v1.5 selected by N150 benchmark;
- lexical + vector RRF, structured-ID protection and semantic-intent near-tie logic;
- one persisted **machine/equipment embedding corpus**;
- exact-vector reuse for unchanged machine rows during rebuild;
- strict signature-based Stage 2B -> 2C -> Stage 3 -> retrieval -> machine-index freshness;
- automatic downstream sequencer;
- Chunk Viewer/source-page inspection;
- frozen 133-question benchmark and historical benchmark/replay records;
- canonical user-confirmed `mobile/oneplus-llama-control` script.

## Important invariants

- Do not rerun Docling merely because retrieval/embedding code changes.
- Do not search an incomplete subset of a configured machine and call it machine-ready.
- If verification changes, all affected downstream derived artifacts become stale in sequence.
- Hybrid RAG is machine-scoped; a single book is lexical/audit only.
- Historical/draft manual revisions do not participate in normal Machine RAG.
- No normal all-books RAG exists.
- Reconstructed/derived rows always retain source manual/job/page/chunk/table provenance.
- Incremental embedding means vector **reuse**, not separate production manual indexes.

## Embedding asset already acquired

- runtime: `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9`
- model: `BAAI/bge-small-en-v1.5`
- dimensions: 384
- N150 benchmark memory: about 277 MiB for embedding runtime in earlier tests.

Do not reacquire another embedding model unless a controlled benchmark shows a material benefit.

## Data not required to reacquire

Unless files are actually missing, do not request/recreate raw PDFs, converted ZIPs, Stage 2A outputs, completed verification output, Stage 2C ledgers, Stage 3 chunks, visual evidence, the frozen 133 benchmark, or the OnePlus control script.

The mapping of manuals/revisions to the real physical machine remains operator knowledge. Never infer ownership or authoritative revision solely from similar filenames/manufacturer/topic.
