# Marine Pipeline Studio v2026.09.21.40.8A

## Trusted-decision and correction integrity

This release is intentionally limited to Stage 2A/2B/2C correctness. It does not change retrieval, embeddings, Stage 3 chunking, or RAG behavior.

### 1. Human-verified corrections survive Stage 2A regeneration

Stage 2A reruns no longer force-supersede correction-ledger entries carrying `human_verified: true`. Automatic/unreviewed entries are still superseded as before. New ledgers use the live `STAGE2C_RULE_VERSION` constant instead of the stale hardcoded `stage2c-structural-v1` string.

Invariant: **automatic regeneration may never overwrite or supersede a human-verified decision.**

### 2. Manual cross-check can no longer bypass trusted state

`_persist_manual_crosscheck` is now non-destructive unless a new READABLE direct source transcription independently passes the Stage 2C deterministic source-transcription safety profile.

- Human-verified entries are never changed automatically.
- Superseded entries cannot be reactivated by a later cross-check.
- `UNREADABLE` is audit evidence only and does not erase a prior rejected/applied/pending decision.
- A previously rejected machine candidate may be replaced only when the *new* READABLE source-image transcription independently passes the current deterministic safety gate.
- Readable-but-unsafe alternate transcriptions remain audit history and do not promote the ledger entry.

This deliberately avoids making `rejected` permanently sticky: rejection belongs to a candidate, not to every possible future source-image reading.

### 3. Troubleshooting actions are protected as obligations, not verb types

`stage2c-source-fidelity-v8` replaces the old verb-set comparison with deterministic `(verb, object-span)` obligation extraction.

- No NLP dependency was added; the implementation uses stdlib `re`, `Counter`, and `SequenceMatcher` only.
- `replace the fuse and holder assembly` remains one obligation.
- `replace the MC card and check the connector` becomes two obligations.
- Repeated identical obligations are counted as a multiset instead of being collapsed by `set()`.
- A conservative same-verb surface match tolerates small OCR spelling cleanup while refusing unrelated replacement clauses.
- The legacy `missing_action_verbs` field remains for compatibility; `missing_action_obligations` now exposes the actual obligation-level evidence.

The original failure is now blocked:

```text
SOURCE
Replace the MC card. Replace the damaged cable.

CANDIDATE
Replace the damaged cable.
```

The missing `replace mc card` obligation produces `TROUBLESHOOTING_ACTION_DROPPED` and keeps immutable Docling text.

### 4. Table-cell structural identity is carried forward

Stage 2A/2B now preserve the Docling cell span through the verification/correction metadata path:

- `table_index`
- `cell_index`
- `row_start`
- `row_end`
- `col_start`
- `col_end`

Merged cells retain their complete row/column range. Duplicate Stage 2A diagnostics converging on the same table-cell route merge missing structural metadata instead of losing it.

This release **does not** implement a table-wide search for a repeated critical value and infer that the value moved to another row. That design was rejected because ship tables legitimately repeat values such as `24 V`, `5 t`, `AUTO`, and `OFF`, which would create false positives. The safe follow-up is structural-binding validation at the overlay/application boundary using the persisted cell identity.

## Regression coverage

The release includes direct regression tests for:

- human-verified ledger entry surviving Stage 2A regeneration while an unreviewed entry is still superseded;
- rejected correction not being promoted by an unsafe READABLE manual cross-check;
- safe new source-image read being allowed to recover an old machine rejection;
- UNREADABLE cross-check preserving prior rejection;
- superseded entry refusing automatic reactivation;
- dropped same-verb remedy (`Replace the MC card`) being detected;
- normalized/reordered obligations not falsely failing;
- compound object `fuse and holder assembly` remaining one obligation;
- `and` followed by a second recognized action becoming a separate obligation;
- repeated identical obligations retaining occurrence count;
- small OCR spelling cleanup inside an action object not falsely appearing as a dropped action;
- merged table-cell row/column spans surviving Stage 2A routing and source-target crop metadata.

## Validation

Run from the repository root:

```bash
PYTHONPATH=. pytest -q
```

Result for this release:

```text
450 passed in 10.70s
```

Python compilation also passes for the modified Stage 2A/2B/2C modules and tests.

## Dependency / source guarantees

- No dependency was added.
- Raw Docling ZIP/JSON remains immutable.
- Retrieval, embeddings, Stage 3 chunking and RAG logic are unchanged in this release.
- `STAGE2C_RULE_VERSION` is now `stage2c-source-fidelity-v8`, so derived Stage 2C state can be recognized as stale against older fidelity rules.

## Deferred to `.40.8B`

- structural binding validation when an overlay is consumed/applied;
- immutable-generation retrieval index swap;
- explicit Stage 3 / retrieval / embedding freshness versions;
- Docling 404 dead-task resubmission;
- Groq in-flight quota reservation accounting.
