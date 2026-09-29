# Marine Pipeline Studio 2026.09.29.40.11Z — Release validation

## Purpose

Fix human visual **Recover evidence** so it is real durable Stage 2B Vision work rather than an invisible in-memory background task.

## Root cause in 40.11Y

`POST .../vision-audit/.../recover` called `Stage2BWorker.start_human_visual_evidence_recovery()`, which used `asyncio.create_task()` and tracked state only in process memory. No `verification_jobs` row was created. Consequently the request did not appear in the Vision/Artifact queue, did not participate in Start/Stop/Auto queue state, and queued/waiting state could be lost on restart.

## 40.11Z behavior

- Clicking **Recover evidence** upserts a current `verification_jobs` row with code `HUMAN_VISUAL_EVIDENCE_RECOVERY`.
- The row is logical Vision work (`target=oneplus`) with high priority. The configured Vision provider may still be Pi5, OnePlus, or Groq according to existing provider selection.
- It is visible through normal Stage 2B status/queue APIs and is included in Vision workload counts.
- Stop pauses new recovery dispatch; Start authorizes it; Auto continuously dispatches it.
- The existing physical-provider reservation, CheckpointVerifier lock, OnePlus workload governor, endpoint circuit breaker, retry/defer and restart recovery behavior apply automatically.
- A recovery row merges evidence into the existing authoritative human `vision_enrichment` ledger entry. It does not create a second Stage 2C visual entry.
- Recovery rows are excluded from `verification_rows_for_stage2c()`, so they cannot alter the Stage 2C verification signature or create false Stage 2C staleness.
- Vision Audit filters out internal recovery-result rows and keeps the originating visual as the human review subject.
- The Verification Vision card reports queued/running/failed evidence-recovery work.

## Scheduling order

1. Normal Text/Vision work
2. Human evidence-recovery Vision work (high-priority within the Vision lane)
3. Shared Artifact idle pool

A pending recovery is a non-artifact blocker for the same book, so a still-needed human recovery is not bypassed by a later artifact sweep.

## Upgrade behavior

No Stage 1, Stage 2A, or completed normal Stage 2B rerun is required solely for Y -> Z.

An evidence-recovery click made under Y that was only resident in memory cannot be migrated as a durable queue row. If the ledger still shows `human_evidence_recovery_required=true` after upgrade, click **Recover evidence** once; Z will create the durable Vision queue row.

## Validation

From the 40.11Z working tree:

- `python -m compileall -q app` — PASS
- every `app/static/*.js` through `node --check` — PASS
- full pytest suite — **618 passed, 1 skipped**

Focused coverage includes:

- durable recovery queue creation and logical Vision targeting;
- full-image + all configured crop evidence merge while preserving the human decision;
- exclusion of recovery rows from Stage 2C signature/source inputs;
- existing Stage 2B, artifact interlock, pipeline-state, Vision audit and human-review gate regressions.
