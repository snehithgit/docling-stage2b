# Release Validation — 2026.09.19.40.5.1

## Scope
UI hotfix for the Convert / Folder watcher Recent documents renderer.

## Fixed defect
The dashboard refresh still wrote to a removed optional navigation element (`#failed-nav`). The null write threw before `renderJobs(data.jobs)`, leaving valid queue counters visible while the Recent documents table showed `Cannot set properties of null (setting 'textContent')`.

The same stale optional-element assumption existed in Manual Convert and Errors scripts and is now guarded there as well.

## Validation
- Full pytest suite: **420 passed**.
- Python compile: PASS.
- Static JavaScript syntax (`node --check`): PASS.
- Shell syntax (`bash -n`): PASS.
- YAML/Compose parse: PASS.
- Benchmark JSON parse: PASS.
- Static asset cache-busting matches `APP_VERSION = 2026.09.19.40.5.1`.
- Regression tests verify no direct write remains to removed `#failed-nav` targets and the Recent documents renderer is defensive.

## Pipeline impact
No conversion, verification, Stage 2C, Stage 3, retrieval, embedding, equipment registry, or persisted job data is changed by this hotfix. Existing pending/processing/completed documents are preserved during deployment.
