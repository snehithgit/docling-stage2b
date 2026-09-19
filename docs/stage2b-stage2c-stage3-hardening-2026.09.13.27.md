# Stage 2B / 2C / 3 hardening · 2026.09.13.27

## Stage 2B

- Adds `SCOPE_INVERSION_DISCARDED_TARGET`: if trimming discards a prefix/suffix that aligns substantially better with immutable Docling than the retained result, the reconstruction is rejected instead of auto-applied.
- Adds strict `TABLE_CELL_CONTEXT_CONTAMINATION`: table-cell verification cannot auto-trim neighboring row/cell text into a target cell. Ambiguous expansion remains unresolved.

## Stage 2C

- Rule version `stage2c-structural-v5`.
- The independent safety boundary now covers both text corrections and table-cell corrections.
- Old saved results can be demoted when their persisted scope guard shows that the discarded segment was more target-like than the applied text.
- Table-cell contamination is independently blocked even if an older Stage 2B result says `applied`.

Replay against `_processed (9).zip` identified five currently active automatic overlays that `.27` demotes without rerunning a model: Instruction Manual R00041/R00053/R00055/R00083 and Deck & Hull R00136. Hydraulics, AD136TI, MacGregor, Anemometer and Engine Room gained no additional demotions from these two new rules.

## Stage 3

The post-validator still never blind-splits arbitrary characters/tokens. It now adds three safe fallbacks:

1. compact Markdown alignment padding/separator rows in retrieval `text` while preserving `raw_text` unchanged;
2. split at complete table-row / paragraph / sentence boundaries only;
3. compact repeated ancestor-heading text while preserving the complete heading ancestry in metadata.

On the already-built `_processed (9)` chunks, replaying the new post-validator reduces chunks above the 256-token target from 3,027 to 627. The remaining 627 are deliberately retained because no safe logical split/compaction can bring them below the target. A fresh Stage 3 run starts from Docling's original HybridChunker output, so exact counts can differ.
