# Convert Queue Renderer Hotfix — 2026.09.19.40.5.1

## Symptom
The Convert / Folder watcher page showed valid queue counters but the Recent documents table displayed:

`Cannot set properties of null (setting 'textContent')`

## Root cause
The sidebar failure badge with id `failed-nav` had been removed from the current navigation HTML, but `dashboard.js` still assigned `textContent` to it unconditionally during every status refresh. That exception occurred before `renderJobs(data.jobs)`, so queue rows never rendered. `convert.js` and `errors.js` contained the same stale reference.

## Fix
- Treat `failed-nav` as optional via `document.getElementById()` + null guard in all three scripts.
- Make `renderJobs()` return safely if its table body is unavailable.
- Add regression tests preventing direct writes to the removed optional badge and protecting the Recent documents renderer.
- Cache-bust all static assets with version `2026.09.19.40.5.1`.

## Pipeline impact
None. Conversion, Stage 2A/2B/2C, Stage 3, retrieval and embeddings are unchanged. Existing queued/processing/completed documents are preserved.
