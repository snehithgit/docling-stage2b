# .40.5 — Source Fidelity & Retrieval Integrity

## Goal
Improve real troubleshooting reliability before changing the embedding model. This release protects technical source content first, then fixes stale derived retrieval artifacts and benchmark/ranking integrity.

## Implemented

### Stage 2C source-preservation gate
Automatic source-image transcription is now rejected for auto-apply when it removes or changes source-critical technical information. Human-verified corrections remain authoritative.

Protected cases include:
- engineering values/units, limits, SWL/WLL/capacity, formula results and structured identifiers;
- troubleshooting action verbs such as check, inspect, verify, replace, reset, clean, tighten, measure, reconnect;
- significant source contraction or an incomplete/dangling transcription;
- dense table/parts-list content already protected by the existing table-cell scope guard.

Safe formatting-only normalization such as `24 V` -> `24V` remains eligible.

### Legacy automatic-correction revalidation
Existing unreviewed automatic text corrections are rechecked deterministically from the saved ledger. No Pi5/OnePlus/Groq rerun is required. Unsafe automatic overlays are demoted to pending/keep-original and their overlay is removed. Human decisions are never demoted.

Dry-run on the uploaded seven-manual processed corpus:
- previously auto-applied text corrections: 400;
- remaining auto-applied after `.40.5` safety replay: 337;
- 64 legacy entries were identified as unsafe/pending after replay (one ledger also contained a previously revalidated entry, so the changed-entry count was 63).

Representative reasons included critical token loss, dropped troubleshooting actions, source contraction, incomplete sentence endings and scope inversion.

### Rule-version freshness
Stage 2C records `stage2c-source-fidelity-v7` and becomes stale when the safety rule changes.

Derived retrieval artifacts record `retrieval-source-integrity-v5`. When only retrieval rules change:

`canonical Stage 3 chunks remain current -> retrieval artifacts stale -> rebuild retrieval only -> machine embedding stale -> rebuild machine index`.

Docling, Stage 2B and canonical Stage 3 chunk generation are not rerun for a retrieval-only rule change.

### Identity integrity
Persisted Stage 2C / Stage 3 / retrieval job IDs are checked against the authoritative `__jobN__` result-directory identity. Unambiguous JSON metadata is repaired in place and tagged with `identity_repair`; a runtime/directory disagreement is never silently repaired.

### Benchmark scope integrity
Machine-scoped benchmarks never silently fall back to all-books search. Legacy cases without an explicit or uniquely inferred equipment scope are skipped as `equipment_scope_required` instead of corrupting the metric.

### Retrieval diversity
Synthetic `TBL-*` evidence sharing the same book/table reference is clustered so one fragmented table cannot occupy many early Top-K positions. Canonical source chunks are not collapsed.

### Targeted ranking hardening
- cross-reference heading/section evidence remains conservative;
- maintenance interval/value evidence gets a deterministic boost when the query explicitly asks for an interval/period;
- explicit parts-list item/position numbers can bind to the matching row without turning all plain numbers into global structured identifiers;
- commissioning/run-in questions prefer rows containing the actual numeric limit and operating duration.

### Electrical troubleshooting holdout
A separate 12-case holdout is included for the two electrical troubleshooting manuals. It is not merged into the frozen 133 regression set. Because those manuals are not yet assigned to real equipment scopes in the uploaded registry, the bundled baseline is diagnostic only, not production Machine-RAG acceptance.

## Pipeline after `.40.5`

`Docling -> 2A -> 2B -> 2C source-fidelity gate -> Stage 3 canonical chunks -> retrieval reconstruction/index -> complete machine embedding -> Machine RAG`

Changing an upstream signature/rule invalidates only the necessary downstream stages.

## Deployment acceptance still required
After the current artifact queue finishes and downstream stages settle on the N150:
1. run the frozen 133 lexical regression;
2. run fresh equipment-scoped BGE hybrid against TEI on the N150;
3. run the electrical troubleshooting holdout after deliberate equipment assignment;
4. record Top-1/3/5/10, MRR, category metrics, latency and machine-index fingerprint.

Do not report the historical structured-ID candidate replay as a fresh `.40.5` hybrid benchmark.
