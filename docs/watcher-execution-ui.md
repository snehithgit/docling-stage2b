# Watcher execution controls and stable UI system

## Execution modes

File discovery is always active, but Docling submission has two explicit modes.

### Manual Start (default)

- `watcher_auto_run: false`
- New stable files are registered as `pending` and do **not** start by themselves.
- Press **Start queued files** on Overview to snapshot the jobs that are pending at that moment.
- That snapshot is processed **smallest source file first**, then progressively larger files.
- Only one Docling request is in flight at a time.
- Files discovered after Start are not added to the active manual batch; they wait for the next Start press.
- After the batch finishes, the watcher returns to **Waiting for Start**.

### Auto Run

- Toggle **Auto Run** on from Overview.
- The choice is persisted to `config.yaml` as `watcher_auto_run: true`.
- Pending/new files run automatically, still **smallest first** and **one at a time**.
- Turning Auto Run off does not cancel an in-flight Docling task. The current task finishes, then no new job is submitted until the next manual Start (or Auto Run is enabled again).

A saved remote Docling task from before an application restart is always resumed, regardless of the current mode, so the app does not abandon work already submitted to Docling.

## API

- `POST /api/watcher/start` — starts the current manual queue snapshot. Returns `409` while Auto Run is enabled.
- `PUT /api/watcher/auto-run` with `{ "enabled": true|false }` — switches and persists execution mode.
- `GET /api/status` includes a `watcher` object with `mode`, `state`, `auto_run`, `batch_remaining`, `pending`, `processing`, and `order`.

## UI system

The web app now uses one shared visual language across Overview, Convert, Quality, and Error Log:

- identical sidebar navigation order and icon treatment;
- shared page headers, cards, panels, badges, action buttons, and responsive tables;
- one dedicated Watcher control bar for operational controls instead of scattering them through Settings;
- compact secondary actions (`Quality`, `Routing`, `Rerun`) using the same button primitive;
- human-readable quality badges remain visually distinct from machine status values;
- mobile tables expose row labels instead of collapsing into unlabeled cells.

Future feature additions should reuse these primitives in `app/static/styles.css` rather than introducing page-specific button/card styles unless a genuinely new interaction requires one.


## UI maintenance rule

The frontend uses one canonical `:root` token block and shared action/status primitives. New features should extend those primitives rather than redeclare near-identical tokens or component selectors later in `styles.css`. Operational errors use inline status regions; native browser `alert()` dialogs are intentionally avoided.

## September 2026 UI consistency cleanup

- `styles.css` has one canonical `:root` token block. The canonical values match the values that were previously winning through CSS cascade, avoiding an unintended visual redesign.
- `.field-help`, `.document-actions`, `.mini-action`, and `.watcher-format-group` now have one base definition each; contextual/nested modifiers remain separate where appropriate.
- Overview and Document quality operational failures use inline `status-message` regions instead of native browser `alert()` dialogs.
- Failed Error Log retries restore the `Retry conversion` button label and render the failure beneath the job.
- User-facing copy avoids internal stage names: Document quality describes quality analysis, while Pi5 and OnePlus remain named only where the actual verification devices are shown.
- Manual Convert uses `VLM`, `ASR`, and sentence-case option labels.
