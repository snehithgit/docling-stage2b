# Phase 10: source coverage audit

The audit compares immutable Docling text, table and picture references with derived retrieval and visual evidence. It reports items and pages without search references, missing page provenance and dangling references. Technical candidate presence and validated candidate presence are counted separately.

Run from the repository root:

```sh
python tools/audit_source_coverage.py --source /data/output/manual.zip --result-dir /data/processed/manual --output coverage.json
```

The report writes only the specified output file. It does not change correction ledgers, worker settings, human decisions or indexes, and calls no model.

A reference proves that an item is represented, not that every sentence, table cell or diagram relationship has been parsed correctly. `semantic_coverage_verified` therefore remains false. Missing references are audit candidates: headers, decoration and intentionally excluded items may be legitimate omissions. They require classification before repair; they must not automatically be marked as errors or accepted as evidence.

This completes the source-reference measurement step. Phase 10 still requires disposition of coverage gaps, historical visual parsing and source validation, and a fresh retrieval/answer evaluation against the resulting corpus. The web release remains V5.0.8.3 until a deployable application change is ready.
