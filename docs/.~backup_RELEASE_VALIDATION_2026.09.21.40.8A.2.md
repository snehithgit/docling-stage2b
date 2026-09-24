# Marine Pipeline Studio v2026.09.21.40.8A.2

## Scope

Verification-coverage hotfix on top of `.40.8A.1`. It changes Stage 2A route collection/visibility only; Stage 2B queue semantics, Stage 2C authority rules, Stage 3, retrieval, embeddings, and RAG behavior are unchanged.

## Problem closed

`build_routes()` previously stopped adding candidates as soon as `len(routes) >= max_routes_per_document`. Diagnostic signals are generated in a fixed order and `LOW_CONFIDENCE_VISUAL` is late, so sufficiently noisy OCR/unicode/table diagnostics could consume the old 500-route ceiling before picture-review candidates were even considered. The later priority sort could only rank the already-truncated subset.

## New invariant

1. Collect every diagnostic candidate.
2. Deduplicate routes by the existing source identity key.
3. Compute the existing priority/score for every candidate.
4. Sort the complete candidate set globally.
5. Apply `max_routes_per_document` only as a runaway/corruption safety ceiling.
6. Queue the selected routes through the existing durable `verification_jobs(status='pending')` mechanism.
7. If the safety ceiling is exceeded, retain all remaining candidates in `routes.json` and report the event loudly.

The default ceiling is now `5000`; it is not a batching tier. Normal worker behavior remains one claimed pending verification job at a time.

## Auditable route summary

`routes.json.summary` now includes:

- `routes` / `routes_created` — queued Stage 2B routes
- `total_candidates_detected` — complete deduplicated Stage 2A candidate set
- `deferred` — candidates beyond the safety ceiling
- `dropped` — always `0` because deferred candidates are retained
- `by_target` / `total_by_target` / `deferred_by_target`
- `deferred_by_code`
- `safety_valve_triggered`

`deferred_routes` contains the full retained candidate records. Raising the ceiling and rerunning Stage 2A queues them using the same deterministic priority order.

## Efficiency

Because candidate collection may now exceed the old 500-route limit, duplicate-route lookup was changed from a linear rescan of the growing route list to a dictionary keyed by the same route identity. This avoids making the existing O(n²) duplicate merge more expensive as the safety ceiling grows.

## Visibility

- Quality page shows `queued / total` routes and a deferred warning when needed.
- Book workflow shows the same route coverage.
- Telegram `/status` and `/books` show Stage 2A queued/total/deferred counts in the existing dashboard layout.

## Regression coverage

New regression tests reproduce the original starvation ordering: several low-priority OCR candidates are generated first and a technical picture candidate last under a deliberately tiny ceiling. The technical picture must survive because global ranking happens before the ceiling. A second test verifies loud deferred counts/breakdowns and retained deferred candidates.

## Explicit non-scope

This release does **not** fix stale/duplicate correction-ledger reconciliation across Stage 2B/2C generation reruns. That is independent of candidate coverage and remains open.

## Validation

- Full pytest suite: **452 / 452 passed**.
- Python compileall: PASS.
- JavaScript syntax check: PASS.
- Shell syntax check: PASS.
- YAML/Compose parse: PASS.
- Release ZIP integrity: PASS.
