# Manual Structural Index - V5.1.0

The manual map is derived from current source-bound Stage 3 rows. It does not
rerun OCR, verification, generate an answer, or rewrite accepted source evidence.
It preserves the existing Stage 2 pipeline and machine/manual scope rules.

## Operator workflow

1. Open a book and select **Browse chapters**, or open `/manual-map`.
2. Build/refresh its map. Inspect headings, PDF page ranges and source passages.
3. Correct a title, category or range only after checking the source. Enter a name
   and save. Overrides and their history persist separately from automatic maps.
4. Optionally prepare semantic chapter search with the existing embedding service.
   Unchanged compatible section vectors are reused. No Colab inference is needed.
5. In Ask, choose **Try chapter-guided search**. The trace shows preferred chapters
   and confirms that global search within the selected scope still participates.

## Evidence and boundaries

Heading paths preserve parent/child hierarchy. Numbered parents can supplement a
flattened path. PDF bookmarks confirm matching heading anchors or supply sections
for headingless passages. Conflicting bookmarks remain unresolved diagnostics.
Explicit contents-list titles are matched to real heading anchors; printed page
numbers are retained as clues rather than guessed PDF offsets.

Sections may share a PDF page. Chunk assignment uses source order and heading
ancestry, not merely a page-number interval. Parent/child overlap is expected;
suspicious sibling overlap, unknown chunks and orphaned overrides are reported.
Ranges extend to the next peer/ancestor boundary where source order supports it.

Original title/text/page references remain intact. Runtime search results inherit
section ID, title, category, breadcrumb, parent and page range from the sidecar.
Search context is separate from literal source text. A chapter/category label or
semantic similarity is never certified answer evidence or a verified diagram link.

## Persistence / migration

This repository already uses per-book derived JSON artifacts, so no destructive
database migration or duplicate source-content table was added:

- `manual_structure.json`: hierarchy, chunk map, source fingerprint and diagnostics.
- `manual_structure_overrides.json`: persistent human structural corrections.
- `manual_structure_override_history.json`: append-only before/after decisions.
- `manual_section_vectors.<model hash>.json`: model-bound reusable section vectors.

New and refreshed Stage 3 indexes automatically build their map. Existing books:

```text
python tools/build_manual_structure.py /data/processed --pdf-dir /data/input
```

The UI rebuild endpoint can locate the original PDF through the conversion record.
No Stage 1/2 reset or chunk/embedding identity migration is required. Machine chunk
embeddings already include heading paths; section vectors are an independent
derived layer. Source changes mark the map stale; stale maps are excluded from routing.

## APIs

- GET `/api/postprocess/jobs/{id}/manual-structure`
- POST `/api/postprocess/jobs/{id}/manual-structure/rebuild`
- PUT `/api/postprocess/jobs/{id}/manual-structure/{section}`
- GET `/api/postprocess/jobs/{id}/manual-structure/{section}/chunks?offset=0`
- POST `/api/postprocess/jobs/{id}/manual-structure/embeddings`

Search, generation and portable prompts accept `structure_mode` with `auto`,
`current` or `structural`. Generation uses the same mode selected for the search.
No generator is called by map/benchmark operations.

## Flags and comparison

`retrieval_structural_enabled` defaults to false. Chapter guidance is opt-in while
evaluation continues. Configuration also controls section limit (5), candidate
depth (15), bounded score preference (0.03) and adjacent expansion.

Global ranking retains BM25/vector/exact-ID and evidence-quality signals. Sections
must match specific query terms or a strong compatible semantic signal to receive
preference. Neighbor passages stay in the same manual and deepest section, at
most one adjacent PDF page and two chunk positions away. Their relationship is
contextual, not a newly verified cause/remedy relationship.

```text
python tools/evaluate_structural_retrieval.py --root PRIVATE_AUDIT_DIR --limit 300 --output comparison.json
```

Use `--limit 0` for all eligible questions from the supplied 30,000-question file.
The tool compares machine-scoped current and structural lexical results on a
frozen corpus, with source-span Recall@1/3/5, MRR@10, individual gains/losses and
failure categories. Section accuracy is explicitly unavailable unless independent
section ground truth is supplied. It does not measure generated-answer correctness.

## Known limits

No AI hierarchy inference or invented chapter summaries are used. Typography-only
headings and ambiguous printed/PDF page mappings remain diagnostic rather than
guessed. Repeated/incorrect upstream heading paths can still produce imperfect maps.
The map is editable, but it is not a promise that every manual's structure is correct.
The proposed full large-benchmark and human structural validation must precede any
claim that default retrieval has improved.

## Validation for this release

The 300-question frozen-corpus comparison included 299 still-valid source spans.
Current and optional structural retrieval both achieved Recall@1 82.27%,
Recall@3 94.31%, Recall@5 95.32%, and MRR@10 0.8827: zero top-five gains or losses.
This confirms parity on this sample, not an improvement or full 30,000-question
validation. Chapter guidance remains opt-in. Generated answers and independent
chapter-label accuracy were not measured by this comparison.
