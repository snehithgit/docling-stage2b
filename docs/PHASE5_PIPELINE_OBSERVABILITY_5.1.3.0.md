# Phase 5 pipeline observability — 5.1.3.0

This phase implements the highest-value remaining recommendations from the
post-Phase-4 architecture audit without changing pipeline progression rules.

## What changed

### Canonical blocker contract

Every document pipeline projection now carries:

- `blockers`
- `primary_blocker`

The current blocker contains:

- `blocker_type`
- `code`
- `severity`
- `stage`
- `entity_id`
- `message`
- `operator_action_required`
- `auto_resolvable`
- `count`
- `details`

Only the earliest unresolved stage is exposed as the canonical blocker. This
prevents downstream consequences such as missing embeddings from obscuring an
upstream verification or human-review gate.

### Durable transition ledger

The existing SQLite database now includes append-only
`pipeline_transitions` rows during normal operation. The background sequencer
records a new row only when the projected stage or blocker changes.

Each row stores:

- book id
- observation timestamp
- previous stage
- new stage
- blocker code/message
- compact context

Dashboard/status GET requests do not write transition history. A transition-log
write failure is logged but cannot stop Stage 2C, Stage 3, or embedding work.

Deleting a managed book through the existing destructive lifecycle also removes
its transition rows together with the rest of that book's active database
history.

### Pipeline health API

`GET /api/pipeline/health` returns `pipeline-health/v1` with:

- total books
- RAG-ready books
- books needing operator action
- books expected to progress automatically
- counts by severity, stage, and blocker code
- current blockers
- recent durable transitions

`GET /api/pipeline/transitions` exposes the transition timeline directly and
can be filtered by postprocess job id.

### Diagnostics UI

Errors & diagnostics now adds:

- Books needing action
- Auto-progressing
- Why books are waiting
- Recent pipeline transitions

The existing conversion, verifier, Stage 2C/3, circuit-breaker and Verifier
Audit diagnostics remain intact.

## Safety properties retained

- Raw Docling output remains immutable.
- Human decisions remain authoritative.
- The canonical stage resolver still decides progression.
- Blockers are projections of existing state; they do not create a second
  state machine.
- GET/status APIs remain read-only.
- Transition logging is non-blocking.
- No all-books retrieval fallback was introduced.
- Machine embeddings remain scoped to one physical equipment set.

## Regression coverage

The test suite now covers:

- canonical blocker classification
- operator-action versus automatic blockers
- RAG-ready books having no blocker
- transition deduplication and previous-stage linkage
- health aggregation
- sequencer observation for a clean zero-route book
- diagnostics UI wiring

