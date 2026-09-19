# Release validation — 2026.09.19.40.4.2

## Scope

Hotfix for full technical-artifact scheduling. Normal Stage 2B Vision routing remains explicit single-provider. Only `FULL_TECHNICAL_VISUAL` uses the local Pi5 + OnePlus shared work-stealing pool.

## Implemented contract

- one atomic shared pending artifact pool;
- Pi5/OnePlus pull only while idle; no fixed share or ratio;
- a slow worker naturally receives less work because it cannot claim while busy;
- manual Pause is authoritative and Start/Retry does not unpause a worker;
- retryable transport/server failures trigger per-worker cooldown (default 60s);
- retry rows return to the shared pool and may move between devices;
- an in-flight job is never duplicated on the other worker;
- actual `artifact_worker` is recorded in saved request/result audit data;
- Artifact Audit reports completed Pi5 vs OnePlus counts;
- normal Vision provider selection remains unchanged with no fallback.

## Source-tree validation

- app version: `2026.09.19.40.4.2`
- pytest: `403 passed`
- Python compileall: PASS
- all static JavaScript `node --check`: PASS
- shell `bash -n`: PASS
- YAML/Compose parse: PASS
- benchmark/document JSON parse: PASS

## Exact archive gate

Candidate archive clean-extraction retest: PASS (`403 passed` plus Python/JS/shell/YAML/JSON validation). Final published archive is rebuilt with this record included and must pass the same gate again before publication.
