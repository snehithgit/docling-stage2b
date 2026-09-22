# `.40.10.1` handoff

`2026.09.22.40.10.1` is the current implementation baseline.

## `.40.10.1` OnePlus stability behavior

- Use accumulated active inference time, not image/request count, to schedule rest.
- 90 min active work -> 20 min cooldown; 20 min natural idle resets the budget.
- Two <7 tok/s results -> scheduled cooldown; one <2 tok/s, one >=10 min request, or a transport outage -> 30 min severe cooldown.
- After cooldown, restart the canonical OnePlus llama.cpp service when available and require >=8 tok/s on the next real request to clear recovery probation.
- All work stays pending/deferred; cooldown does not downgrade evidence or consume retry budget.
- State is persisted in `/data/db/oneplus_workload.json`.

## What is closed

The September 21 audit list is closed across `.40.8A.2`, `.40.8B`, and `.40.9`:

- safe Git publishing;
- local verifier outage/circuit-breaker correctness;
- correction-generation reconciliation;
- duplicate conversion submission race;
- shared correction-ledger locking;
- global Stage 2A candidate ordering with a loud 5000-route safety valve;
- config list-type validation;
- Groq in-flight quota reservation;
- Stage 3 table-cell correction provenance;
- equipment-scope hard boundary;
- identified blocking async endpoint work;
- conservative Stage 3 token accounting;
- verifier HTTP lifetime verified context-managed;
- recursion guard;
- explicit verifier schema modes;
- Telegram tests;
- stronger deterministic claim ↔ cited-source support audit.

SQLite/SMB was intentionally omitted because the inspected network share was only the review/export environment; the real application runs in its container deployment.

## `.40.9` important behavior

- `STAGE3_RULE_VERSION = stage3-canonical-integrity-v2`; older Stage 3 canonical output is stale and must rebuild.
- Table-cell corrections keep table/cell + row/column spans in final chunk provenance.
- Equipment-scoped answer generation filters text/visual results before choosing an anchor.
- Exact visual values/identifiers count as grounded only if they occur in `visible_text` or are corroborated by text evidence.
- Telegram remains monitoring-only.
- Canonical OnePlus script remains CPU `4,5,6,7`, nice `10`, `-t 4 -tb 4` with the user-specified Qwen model/mmproj.

## Still separate before final production benchmark

- immutable/transactional machine index generations;
- explicit embedding-rule fingerprint/version;
- Docling forgotten-task (404) resubmission;
- table structural-binding validation when an overlay is consumed.

After those are done: **Revalidate all + rebuild**, rebuild stale machine embeddings, then run the fresh N150 machine-hybrid benchmark and electrical holdout.
