# Stale derived-file cleanup — 2026.09.20.40.6.1

This maintenance patch adds a preview-first **Clear stale files** action to the Verification page.

## What is eligible

Only deterministically stale derived artifacts are selected:

- `verification/stage2b_job_*_attempt_*_error.json` when the same job already has its final `stage2b_job_XXXXXX.json` result;
- completed inference `checkpoint_*.json` when that job already has its final result;
- equipment embedding-index directories whose exact equipment id is absent from a valid `equipment_registry.json`;
- `retrieval_quality.json` files whose `retrieval_rule_version` is missing or differs from the current retrieval rule.

The scanner does **not** select source manuals, converted ZIPs, current verifier results, Stage 2C overlays, canonical Stage 3 chunks, or active equipment embedding indexes.

If `equipment_registry.json` is missing/corrupt, no machine index is considered orphaned; this fails safe.

## UI/API

Verification → Maintenance now has:

- **Scan stale files** — read-only preview with count, bytes and category breakdown.
- **Clear stale files** — confirmation required; the server rescans immediately before deletion.

API:

- `GET /api/maintenance/stale-files`
- `POST /api/maintenance/stale-files/clear`

## Equipment deletion

Deleting an equipment scope now removes its derived `equipment_embedding_index/<equipment_id>` directory immediately, preventing new orphan machine indexes.

## Validation against supplied processed snapshot

`_processed (11).zip` preview:

- 7,384 superseded verification error files
- 1 completed-job checkpoint
- 1 orphan equipment index (`eq-deck-crane-a5a195`) containing 3 files
- 3 outdated `retrieval_quality.json` files
- 7,391 files total
- 10,725,409 bytes (~10.23 MiB)

Unfinished checkpoints were not selected.
