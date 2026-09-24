# Release validation — 2026.09.23.40.11L

Remaining-audit hardening batch based on 40.11K.

Implemented:
- #9 Groq reservation cleanup now covers AsyncClient enter/construction cancellation as well as request failures for text and vision clients.
- #10 Telegram uses a dedicated unbounded in-process event subscriber; dashboard SSE retains size-1 coalescing semantics.
- #14 Telegram `/books` now checks Stage 2C and Stage 3 freshness before reporting Complete.
- #22 shared artifact claims persist `claimed_by`, and processing lane counters use the actual claiming device.

Schema migration:
- `verification_jobs.claimed_by TEXT` is additive and created automatically.

Validation:
- `python -m compileall -q app`: PASS
- `PYTHONPATH=. pytest -q`: 549 passed, 1 skipped

Not changed:
- #3 delete/quarantine race remains classified as an unverified architectural race. It should be reproduced deterministically before changing destructive lifecycle semantics; production quarantine evidence did not show an actual incident.
