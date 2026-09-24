# Release Validation — 2026.09.20.40.6.2

## Scope

Sequential Stage 2B artifact-sweep orchestration on top of `.40.6.1` stale-file cleanup.

## Verified behavior

- Stage 2A route discovery auto-prepares sweep rows without a manual Artifact Audit click.
- **Verify book** authorizes normal Text/Vision rows only and arms the sweep.
- Sweep rows are released only after all current normal routes for that book are completed.
- Failed/pending/processing normal routes block sweep release.
- `claim_next_artifact()` independently enforces the dependency to protect upgraded/legacy databases.
- Auto Run arms sweep work when normal verification begins.
- Stage 2C remains blocked while prepared/released artifact work is unfinished.
- Current normal picture routes suppress same-picture full-sweep jobs.
- Legacy overlap sweep rows become historical; matching active Stage 2C vision entries become `superseded`.
- Artifact Audit manual action remains an optional backfill/arm control and cannot bypass sequencing.

## Validation

- `pytest -q`: **428 passed**.
- Python compilation: PASS.
- JavaScript syntax: PASS.
- YAML/config parsing: PASS.
- Static cache-busting/version alignment: `2026.09.20.40.6.2`.
- Raw Docling output mutation: none.
