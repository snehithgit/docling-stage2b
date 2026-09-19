# Retrieval Quality Audit

## Trigger case

Query: *"what is the swl value of this crane safe working load"* (scope: Deck Crane, "Instruction Manual")
Result: generator correctly refused, saying no SWL numeric value was found in the retrieved sources — even though the manual contains one.

## Root cause

The crane's actual capacity data (page 57, "BASIC DATA" table) exists in the corpus but is split across three orphaned chunks by Docling's HybridChunker, upstream of this app's own chunking safeguards:

| Chunk | Page | Content |
|---|---|---|
| `CHK-000167` | 57 | Header only: `Hoisting capacity Low speed (ton) \| Hoisting speed Low (m/min) \| ...` — zero data |
| `CHK-000168` | 57 | Near-empty continuation fragment: `BASIC DATA\n\|  \|` |
| `CHK-000169` | 57 | The real data row: `36 \| 23 \| 18 \| 36 \| 4.0 \| 28 \| 65 \| 0.8 \| 28.8 \| ...` — no column labels attached |

No single chunk contains both the numeric SWL value and enough label/vocabulary to be matched by a natural-language query. `CHK-000169` scores `quality_score: 100, eligible: true` in the pipeline's own metadata — it passes every existing check but is semantically empty (pure numbers, nothing for lexical or vector search to match against "safe working load").

A second, related bug: a genuine SWL value elsewhere in the manual (`CHK-001457`, page 448, "LIFTING BLOCK SWL 36 tonnes", a parts-list row) is misclassified as `content_type: "prose"` / `table_related: false`, so any table-aware ranking logic would miss it.

### Why the existing safeguard didn't catch this

`app/stage3.py::_split_oversized_markdown_table` already repeats table headers when *this app* splits an oversized table (`stage3_table_split_repeat_header: true`). But it only fires when `parent_tokens > max_tokens`. Chunk `CHK-000167` is only 114 tokens — well under the 256 budget — because the split happened **inside Docling's own HybridChunker**, before Stage 3 ever saw one oversized chunk to repair. The existing header-repeat logic never gets a chance to run against splits Docling itself produces.

## Corpus-wide audit (all 7 processed manuals)

The same pattern was found everywhere, at meaningfully larger scale in the electrical fault-finding manuals:

| Manual | Total chunks | Orphaned table headers (dropped) | Orphaned label-less data rows (kept) |
|---|---|---|---|
| Engine Room Electrical Maintenance | 4,257 | **836** | — |
| Deck & Hull Electrical Maintenance | 2,511 | **363** | — |
| Instruction Manual (crane) | 1,908 | 39 | 5 |
| MacGregor Crane Instruction Manual | 1,639 | 40 | 0 |
| AD136TI Operation & Maintenance | 785 | 21 | 4 |
| Hydraulics for Mariners | 697 | 5 | 0 |
| Anemometer / Anemoscope | 30 | 0 | 0 |

Roughly **1,300 chunks corpus-wide** are orphaned table headers whose paired data rows lost their column semantics. In the two electrical manuals this is up to ~20% of all chunks.

Traced example (Engine Room Electrical, page 69): `CHK-000267` is a "Probable Cause | Probable Cause | Remedies" header with no data; the next chunk, `CHK-000271`, is a garbled sentence fragment — `"that is 85% or more to the UVT coil. Check the"` — showing Docling's table-structure recognizer is fragmenting merged/multi-line cells in these fault-finding tables, not just splitting on token budget.

### Exclusion reasons, all manuals (chunks marked `eligible: false`)

| Manual | TABLE_HEADER_ONLY | REPETITIVE_OCR_BLOCK | TOO_LITTLE_SEARCHABLE_TEXT | TABLE_FRAGMENT | OVERSIZED_CHUNK |
|---|---|---|---|---|---|
| Engine Room Electrical | 836 | 10 | 108 | 0 | 0 |
| Deck & Hull Electrical | 363 | 2 | 51 | 0 | 0 |
| Instruction Manual (crane) | 39 | 43 | 2 | 2 | 1 |
| MacGregor Crane | 40 | 8 | 9 | 0 | 0 |
| AD136TI | 21 | 17 | 6 | 0 | 0 |
| Hydraulics for Mariners | 5 | 1 | 0 | 0 | 0 |
| Anemometer | 0 | 0 | 0 | 0 | 0 |

`TABLE_HEADER_ONLY` is by far the dominant exclusion reason in every manual that has any exclusions.

## Issue classes, summarized

1. **Orphaned table headers (dominant, ~1,300 chunks)** — correctly detected and excluded by `retrieval_metadata()`, but the paired data chunk is never re-labeled, so the content is either lost (header dropped) or kept meaningless (data with no header).
2. **Label-less data survivors** — chunks like `CHK-000169` pass every existing quality check (100/100, no warnings) purely because they have enough *tokens*, without checking whether those tokens are meaningful vocabulary. These are retrieval-invisible to both lexical and vector search.
3. **Table-fragment corruption** — Docling's table structure recognizer breaks multi-line/merged cells in complex fault-finding tables into disconnected fragments, independent of chunk token budget.
4. **Content-type misclassification** — some genuinely tabular content (parts-list rows) is tagged `content_type: "prose"`, which would break any future table-aware ranking boost.

## Recommended fixes, in priority order

1. **Add a label-density check to `retrieval_metadata()`** (`app/retrieval.py`) — for any `table_related` chunk, compute the ratio of alphabetic label tokens to numeric tokens. Flag near-zero-label table chunks with a new warning (e.g. `TABLE_DATA_WITHOUT_HEADER`) and cap their quality score. This stops chunks like `CHK-000169` from being trusted as standalone, fully-scored answers.

2. **Build a table-stitching pass keyed on `doc_items`** — before/alongside eligibility scoring, group consecutive chunks referencing the same `#/tables/N` doc_item and same page. When a header-only chunk is immediately followed by data-only chunk(s) from the same table, synthesize a merged chunk (header + data together) for embedding/lexical indexing, while keeping the original `chunk_id`s for citation/provenance. This is the direct fix for the SWL case and the ~1,300 header/data pairs found above. **This is the highest-leverage fix.**

3. **Re-run the stitching fix across all 7 already-processed manuals** — this is a post-processing pass on existing `chunks.jsonl`, not a re-conversion. Regenerate `retrieval_index.jsonl` and rebuild embedding vectors only for changed rows. Prioritize the two electrical manuals first (836 and 363 orphaned headers).

4. **Address table-fragment corruption** in fault-finding tables — either adjust `docling_serve`'s table-cell handling upstream, or, as a stopgap, apply the same doc_item-based concatenation from step 2 to `TABLE_FRAGMENT`-tagged chunks.

5. **Fix `content_type` misclassification** for parts-list-style tables (space-delimited, not markdown-pipe tables) in `_table_shape`/`_content_type` so they're correctly tagged `table`/`table_related` rather than `prose`.

6. **Re-benchmark before declaring 90%** — use the existing `docs/benchmarks` harness plus a query set of manual spec-lookups (SWL-style numeric/spec questions), and add "quality_score 100 but zero alphabetic labels" as a tracked regression metric — that's the exact blind spot that let this issue hide behind 87–99% mean quality scores.
