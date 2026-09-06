# Docling Auto-Convert

**Docling Auto-Convert** is a local-only document conversion dashboard for an existing Docling Serve installation. One Python container watches a bind-mounted input folder, queues eligible files in order, submits one conversion at a time through Docling Serve’s asynchronous REST API, persists status in a local SQLite file, and publishes live browser updates over Server-Sent Events.

The container makes no network calls other than the configured Docling Serve URL. It has no application login, cloud storage, analytics, CDN assets, external database, background queue service, or second worker container.

## What is included

| Area | Implementation |
| --- | --- |
| Automatic intake | A dedicated discovery loop watches the top level of the configured input directory even while a long conversion is running. It only creates a job after a file is stable; a separate single conversion worker processes queued files oldest-first. |
| Conversion | The worker uses `POST /v1/convert/file/async`, polls `GET /v1/status/poll/{task_id}`, then fetches `GET /v1/result/{task_id}`. |
| State | A single `jobs.db` SQLite file tracks pending, processing, completed, and failed documents, plus source size/mtime/SHA-256 identity so a changed file can be processed even when its filename is reused. Existing databases are migrated in place. |
| Interface | The overview dashboard, error log, settings drawer, and download links are plain bundled HTML, CSS, and JavaScript. |
| Recovery | A failure is recorded and never blocks later files. After restart, saved Docling task IDs are resumed instead of re-uploaded; a processing row that never received a task ID is safely returned to pending. The Error Log can re-queue failed input files. |
| Manual convert | A `/convert` page mirrors Docling Serve's own bundled options UI (Convert URL / Convert File, plus the full options panel) for one-off, on-demand conversions outside the folder-watch pipeline. |

## Manual convert page

Open `/convert` from the sidebar to convert a single URL or file on demand, independent of the watched-folder pipeline. It exposes the same per-request options as Docling Serve's own UI:

- **To Formats** — Docling (JSON), Markdown, HTML, Plain Text, Doc Tags (multi-select)
- **Watcher format snapshots** — choose any one/two/three/etc.; each queued file keeps exactly the formats selected when it was discovered, and Live Queue shows them all
- **Image Export Mode** — Embedded, Placeholder, Referenced
- **Pipeline type** — Legacy, Standard, Vlm, Asr
- **OCR** — Enable OCR, Force OCR, and an OCR Engine choice (Auto, EasyOCR, Tesseract, RapidOCR)
- **PDF Backend** — `docling_parse`, `pypdfium2`
- **Table Mode** — Fast, Accurate
- **Infer heading levels**, **Abort on Error**
- **Enrichment** — code enrichment, formula enrichment, picture classification, picture description

There is no "Return as File" toggle: this page always submits with a zip target and always ends with a `Download converted_docs.zip` link once the task succeeds, so that switch has nothing left to control. Submissions go through `POST /v1/convert/file/async` (for uploads) or `POST /v1/convert/source/async` (for URLs) on your configured Docling Serve instance, independent of the automatic pipeline's settings, and are not persisted to `jobs.db` — they're ephemeral, one-off conversions the same way Docling Serve's own UI works.

## Quick start on ZimaOS

First copy the configuration template and change `docling_url` to the LAN address of your existing Docling Serve box. The supplied settings screen can later update the Docling URL, input folder, output folder, and desired output format directly in this same configuration file.

```bash
cp config.example.yaml config.yaml
mkdir -p input converted data
docker compose up --build -d
```

Open `http://<ZimaOS-host>:8080`. Drop supported documents directly into `input/`; they are discovered and queued but, by default, **do not start Docling automatically**. On Overview press **Start queued files** to run the current queue, or enable **Auto Run** for continuous operation. Completed files are saved under `converted/`, while persistent state is saved in `data/jobs.db`.

## Output modes

The default `target_type: zip` writes the first version as `{filename-stem}.zip`. If a different source later reuses the same filename, its SHA-256 identity is used to preserve the earlier output and write a distinct name such as `{filename-stem}__a1b2c3d4.zip`. Change `target_type` to `inbody` if you prefer directly downloadable `.md`, `.json`, or `.text` files. The settings panel exposes watcher `to_formats` as a true multi-select. You can choose any one, two, three, or more supported formats; each discovered job snapshots that exact set and the live queue displays all selected formats. The panel does not change `target_type`, OCR, table mode, pipeline, timeouts, or extension allow-list, which intentionally remain explicit deployment configuration.

| Desired output | Set `to_formats` | Direct-output extension when `target_type: inbody` |
| --- | --- | --- |
| Markdown | `['md']` | `.md` |
| JSON | `['json']` | `.json` |
| Plain text | `['text']` | `.text` |

## Operational behavior

The conversion worker processes **exactly one document at a time**, while an independent discovery task continues scanning the input directory. The default execution mode is **Manual Start** (`watcher_auto_run: false`): files can accumulate safely as `pending` until **Start queued files** is pressed. A Start press snapshots only the files currently pending and processes that batch **smallest file first → largest file last**. Files discovered after the snapshot wait for the next Start. **Auto Run** can be toggled on from Overview; it is persisted and continuously selects the smallest pending file next. Turning Auto Run off lets the current Docling task finish, then pauses before another submission. Unsupported files are ignored. A failed conversion remains visible on the Error Log and is not automatically retried; retrying from the UI increments its retry count and reuses the same source identity.

Automatic uploads stream the source file from disk rather than calling `read_bytes()`, avoiding a full extra in-memory copy of large scanned manuals. New jobs store source size, nanosecond mtime, and SHA-256. Unchanged files use the cheap size/mtime fast path on later discovery passes, so retained large inputs are not re-hashed every few seconds. Older filename-only database rows are backfilled once during normal discovery.

Completed files are written to a hidden `.part` file, flushed to disk, and validated before `os.replace()` atomically exposes the final filename. ZIP results must be a readable, non-empty ZIP whose CRC test passes. An invalid or interrupted result never replaces a previously valid output.

The application checks Docling Serve’s `/health` and `/ready` endpoints frequently so the dashboard distinguishes a reachable-but-not-ready server from one that cannot be reached. If the app restarts while Docling is converting, a saved `docling_task_id` stays in `processing` and the worker resumes polling it. A processing row with no saved task ID is re-queued as `pending` because there is no remote task to resume.

## Local verification

Create an isolated environment, install the requirements, then run the included standard-library test suite and server.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
cp config.example.yaml config.yaml
uvicorn app.main:app --reload --port 8080
```

## Supported extensions

The default allow-list is declared in `config.yaml` and covers PDFs, Office files, CSV, text/HTML/Markdown, and common raster-image formats. The application only inspects files directly inside the input folder; it deliberately does not recurse into subfolders.

## API reference

The client follows the official Docling Serve REST API flow for asynchronous file conversion: upload using a `files` multipart field and form-based options, wait for a `task_status` of `success` or `failure`, then retrieve the result. See the [Docling REST API documentation](https://docling-project.github.io/docling/usage/api_server/rest_api/) for the full live schema and additional conversion options.


## Stage 2A: quality profiling and routing

Completed Docling ZIPs now automatically enter a separate, non-destructive quality queue. Results are written under `./processed` and can be reviewed at **http://HOST:8080/quality**. The raw converted ZIP is never modified.

Stage 2A produces `profile.json`, `diagnostics.json`, `routes.json`, `source_manifest.json`, `integrity.json`, `coverage.json`, `summary.json`, and an empty `correction_ledger.json`. Diagnostics include document-internal heading-hierarchy consistency and a conservative layout-aware reading-order check; deep level-6 headings are not treated as errors merely because of their depth. Validation coverage is explicit, so zero anomalies with poor checker coverage is reported as `limited`/`not_evaluable` rather than clean. ZIP CRC and referenced-artifact integrity are checked before visual routing. Pi5 and OnePlus endpoint health is displayed, while external inference remains disabled by default until routing has been validated. See `docs/stage2-quality-router.md` and `docs/validation-coverage.md`.


### Human-readable quality labels

Stage 2A keeps machine status codes stable for routing and APIs, while adding `display_label` fields for the Quality dashboard and generated reports. For example, `limited` remains the internal value but is displayed as **Not enough checked to be sure**. Human labels are presentation-only and are never sent to Pi5/OnePlus unless explicitly included in a future task prompt.

## GRAB-SMAG regression hardening

This build also includes conservative generic OCR-fragment detection, non-prose reading-order exclusions for form/title/contents/technical-visual pages, and requested-vs-returned watcher format validation. See `docs/grab-smag-regression-fixes.md`.

## Stage 2 rerun and converted-folder import

The Quality & Routing worker no longer depends only on conversion rows already present in `jobs.db`.

- Drop any valid Docling ZIP directly into the configured `output_dir` / `converted/` folder. The worker validates the ZIP, registers it as an **Imported ZIP**, and sends it through Stage 2A automatically.
- Watcher-created ZIPs are never duplicated as imports; an existing watcher job owns its reserved output filename.
- Imported ZIPs do not inflate the main PDF conversion counters/history.
- Completed documents expose **Quality**, **Routing**, and **Rerun** actions in the Quality page. Watcher jobs also expose the Stage 2 actions from the live Documents/Recent documents table once analysis is complete.
- **Rerun** is Stage-2-only. It reuses the existing converted ZIP and regenerates the Stage 2 artifacts with the currently installed code. It does not submit the PDF to Docling again and does not require deleting `jobs.db`.
- Reruns are serialized by the existing Stage 2 worker and are restart-safe. A job already pending/processing cannot be queued twice.
- Unrelated/non-Docling ZIPs in `converted/` are ignored. Their unchanged file signatures are cached so they are not repeatedly re-inspected every poll.

The generated `source_manifest.json` includes `source_kind: "watcher"` or `source_kind: "converted_folder"` so downstream audit logic can identify how the Docling source entered Stage 2.

### Troubleshooting structure detection fix

Stage 2A now recognizes common troubleshooting section-heading variants including `Troubleshooting`, `Troubleshooting Chart`, `Fault Finding`, `Possible/Probable Cause`, `Corrective Action`, `Symptom`, and `Remedy`. Detection stays scoped to Docling section headings to avoid broad false positives from ordinary body prose. The MacGregor CC3000 troubleshooting guide regression now reports `detected_structures.troubleshooting: true`.

## Watcher controls and UI consistency

Overview now contains a dedicated watcher control bar with **Start queued files** and an **Auto Run** switch. Queue ordering is always **smallest first**, and Docling remains serialized to one conversion at a time. The execution controls are deliberately separate from Settings, which is reserved for folders, connection details, and output-format selection.

The UI was also consolidated into one shared component system across Overview, Convert, Quality, and Error Log so new functionality does not keep changing the visual language. See `docs/watcher-execution-ui.md`.

### UI consistency baseline

The frontend now keeps one canonical design-token block, shared action/status primitives, inline operational feedback, and stable user-facing terminology. Internal quality/routing status codes and backend behavior are unchanged.

## Verification execution

The Quality page now includes independent Pi5 text and OnePlus vision verification queues. Both default to manual start and support Auto Run. Requests are serialized per device, while Pi5 and OnePlus can work concurrently. Results are persisted in SQLite and under each document's `processed/.../verification/` directory. See `docs/stage2b-verification.md`.


## Dedicated verification workspace

Stage 2B verification now lives at `/verification`. It provides per-book manual verification, a master Auto verify all switch, independent Pi5/OnePlus Auto Run controls, and complete remaining queues. Retryable OnePlus/Pi5 failures use per-job exponential backoff so one slow route cannot starve later work. See `docs/stage2b-verification-workspace.md`.

## Stage 2B polling reliability update

The Verification workspace now keeps read-only polling separate from route discovery. GET status/queue/book calls never rescan Stage 2A route files. Route synchronization is coalesced and cached by file signature, frontend polling cannot overlap, Verify book wakes routes currently delayed by retry backoff, and Stage 2B retry/failure details are emitted to container logs. See `docs/stage2b-polling-reliability.md`.

## OnePlus server controller

This build adds a dedicated `/oneplus` page for the Termux vision server. The phone only needs its SSH server running; the web app can Start, Stop, Restart, capture the currently running llama.cpp configuration, discover GGUF models, check `/health`, and view the remote llama-server log.

Before using the controller, copy `.env.example` to `.env` and set `ONEPLUS_SSH_PASSWORD` to the Termux SSH password. The password is never exposed to the browser or stored in `config.yaml`. The controller ships with the proven Qwen3.5 2B Q8 + mmproj launch preset already filled in, including `-t 4 -tb 6 -c 4096 -np 1`, reasoning disabled, and `--image-max-tokens 1024`. See `docs/oneplus-server-control.md`.


### OnePlus SSH controls

The OnePlus controller page includes **Reconnect SSH** and **Stop SSH**. Stopping SSH does not stop llama-server; it only removes remote control access until `sshd` is started manually in Termux again.


### Stage 2B verifier controls
The Verification workspace has persistent Stop verifier controls for Pi5 and OnePlus and shows completed/failed results only. The OnePlus controller launches llama.cpp detached from SSH and requests a Termux wake lock while the server is running, so page navigation or sshd restarts do not intentionally stop the model server.

## OnePlus phone-script controller

OnePlus llama.cpp lifecycle is deliberately phone-owned. The web app only SSHes
to Termux and invokes `$HOME/bin/oneplus-llama-control` with `start`, `restart`
or `stop`. Use the OnePlus page's **Install / update script** button once after
upgrading. The bundled script contains the validated model command and handles
the Termux wake lock locally on the rooted phone.

## OnePlus bundled control script
The Docker image includes `mobile/oneplus-llama-control` at `/app/mobile/oneplus-llama-control`.
The OnePlus page's **Install / update script** action sends this bundled script over SSH to `$HOME/bin/oneplus-llama-control` on the phone.
Rebuild the image after upgrading this project so the bundled script is present in the container.

## OnePlus script-control packaging + UI fix

- The Docker image now copies `mobile/oneplus-llama-control` to `/app/mobile/oneplus-llama-control`, so **Install / update script** works inside the container.
- The OnePlus page uses the same canonical page header, cards, buttons, badges and sidebar footer as the rest of the app.
- Server commands are a three-column equal-width action row on desktop and stack on small screens.
- SSH actions are a two-column equal-width row on desktop and stack on small screens.
- Legacy model-browser/process-inspection UI classes were removed from the simplified script-only page.
