# Phase 4 acceptance and hardening — 5.1.2.2

Phase 4 closes the performance and operator-state hardening program without
relaxing any pipeline, human-review, evidence, or machine-scope gate.

## Accepted canonical sequence

The regression matrix verifies these operator states:

| Condition | Canonical next stage | Operator meaning |
| --- | --- | --- |
| Stage 2A incomplete | `stage2a` | extraction analysis is still running |
| required verification pending | `stage2b` | verification must finish |
| required verification failed | `stage2b` | failed checks require attention |
| clean zero-route book after current discovery | `stage2c` | no artificial Stage 2B backlog is created |
| Stage 2C stale/not built | `stage2c` | rebuild correction/enrichment overlay |
| structural review pending | `stage2a_human_review` | source review blocks Stage 3 |
| verifier audit blocking | `verifier_audit` | human verifier decision blocks Stage 3 |
| testing bypass with unresolved non-blocking audit | `stage3` | unresolved evidence remains unresolved; only the test gate is bypassed |
| Stage 3 stale/not built | `stage3` | canonical chunks must rebuild |
| Stage 3 current, no machine | `assign_machine` | assign manual to its physical machine |
| machine assigned, embedding stale/missing | `machine_embedding` | rebuild the complete machine corpus |
| machine embedding current | `rag_ready` | machine-scoped RAG is available |

Machine projection is explicitly tested not to overwrite an upstream structural
or verifier-review blocker.

## UI acceptance

The Books list and Book header now follow the canonical `pipeline.next_stage`
instead of inferring state from secondary counters.

In particular:

- `stage2a_human_review` always renders as source review even if its count is
  temporarily absent from a compact payload.
- failed required verification renders **Verification needs attention** at the
  Book header instead of the generic **In workflow**.
- Stage 2C, Stage 3, assignment, machine embedding and ready-RAG states have
  explicit top-level labels.
- only `rag_ready` produces the Books-page **Test RAG** shortcut.
- downstream machine-ready flags cannot visually hide an upstream human-review
  or failed-verification stage.

## Performance hardening completed in Phase 4

1. Exact Book status uses a targeted Stage-2B aggregate instead of scanning all
   verification books.
2. Books polling uses `/api/documents/summary`.
3. The summary is event-generation invalidated and has a bounded reconciliation
   age for out-of-process changes.
4. Unchanged client generations return `not_modified`.
5. Summary rebuilds preserve canonical pipeline/machine state but skip detailed
   evidence/readiness expansion that the Books page does not consume.
6. Full `/api/documents` and exact `/api/documents/{job_id}` remain the
   detailed authoritative APIs.

## Evidence acceptance already covered by the suite

Existing regression coverage additionally proves that a human-validated
`V-*` technical visual graph is rebound by exact evidence identity and reaches
the RAG generation packet as `validated_visual_graph`. Unvalidated visual
relationships remain withheld.

## Release gate

The Phase 4 branch is mergeable only after:

- Python syntax passes;
- JavaScript syntax passes;
- frontend behavior regressions pass;
- the complete test suite passes;
- static asset cache keys and `nav.js` version identity match
  `APP_VERSION = 5.1.2.2`.

