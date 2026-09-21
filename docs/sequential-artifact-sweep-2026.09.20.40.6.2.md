# Sequential Artifact Sweep — 2026.09.20.40.6.2

## Problem

Older releases created `FULL_TECHNICAL_VISUAL` rows only when Artifact Audit **Start all** was clicked. A book could therefore become Stage 2C/Stage 3/RAG-ready without ever receiving the full technical-artifact sweep. Starting the sweep later changed the Stage 2B signature and invalidated downstream stages. The sweep also duplicated any picture that already had a normal `VISION_REVIEW` route.

## New sequence

`Docling -> Stage 2A -> normal Stage 2B Text/Vision -> full technical-artifact sweep -> Stage 2C -> Stage 3 -> retrieval -> machine embedding -> Machine RAG`

1. Stage 2A route discovery automatically prepares artifact-sweep rows.
2. Prepared sweep rows start unauthorized.
3. **Verify book** or normal Start authorizes only normal Text/Vision rows and arms that book's sweep. Auto Run arms the sweep when normal processing begins.
4. The database releases sweep rows only when no current normal route for the book is pending, processing, or failed.
5. `claim_next_artifact()` independently rechecks the dependency, so an accidentally-authorized legacy row still cannot run early.
6. The existing shared Pi5/OnePlus work-stealing pool drains released artifact rows.
7. Stage 2C auto-finalization sees the pending/released sweep rows and waits until they are all completed.

## Duplicate picture protection

A Docling picture with a current normal picture-verification route is excluded from the sweep. During upgrade/backfill, overlapping current sweep rows are marked historical. Any matching Stage 2C `vision_enrichment` entry is retained for audit but changed to `status=superseded`, and active overlays are rebuilt without it. Artifact Audit also prefers the normal picture route as a defensive read-side rule.

## Artifact Audit button

**Verify all technical artifacts (backfill)** remains for old/imported books and explicit maintenance. It prepares/arms missing rows but obeys the same normal-verification dependency; it cannot bypass Text/Vision completion.

## Validation

- Python test suite: 428/428 passed.
- New regression coverage verifies automatic sweep preparation, normal-picture overlap skipping, Verify-book sequencing, release only after Text + Vision completion, and the hard claim guard against early authorization.
- Raw Docling artifacts remain immutable.
