# Marine Pipeline Studio — 2026.09.28.40.11W validation

## Scope

40.11W fixes idle local workers failing to resume pending `FULL_TECHNICAL_VISUAL` artifact work and replaces independent Text/Vision worker controls with one scheduler interlock for **Text + Vision + Artifact** work.

## Scheduler contract

The Verification page now exposes one shared control set:

- **Start** — manual snapshot mode. Unpauses both worker loops, disables Auto, authorizes all currently pending normal Text/Vision routes, and releases artifact rows whose normal dependencies are already complete.
- **Stop** — pauses new Text/Vision/Artifact dispatch globally. A request already in flight is allowed to finish safely. Pending work is preserved.
- **Auto** — continuous mode. All normal Text/Vision routes remain eligible automatically; artifact work is released after its book's normal verification is complete and consumes an idle Pi5/OnePlus worker automatically.

Normal Text/Vision work has priority over the shared artifact pool. A scheduler-level provider reservation prevents Text, Vision and Artifact lanes from simultaneously claiming the same physical Pi5/OnePlus/Groq provider. The existing provider lock remains the final single-flight inference guard.

## Artifact queue recovery

Older databases could contain artifact rows in this state:

`status=pending`, `authorized=0`, `run_mode=NULL`

Those rows were invisible to the previous artifact release query and could remain pending while Pi5 and OnePlus both appeared idle. 40.11W:

- normalizes legacy current pending artifact rows with NULL `run_mode` to `awaiting_normal` during store initialization;
- accepts the legacy NULL state in the release path as a second fail-safe;
- re-scans artifact release eligibility whenever an idle local worker is looking for work;
- preserves dependency-ready artifact authorization when manual Text/Vision authorization is cleared or the shared scheduler is stopped.

## UI/status

The Verification page now shows three workload cards: **Text**, **Vision**, and **Artifact shared idle pool**. Artifact status reports completed/pending/failed counts, dependency-waiting vs ready work, and the physical provider currently reserved for artifact processing.

The `/api/stage2b/status` response adds:

- `interlock.mode` (`started`, `stopped`, `auto`, or legacy `mixed`)
- `interlock.provider_reservations`
- logical `workloads.text`, `workloads.vision`, and `workloads.artifact` counts

New control endpoints:

- `POST /api/stage2b/interlock/start`
- `POST /api/stage2b/interlock/stop`
- `POST /api/stage2b/interlock/auto`

Legacy per-device APIs remain available for compatibility, but the main UI uses the shared interlock.

## Pipeline compatibility

This release changes only Stage 2B scheduling/control and queue-state recovery. It does **not** change Docling Stage 2A output, Stage 2C correction semantics, Stage 3 chunk rules, raw Docling data, or human decisions. No Stage 2A/2B rerun is required solely for the upgrade; existing pending artifact rows are recovered in place.

## Validation

Executed against the packaged 40.11W source tree:

- pytest: **609 passed, 1 skipped**
- Python `compileall`: **PASS**
- every frontend JavaScript file with `node --check`: **PASS**

Added regression coverage verifies:

- legacy NULL artifact `run_mode` rows are released;
- stopping/clearing manual normal work does not disarm a dependency-ready artifact;
- one physical provider cannot be scheduler-reserved simultaneously by normal and artifact work;
- artifact work yields when another normal role has runnable work on that same physical provider;
- Start / Stop / Auto interlock API state is reflected by `/api/stage2b/status`;
- the Verification UI exposes the shared interlock and Artifact idle-pool card.
