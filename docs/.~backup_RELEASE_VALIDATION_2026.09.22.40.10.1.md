# Release validation — 2026.09.22.40.10.1

## Scope

Adaptive OnePlus workload protection derived from observed production outage history in `_processed (13).zip`. The change does not alter verifier prompts, model assignment, human-audit authority, raw Docling data, or RAG semantics. It changes only when new OnePlus inference may start.

## Production evidence behind the policy

- Two healthy-to-bad transitions occurred after about 123–126 minutes of near-continuous OnePlus inference.
- Healthy generation was about 10.5–11.7 tok/s; severe degraded periods fell below 1 tok/s, including about 0.28–0.38 tok/s.
- Request counts varied substantially, so request-count cooldowns were rejected in favor of accumulated active inference time plus throughput signals.

## Workload policy

- Existing physical OnePlus provider lock remains the single-flight boundary for normal vision, artifact sweep, source-image cross-checks and visual-evidence recovery.
- Accumulate actual OnePlus inference duration from llama.cpp streaming timings where available.
- After 90 minutes (5400 s) of accumulated active inference, enter a 20-minute scheduled cooldown.
- A natural idle period of 20 minutes resets the accumulated active-time budget.
- Two consecutive completed requests below 7 tok/s trigger a 20-minute cooldown.
- One completed request below 2 tok/s triggers a 30-minute severe cooldown.
- One request lasting at least 10 minutes triggers a 30-minute severe cooldown even if its reported generation speed is otherwise acceptable.
- Transport/time-out outage triggers a 30-minute severe cooldown.
- After cooldown, the canonical OnePlus llama.cpp control script is restarted when the SSH control bridge is available. Existing endpoint health checks still run before queued work is claimed.
- The first real inference after cooldown is a recovery probe: >=8 tok/s clears probation; slower recovery re-enters the severe cooldown.
- OnePlus logical jobs also retain the 40-minute absolute defense-in-depth ceiling.

## Persistence and quality guarantees

- Workload state is stored at `/data/db/oneplus_workload.json` (alongside `jobs.db`) so container restarts do not erase an active cooldown.
- Cooldown defers work without consuming retry budget or changing verifier/audit results.
- No verification candidate is dropped; pending work resumes after recovery.
- Checkpoint-reused inference does not consume the phone workload budget because no physical inference occurred.
- No prompt, token-quality gate, crop behavior, evidence merge, Stage 2C safety rule or human decision rule is weakened.

## Operator visibility

- `/api/oneplus-control/status` includes workload/budget/cooldown state.
- OnePlus web status shows active inference minutes, last generation speed, cooldown remaining and cooldown reason.
- Telegram `/status` and `/workers` show `Cooling` when workload protection is active.

## Regression coverage

Direct tests cover time-based budgeting, repeated slow throughput, severe slowdown, long requests, transport outages, restart + recovery probation, checkpoint reuse, shared single-flight locking, and Stage 2B defer-without-retry behavior.

## Validation

Pre-package validation: **517/517 tests pass**, Python compileall passes, JavaScript syntax passes, shell syntax passes, and YAML/Compose parsing passes. The exact packaged ZIP is re-extracted and retested before delivery; SHA-256 is recorded in the delivery response.
