# Marine Pipeline Studio 2026.09.30.40.11AH — release validation

## Scope

This release builds on 40.11AG and addresses three issues found against the user's live processed output:

1. Verification health/manual Stage-2B start now use the dynamic Colab worker registry instead of stale legacy `colab_url`/API-key configuration.
2. Full-artifact sweep rows cannot remain current against an older `__runN` result directory after a Stage-2A rerun; matching rows are re-armed against the new run and obsolete rows are retired.
3. A guarded reset utility can restart the pipeline from preserved Docling output without reconversion.

## Stage-2A reset contract

`tools/reset_to_stage2a.py --apply --colab-only`:

- stops `docling-autoconvert` before modifying SQLite/files;
- requires at least one valid enabled dynamic Colab worker with URL + ASCII API key;
- snapshots `data/`, `config.yaml`, and compose metadata;
- moves the existing `processed/` tree into a timestamped backup (lossless and fast on the same filesystem);
- leaves `converted/` unchanged and records its file manifest;
- preserves the `jobs` table (Stage-1/Docling conversion ledger);
- clears `postprocess_jobs`, `verification_jobs`, and `review_assistant_jobs`;
- configures text + vision verification for Colab;
- pauses local Pi5/OnePlus Stage-2B participation and enables artifact participation on the selected Colab worker;
- restarts the app, which rediscoveries completed conversions and queues fresh Stage 2A automatically;
- rolls back from the backup automatically if a reset step fails.

`tools/restore_stage2a_backup.py` restores one of those backups explicitly.

## Data intentionally preserved

- `converted/*.zip` raw Docling output
- Stage-1 `jobs` rows required to rediscover those converted outputs without Docling reconversion
- input/source files
- dynamic worker registry/API-key files (with an exact pre-reset copy stored in the backup)

## Data intentionally reset

- all `/processed` Stage-2A/2B/2C/Stage-3 artifacts and retrieval derivatives
- Stage-2A postprocess rows
- Stage-2B verification rows
- deferred AI review-assistant rows

## Targeted validation

- reset-tool database preservation/reset tests
- Colab-only worker registry mutation test
- provider YAML update test
- artifact-sweep rerun identity/regression tests

Full project suite was run after version stamping and packaging checks.
