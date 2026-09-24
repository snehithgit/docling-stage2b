# Table Evidence Reconstruction — `.40.3`

Date: 2026-09-18  
Release: `2026.09.18.40.3`

## Why this exists

Docling HybridChunker can split one source table into several Stage 3 chunks before this app sees it. A typical failure is:

- one chunk contains the table labels/header;
- another contains only a separator or empty fragment;
- another contains numeric rows without their labels.

The canonical Stage 3 chunks are valid provenance artifacts, but a natural-language retriever may not be able to connect labels such as `Hoisting capacity Low speed (ton)` with rows such as `36 | 23 | 18 ...` when they are separated.

The crane SWL/capacity audit on page 57 exposed this directly. The existing oversized-table splitter did not help because Docling had already fragmented the table before the app received the Stage 3 rows.

## `.40.3` implementation

A deterministic **Table Evidence Reconstruction** step now runs after canonical Stage 3 chunks are available and before the retrieval index is finalized.

It is derived retrieval evidence only:

`Stage 3 canonical chunks -> table reconstruction -> retrieval_index.jsonl -> machine embedding -> RAG`

Raw Docling and canonical `chunks.jsonl` are never rewritten to hide or merge the original chunks.

### Conservative grouping

Only contiguous chunks are considered when they:

- reference the same Docling `#/tables/N` item;
- are on the same source page;
- have consecutive `chunk_index` values.

The reconstruction extracts the best available column-label row, removes duplicate Markdown separators/header repetitions, and creates bounded retrieval evidence windows with the labels repeated.

### Provenance

Each reconstructed row has a stable ID such as:

`TBL-000011-P0057-001`

and retains:

- `postprocess_job_id`;
- source filename;
- source page;
- `table_ref`;
- `source_chunk_ids`;
- `table_group_chunk_ids`;
- headings;
- original Docling table reference;
- `retrieval_evidence_type = stitched_table`;
- `stitched_table = true`.

The derived rows are also written to `table_evidence.jsonl` for direct audit.

## Label-density safety guard

For table-related canonical chunks, retrieval metadata now measures alphabetic labels versus numeric values using **raw table body text**, not the heading-prefixed display text.

A near-numeric-only fragment with at least four numeric values and very few alphabetic labels receives:

`TABLE_DATA_WITHOUT_HEADER`

and its quality score is reduced by 35 points. This prevents a numeric fragment from looking like a fully trustworthy standalone evidence row simply because Stage 3 headings contain words.

The chunk remains auditable. If safe table reconstruction is possible, the reconstructed row supplies the missing labels for retrieval.

## Near-empty Markdown rows

A canonical Markdown table with a header + separator + an effectively empty row such as `| |` is now treated as `table_header_only`, not as quality-100 table data.

This fixes the fault-table pattern where empty or separator-only fragments survived the old detector.

## Flattened parts/specification tables

Docling sometimes serializes a parts list as space-delimited text instead of a pipe table. `.40.3` conservatively detects generic headers containing the sequence:

`Item -> Qty/Quantity -> Article/Part number -> Description`

and classifies those rows as table-related. This fixes cases such as `LIFTING BLOCK SWL 36 tonnes` being treated as ordinary prose.

## Seven-manual replay

An in-memory replay was run against the current seven processed manuals. No source files were modified.

| Manual | Canonical chunks | Reconstructed table evidence | Label-less table-data warnings | Flattened table rows detected |
|---|---:|---:|---:|---:|
| AD136TI Operation & Maintenance | 785 | 48 | 4 | 0 |
| Anemometer / Anemoscope | 30 | 1 | 0 | 0 |
| Instruction Manual | 1,908 | 133 | 7 | 178 |
| MacGregor Crane Instruction Manual | 1,639 | 100 | 1 | 135 |
| Hydraulics for Mariners | 697 | 9 | 0 | 0 |
| Deck & Hull Electrical Maintenance | 2,511 | 166 | 0 | 0 |
| Engine Room Electrical Maintenance | 4,257 | 247 | 0 | 0 |
| **Total** | **11,827** | **704** | **12** | **313** |

The reconstruction count is intentionally lower than the number of historical `TABLE_HEADER_ONLY` exclusions because only safe same-table/same-page/contiguous groups with recoverable labels are synthesized.

## SWL/capacity regression

After the change:

- the page-57 BASIC DATA capacity table has a labeled reconstructed retrieval row containing both its column names and numeric data;
- the `LIFTING BLOCK SWL 36 tonnes` parts-list row is table-aware instead of prose;
- the original SWL-style query retrieves the direct `LIFTING BLOCK SWL 36 tonnes` source at lexical rank 3 and the reconstructed page-57 hoisting-capacity evidence at rank 4 in the seven-manual replay;
- generation must still preserve context and must not silently equate `lifting block SWL` with every crane operating-capacity row.

## Stage sequencing

This is a downstream derived stage. It does **not** require Docling conversion or Stage 2 verification to be rerun solely because reconstruction logic changes.

When Stage 3 is rebuilt or deterministically refreshed:

1. canonical chunks are produced/refreshed;
2. table evidence is reconstructed;
3. `retrieval_index.jsonl` changes;
4. the affected machine embedding fingerprint becomes stale;
5. machine embeddings rebuild only after the new retrieval index is final.

This preserves the hard pipeline invariant that every downstream stage consumes only completed/current upstream data.

## Frozen 133 regression gate

The conservative reconstruction rule was checked against the existing 133-question priority-manual benchmark using the same expected-manual proxy boundary and the benchmark's source-matching semantics (expected job + Docling refs/pages).

| Metric | `.39` lexical baseline | `.40.3` after table reconstruction |
|---|---:|---:|
| Top-1 | 70.68% | **72.18%** |
| Top-3 | 87.22% | **88.72%** |
| Top-5 | 91.73% | **92.48%** |
| Top-10 | 94.74% | **94.74%** |
| MRR | 0.80072 | **0.80944** |

The first broad reconstruction attempt was rejected because synthetic rows crowded healthy table results. The final rule reconstructs only groups with explicit fragmentation evidence, which preserves the frozen source baseline while improving it slightly.

See `docs/benchmarks/table_reconstruction_lexical_133_2026.09.18.40.3.json`.
