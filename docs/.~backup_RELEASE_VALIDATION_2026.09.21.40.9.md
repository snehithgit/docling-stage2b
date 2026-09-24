# Marine Pipeline Studio v2026.09.21.40.9

## Scope

This release closes the remaining items in the September 21 application audit backlog after `.40.8B`, while preserving the immutable-Docling, human-authority, machine-scoped RAG and monitoring-only Telegram architecture. The SQLite/SMB finding remains intentionally omitted because it described the review/share environment rather than the real container deployment.

## Closed findings

### 10. Type-aware configuration validation
- `to_formats`, `supported_extensions`, and `telegram_allowed_chat_ids` must have their documented list types.
- A scalar such as `supported_extensions: .pdf` now fails startup validation instead of being iterated character-by-character.
- Extension values are normalized/deduplicated; Telegram chat IDs remain integer-only and deduplicated.

### 13. Stage 3 table-cell correction provenance
- Applied table-cell corrections now remain attached to Stage 3 chunks that reference the corrected Docling table.
- Provenance includes ledger entry ID, table/cell index, row/column span, original/corrected text, page, provenance source and human-verification flag.
- Merged-cell spans are preserved.

### 15. Equipment allow-list is now a hard generation boundary
- Generation filters text and visual retrieval results to `allowed_job_ids` before selecting an anchor.
- An out-of-scope Top-1 result or neighbor can no longer enter an equipment-scoped evidence packet.
- The per-source add path keeps a second scope guard as defense in depth.

### 7. Blocking work removed from identified async request paths
- Original-PDF rendering/highlighting runs in `asyncio.to_thread`.
- Large ledger/manifest/registry/audit reads and deterministic audit summaries in the affected async endpoints are moved off the event loop.
- Pipeline/background audit checks that could block the same event loop are offloaded as well.

### 14. Token-budget accounting hardened
- Local Stage 3 split/compaction no longer uses the old single proportional estimate with a 3% margin.
- The deterministic estimator takes the maximum of Docling parent token density with an 8% margin, a conservative character-density bound, and a lexical/punctuation piece count.
- Estimated children are marked `conservative_multi_signal_estimate_v2`.
- No heavyweight tokenizer dependency is added.

### HTTP verifier client lifetime
- Source inspection disproved the reported leak: verifier HTTP calls already create `httpx.AsyncClient` with `async with`, and the Telegram service explicitly closes its one persistent client in `stop()`.
- Regression coverage now asserts per-call verifier client exit/cleanup. No unnecessary client-lifecycle redesign was introduced.

### Recursion hardening
- The Docling body/group flattener now uses an explicit stack with a 200-level ceiling instead of unbounded Python recursion.
- Deep/malformed document graphs cannot trigger `RecursionError` in that walker.

### Verifier schema robustness
- Groq vision schema selection is explicit (`vision`, `direct_transcription`, `crosscheck`) rather than inferred from exact prompt substrings.
- Stage 2B production call sites pass the intended schema mode explicitly, so prompt wording can evolve without silently changing response schema.

### Telegram regression coverage
- Added tests for unauthorized-chat rejection, command dispatch, bot-name suffix handling, non-command ignoring and persistent-client shutdown.
- Telegram remains monitoring-only and never writes pipeline files/SQLite directly.

### 12. Stronger claim ↔ citation grounding
- Citation auditing now checks claims, not only whether a `[S#]`/`[V#]` label exists.
- Each factual/technical claim must cite supplied evidence.
- Exact technical values/identifiers must occur in the cited exact evidence; for visual sources, exact values must be present in `visible_text`, not only in model-generated summary/object interpretation.
- Non-critical claims require meaningful lexical overlap with the cited source.
- Unsupported claims set `grounding_passed=false` and `answer_usable=false` and produce a UI/API grounding warning.
- This is a conservative deterministic support check, not a claim of full semantic/NLI entailment.

## Stage 3 freshness for this release

Because table-correction provenance and token post-validation change canonical Stage 3 output semantics, `.40.9` adds `STAGE3_RULE_VERSION = stage3-canonical-integrity-v2`. Older Stage 3 output is therefore marked stale and must be rebuilt rather than being incorrectly treated as current. Retrieval-only refresh cannot falsely stamp the new Stage 3 rule version.

## Explicitly omitted / separately tracked

- SQLite/SMB audit item: intentionally omitted as non-applicable to the actual deployment.
- Atomic immutable machine-index generations / single `CURRENT` pointer: separate retrieval-transaction work.
- Explicit embedding-rule version/fingerprint: separate embedding freshness work.
- Docling forgotten-task/404 resubmission: separate conversion recovery work.
- Table structural-binding enforcement at overlay consumption remains separate from the provenance fix above.

## Regression coverage

Direct regression tests cover configuration scalar mistakes, equipment-scope anchor leakage, table-cell provenance, deep document trees, conservative token estimation, explicit verifier schemas, Telegram behavior/client cleanup, Stage 3 rule-version staleness, and claim-to-cited-source support including visual exact-value rules.

Validation before packaging: **481/481 tests pass** (`PYTHONPATH=. pytest -q`). Python bytecode compilation, JavaScript syntax checks, shell syntax checks, and YAML parsing also pass. The final release procedure re-extracts the exact ZIP and repeats the full pytest suite plus the same available syntax/parse checks before release.
