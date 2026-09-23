# Release validation — 2026.09.23.40.11K

Continuation of the 2026-09-22 deep bug audit after Phase A (40.11J).

Implemented in this hardening batch:
- #5 serialize equipment embedding publication/query snapshots so vectors/rows/metadata cannot be mixed within this process.
- #6 make legacy endpoint-outage recovery a true one-time DB migration.
- #7 apply the configured Pi5 timeout as an outer Stage 2B job ceiling as well as the HTTP-level timeout.
- #8 make Stage 2B mark_processing a pending->processing compare-and-swap.
- #11 align web /api/errors with the unlimited postprocess population used by Telegram.
- #12 report retry execution state and authorize newly-pending retries when a manual batch is already active.
- #13 normalize artifact-sweep retry/failure attempt accounting for preclaimed rows.
- #15 serialize equipment-registry read/modify/write mutations.
- #16 zero-citation generated answers can no longer pass grounding (except recognized insufficient-information answers).
- #17 isolate resume-loop failures per item so one exception cannot strand later work.
- #18 visual-evidence freshness now keys off ledger SHA-256 rather than filesystem mtime.
- #19 persist an idle reset performed by OnePlus workload snapshot().
- #20 serialize OnePlus status probes with control operations.
- #21 reject identical configured input/output/processed/database paths.

Still intentionally pending from the audit:
- #3 delete/quarantine race: plausible but unconfirmed; requires deterministic reproduction before changing destructive lifecycle code.
- #9 Groq reservation cancellation window.
- #10 reliable Telegram event delivery/backpressure semantics.
- #14 Telegram /books freshness parity with /api/documents.
- #22 persistent actual-claimant attribution for shared artifact-sweep rows.

Validation:
- python -m compileall -q app : PASS
- PYTHONPATH=<release-root> pytest -q --disable-warnings --maxfail=1 : 547 passed, 1 skipped
