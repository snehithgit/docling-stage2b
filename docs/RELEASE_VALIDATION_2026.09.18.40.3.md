# Release validation — 2026.09.18.40.3

Release: `2026.09.18.40.3`
Date: 2026-09-18

## Validation gates

- Full pytest suite: **384 passed**.
- Python compileall: PASS.
- Static JavaScript syntax: PASS.
- Shell syntax: PASS.
- YAML / Docker Compose parse: PASS.
- Benchmark JSON parse: PASS.
- Mandatory tracker/completed/TODO/acquired/next-phase/handoff/workflow documents: PASS.
- ZIP integrity: PASS.
- Clean extraction: PASS.
- Repeat validation on extracted package: PASS.

## Architecture verified

- Strict sequential dependency chain: Docling -> Stage 2A -> Stage 2B -> Stage 2C -> Stage 3 -> table evidence reconstruction -> retrieval index -> machine embedding -> RAG.
- Failed or incomplete verification blocks Stage 2C.
- Stale upstream signatures invalidate downstream stages.
- Production hybrid embeddings are persisted per physical machine/equipment, not per PDF.
- All assigned manuals must be current before a machine is fully RAG-ready.
- Canonical Stage 3 chunks stay unchanged; table reconstruction produces derived auditable retrieval evidence.
- No normal unrestricted all-books RAG path.
- Chunk Viewer and source-page provenance are included.

## Retrieval-quality checks

- Seven-manual replay: 704 conservative reconstructed table-evidence windows.
- Frozen 133 lexical source benchmark after table reconstruction: Top-1 **72.18%**, Top-3 **88.72%**, Top-5 **92.48%**, Top-10 **94.74%**, MRR **0.80944**.
- SWL/spec regression coverage included.

The final SHA-256 is intentionally reported with the distributed artifact rather than embedded in this file because modifying the archive to include its own checksum would change that checksum.
