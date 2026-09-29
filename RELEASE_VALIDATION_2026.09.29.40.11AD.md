# Marine Pipeline Studio — Release Validation — 2026.09.29.40.11AD

## Scope

Urgent worker-control and deferred AI human-review-assistant release, based on 40.11AC.

## Implemented

- Dedicated `/workers` page.
- Independent Stop/Resume for Pi5, OnePlus and every configured Colab worker.
- Stop is drain-safe: an in-flight request may finish; no new work is claimed afterward.
- Independent Artifact sweep participation checkbox for Pi5, OnePlus and each Colab worker.
- Dynamic Colab worker registry with Add/Save/Test/Stop/Resume/Remove and per-worker API-key file (mode 0600).
- Multiple Colab KoboldCpp workers can run concurrently because each worker owns a separate physical provider lock (`colab:<worker-id>`).
- Text/Vision provider selection can use the configured Colab worker pool while preserving explicit no-fallback semantics.
- Artifact idle pool can use any opted-in, configured, unpaused Colab worker after normal Text/Vision priority rules.
- Dedicated `/review-workers` settings/status page.
- Per-Colab assignment for Text human-review assistance and Vision human-review assistance.
- AI review-assistant phase is globally gated until all primary Text, Vision and Artifact jobs are clear across the library.
- Review-assistant jobs are durable in SQLite and recover interrupted `processing` rows after restart.
- Review suggestions are advisory only. They never set `human_verified` and never make a human visual decision.
- Text review page and Vision audit page surface the Colab review suggestion beside the authoritative human controls.
- Review workers use their own Stop/Resume state; legacy Pi5/OnePlus pause flags do not disable the post-verification Colab review phase.

## Data safety

- Existing Stage 2A/2B/2C/Stage 3 data is not invalidated by adding worker-management settings.
- Removing a Colab worker removes only its worker configuration/secret and review assignment; pending primary jobs remain available to other eligible workers.
- API keys are stored separately from the registry in mode-0600 files.
- Human review remains authoritative.

## Validation

Executed from the working source tree:

- `python -m pytest -q` → **633 passed, 1 skipped**
- `python -m compileall -q app` → **PASS**
- `node --check app/static/*.js` → **PASS**

Targeted worker/review tests:

- independent local worker pause and Artifact participation
- multiple Colab workers
- per-Colab Artifact participation
- configured/unconfigured/paused Colab filtering
- review worker assignment cleanup after Colab removal
- global Text/Vision/Artifact machine-work barrier before AI review phase
- review suggestions remain advisory in Text/Vision human-review UIs

## Upgrade

Upgrade from 40.11AC using the same persistent database, processed directory, converted outputs and review ledgers. No Stage 1, Stage 2A or completed normal Stage 2B rerun is required solely for this release.
