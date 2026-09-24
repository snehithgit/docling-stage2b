# Release validation — 2026.09.23.40.11N

Fresh-audit remediation batch based on 40.11M.

Implemented:
- NEW-1: repaired GroqStructuredVerifier recursive `_reserved_client`; added real text-client and reservation-release regression tests.
- Correction-ledger authority: Stage 2C backfill normalization and artifact-sweep supersession now use the shared Stage 2C ledger lock.
- Stage 2B terminal persistence: failure-artifact writes are best-effort and cannot strand a claimed row in `processing`; lost CAS claims no longer fail another owner's row; pre-claim setup failures remain pending.
- Stage 2A: processing transition is inside the worker's protected failure boundary.
- Conversion queue deletion: terminal rows are atomically reserved as `deleting` before quarantine, blocking retry races; reservation is restored on quarantine/delete failure; rollback refuses to overwrite a newly-created destination; manifest-write failure rolls file moves back.
- Equipment hybrid index: rebuild temp files are unique per generation attempt; equipment index deletion shares the same lock as query/publish snapshots.
- Pi5 timeout validation now requires a 30-second outer margin over the HTTP request timeout.
- Shared artifact attribution uses `claimed_by` for processing, completed, and failed counters.
- Telegram delivery failures are logged instead of silently swallowed.
- Visual authority fails safe when a source image identity cannot be resolved instead of searching the entire book.
- Vision Audit decision dropdown now includes `Evidence recovery`.

Validation:
- `python -m compileall -q app`: PASS
- `PYTHONPATH=. pytest -q`: 553 passed, 1 skipped

Notes:
- Telegram's event subscriber remains in-process rather than durable across process restart. Delivery failures are now observable in logs.
- The original managed-book delete/quarantine architectural race remains separate from the newly fixed conversion-queue delete/retry race; rollback is now collision-safe, but a broader managed-book deletion state-machine change should still be driven by a deterministic reproducer.
