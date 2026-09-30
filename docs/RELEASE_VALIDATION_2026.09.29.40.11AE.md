# Marine Pipeline Studio — Release Validation — 2026.09.29.40.11AE

## Scope

40.11AE fixes Colab worker Stop/Remove behavior discovered in 40.11AD worker management.

## Root causes

1. Stop is intentionally drain-safe, but AD rendered an active paused worker as `Running`, so an accepted Stop request looked ineffective until the current inference completed.
2. Removing the last dynamic Colab worker was not durable when legacy AB/AC Colab fields remained configured. `ensure_legacy_colab()` treated an empty dynamic worker list as "not migrated" and recreated `colab-1` on the next registry snapshot.
3. DELETE rejected an active worker with HTTP 409, forcing the operator to wait and retry manually instead of allowing a safe drain-and-remove request.
4. Dynamic providers use names such as `colab:colab-1`, but the outer Stage 2B job timeout only recognized the literal `colab`, so some Colab jobs could inherit a longer local-device timeout.

## Implemented

- Legacy Colab import is now a durable one-time migration using `legacy_colab_import_done`.
- Deleting the last Colab worker no longer causes it to reappear from old config values.
- Busy Colab removal is drain-safe:
  - worker is immediately paused;
  - new Text/Vision/Artifact/review work cannot claim it;
  - review-worker assignments are detached;
  - `remove_requested` is persisted;
  - when the active provider reservation releases, the worker and its key file are removed automatically.
- Idle Colab workers are still removed immediately.
- Workers UI now distinguishes:
  - `Running`;
  - `Stopping after current job`;
  - `Stopped`;
  - `Removing after current job`;
  - `Pending removal`.
- Remove button says `Remove after current job` while busy and reports that removal is queued instead of surfacing a 409.
- A worker queued for removal cannot be resumed or reconfigured.
- Dynamic `colab:*` providers now use the configured Colab outer timeout rather than inheriting a Pi5/OnePlus route timeout.

## Safety behavior

Stop/Remove remain drain-safe. 40.11AE does not kill an in-flight model request or orphan a `processing` verification row. Pending work is preserved and can be claimed by another eligible worker after the normal scheduler rules allow it.

Raw Docling, Stage 2A, Stage 2C, Stage 3, retrieval, human review decisions, and existing verification results are unchanged.

## Validation

- `python -m pytest -q` -> **635 passed, 1 skipped**
- `python -m compileall -q app` -> **PASS**
- `node --check app/static/*.js` -> **PASS**

Focused regressions cover:

- one-time legacy Colab import;
- deleting the last migrated worker without resurrection;
- drain-safe busy-worker removal;
- review-assignment cleanup;
- provider reservation release triggering final removal;
- removal of the per-worker secret;
- existing worker management/UI tests.

## Upgrade

Upgrade directly from 40.11AD using the same database, processed directory, converted outputs, worker registry and review ledgers. No Stage 1, Stage 2A, completed Stage 2B, Stage 2C, or Stage 3 rerun is required solely for this release.
