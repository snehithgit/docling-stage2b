# Phase 10: source coverage audit

The audit compares immutable Docling text, table and picture references with derived retrieval and visual evidence. It reports items and pages without search references, missing page provenance and dangling references. Technical candidate presence and validated candidate presence are counted separately.

Run from the repository root:

```sh
python tools/audit_source_coverage.py --source /data/output/manual.zip --result-dir /data/processed/manual --output coverage.json
```

The report writes only the specified output file. It does not change correction ledgers, worker settings, human decisions or indexes, and calls no model.

A reference proves that an item is represented, not that every sentence, table cell or diagram relationship has been parsed correctly. `semantic_coverage_verified` therefore remains false. Missing references are audit candidates: headers, decoration and intentionally excluded items may be legitimate omissions. They require classification before repair; they must not automatically be marked as errors or accepted as evidence.

Stage 3 now runs this check after writing the search index and technical ledger. It saves `source_coverage.json`, including a prioritized `recovery_queue`. Same-page literal text already represented in chunks or headings is separated from review work. Ordinary page headers and footers are excluded, but warnings and cautions stay in review. Tables and pictures receive high priority. This queue is source-review work, not automatically dispatched model jobs: review must establish whether content was really lost before using GPU time or applying a repair.

Technical Evidence displays the queue and links to original source review. Its Check coverage action backfills existing books without rechunking or calling a model. Index, visual evidence, ledger or manifest changes mark the report stale. Release preflight reports unresolved gaps separately from source validation.

Phase 10 still requires disposition of coverage gaps, historical visual parsing and source validation, and a fresh retrieval/answer evaluation against the resulting corpus. Reference measurement never certifies whole-manual semantic accuracy.
