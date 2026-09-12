# Docling Auto-Convert

**Docling Auto-Convert** is a document conversion and technical-manual verification dashboard for an existing Docling Serve installation. One Python container watches a bind-mounted input folder, queues eligible files in order, submits one conversion at a time through Docling Serve’s asynchronous REST API, persists status in a local SQLite file, and publishes live browser updates over Server-Sent Events.

Document storage, Docling conversion, Stage 2A analysis, corrections/overlays and chunk artifacts remain local. Verification provider choice is explicit and symmetric: **Text/OCR reconstruction** and **Image analysis** can each independently use **Pi5**, **OnePlus**, or **Groq**. Pi5 and OnePlus are both treated as vision-capable processors. There is no automatic provider fallback or rotation. Groq is optional. The project has no cloud storage, analytics, CDN assets, external database, or external queue service.

## What is included

| Area | Implementation |
| --- | --- |
| Automatic intake | A dedicated discovery loop watches the top level of the configured input directory even while a long conversion is running. It only creates a job after a file is stable; a separate single conversion worker processes queued files oldest-first. |
| Conversion | The worker uses `POST /v1/convert/file/async`, polls `GET /v1/status/poll/{task_id}`, then fetches `GET /v1/result/{task_id}`. |
| State | A single `jobs.db` SQLite file tracks pending, processing, completed, and failed documents, plus source size/mtime/SHA-256 identity so a changed file can be processed even when its filename is reused. Existing databases are migrated in place. |
| Interface | The overview dashboard, error log, settings drawer, and download links are plain bundled HTML, CSS, and JavaScript. |
| Recovery | A failure is recorded and never blocks later files. After restart, saved Docling task IDs are resumed instead of re-uploaded; a processing row that never received a task ID is safely returned to pending. The Error Log can re-queue failed input files. |
| Manual convert | A `/convert` page mirrors Docling Serve's own bundled options UI (Convert URL / Convert File, plus the full options panel) for one-off, on-demand conversions outside the folder-watch pipeline. |


## Stage-wise book workflow · version 2026.09.12.26

Verifier status is always visible on Verification. **Text/OCR reconstruction** and **Image analysis** each expose the same explicit selector: **Pi5 / OnePlus / Groq**. The selected provider is persisted and is the only processor used for that role until you change it. There is no automatic provider fallback. Start, Stop and per-role Auto Run controls remain explicit. The dedicated OnePlus page controls the phone llama.cpp server whenever either role is assigned to OnePlus.

The primary UI is **My books → Open workflow**:

`Docling → 2A Extraction → 2B source-image reconstruction + image analysis → 2C automatic overlay/enrichment → 3 Hybrid chunks`

- A routed OCR text block is **not semantically judged**. The app uses the Docling provenance bbox to render only that target region from the original PDF/raster source. Neighboring Docling blocks are supplied as BEFORE/AFTER location context only and are deliberately kept outside the crop.
- The selected Pi5, OnePlus, or Groq vision-capable processor transcribes only the target crop. `READABLE` + unchanged text keeps Docling. `READABLE` + different text is applied only when deterministic target-alignment safety passes; wrong-region/low-overlap transcriptions, unsafe high-risk numeric/identifier changes, truncation, partial-target reconstruction and novel token duplication preserve immutable Docling instead. `UNREADABLE` also preserves Docling and records the item for audit.
- Whole-page OCR fallback is disabled by default. Missing/unusable target coordinates therefore preserve the original rather than asking a model to guess the target on a dense page.
- Human Review is **optional audit/manual override**, not a Stage 2C or Stage 3 gate. Human-verified edits still have highest precedence and cannot be overwritten automatically.
- Stage 2C can auto-finalize after all Stage 2B routes are terminal; Stage 3 still requires a finalized Stage 2C artifact.
- Image/figure routes remain separate from OCR text reconstruction and use the independently selected Image processor.
- Raw Docling ZIP/JSON is never modified.
- Version `.21` adds a deterministic target-scope filter after transcription. BEFORE/AFTER anchor text, duplicated model output, `<think>` residue and clearly unrelated adjacent-block captures are trimmed/rejected before any automatic overlay write.
- Isolated `rare_near_frequent_token` edit-distance matches are retained as **low-priority source-image verification routes** instead of being discarded. Strong same-document corroborators remain medium/high priority. Lexical similarity alone can never apply a correction.
- An automatic `status=applied` correction is already live in `chunk_overlays.jsonl`; the Audit page now opens with that applied text and explicitly says **No Save click is required**. Save is only a human override.

- Version `.22` hardens Stage 2A for unknown manuals: empty text and geometry defects, Docling graph integrity, Unicode/PUA anomalies, table-grid validation, table-cell OCR/recall, geometry-aware running furniture, cross-page word-break evidence, honest recall-cap accounting, and optional PDF↔JSON source cross-checking.
- Table-cell OCR corrections are supported end-to-end: Stage 2B crops the exact cell, Stage 2C records `(table_index, cell_index)`, and Stage 3 applies the overlay only to the in-memory Docling JSON before HybridChunker.

Manual Text → Image re-read also uses the isolated target crop and the currently selected Image processor. Vision → Text consistency checking remains an optional audit action. Local OnePlus retains the slow-device-safe limits of 1200 seconds to first token and 300 seconds of stream idle.

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

The Quality/Verification flow keeps independent Text and Image queues. Each role can use Pi5, OnePlus, or Groq. Requests are serialized by the physical processor, so Pi5 and OnePlus can work concurrently when assigned to different roles, while assigning both roles to the same local device safely serializes them. Results are persisted in SQLite and under each document's `processed/.../verification/` directory. See `docs/stage2b-verification.md`.


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

## Stage 2C conservative correction/enrichment

Completed Stage 2B results now feed an immutable-overlay Stage 2C. Pi5 corrections require deterministic garble/evidence/fidelity gates plus re-verification; OnePlus keeps `visible_text` separate from generated object descriptions and uses structurally corroborated diagram categories. Accepted entries are written to `correction_ledger.json` and `chunk_overlays.jsonl`; the raw Docling ZIP is never modified. See `docs/stage2c-correction-enrichment.md`.

## Stage 2C empirical calibration

Real-manual validation changed two initial conservative heuristics:

- `stage2c_correction_min_garble_score` defaults to `0.12` (legacy `0.30` auto-migrates in memory). Safety still comes from the upstream corrupt/evidence/confidence gates plus correction fidelity and re-verification.
- Vision diagram corroboration uses thin-stroke erosion, filled-core survival and spatial spread instead of row/column ink occupancy, preventing bold logos/wordmarks from acting as schematic evidence.
- Pi5 evidence is bounded to one exact <=120-character suspect-text span; the old 160-token Pi5 budget migrates to 220 to prevent observed JSON truncation without encouraging verbose output.
- Legacy OnePlus `visible_labels` are retained separately as ambiguous observations and are no longer promoted into `visible_text`; legacy full-image summaries are recovered during Stage 2C backfill.
- Vision prompts explicitly classify manual/book covers, title/publisher pages and promotional pages as `cover_art` rather than `technical_photo` merely because technical equipment is pictured.


## Imported converted ZIPs and Stage 2C backfill

ZIPs dropped directly into `converted/` now appear on Overview under **All documents**. After Stage 2B verification has finished, use **Build Stage 2C** to generate the correction/enrichment ledger from the persisted verifier results. This does not rerun Docling or completed OnePlus/Pi5 verification; Pi5 is contacted again only for text items that pass the automatic correction gate.


## Fresh-run crosscheck isolation · version 2026.09.07.5

- Optional OnePlus page-image crosschecks can no longer fail an otherwise completed Pi5 Stage 2B route.
- If llama.cpp truncates after emitting complete `verdict` and `corrected_text` fields, those closed fields are safely recovered and marked `partial_response_recovered`.
- Unrecoverable JSON, phone transport errors, or an offline OnePlus are recorded as an unusable second opinion while the authoritative Pi5 verification still completes.
- The crosscheck prompt now requests exactly `verdict` and `corrected_text` and explicitly forbids verbose `reason`/explanation fields.
- `UNREADABLE` or fidelity-rejected OnePlus alternatives no longer upgrade a rejected Pi5 correction to `proposed`; the original correction status is preserved.

## Guided frontend · version 2026.09.07.5

The home page is now **My books**. Each book shows extraction checks (2A), device
verification (2B), and results (2C), with a recommended next action. Search and
attention filters help find books; readable reports show corrections and image
descriptions without requiring JSON inspection. Conversion controls are at `/queue`.

The navigation footer displays the frontend version and whether it matches
`/api/version` on the running server. A mismatch asks you to refresh. HTML is
revalidated and assets use versioned URLs. When releasing frontend changes,
update `app/version.py`, the badge in `app/static/nav.js`, and HTML asset versions
together; the test suite checks their consistency. Rebuild and restart the
container to deploy this release.



## Human before/after correction review · version 2026.09.07.5

The correction review page now presents the immutable Docling OCR text beside an editable human-corrected version, shows a live word-level removed/added diff, exposes the AI proposal only as a review aid, and saves accepted text as a human-verified Stage 2C overlay. The ledger retains the before/after text, prior status/reason, action and save timestamp; raw Docling output remains unchanged.

## Raw Docling neighbor review · version 2026.09.07.6

The manual **Review corrupted text** page is now grounded in the immutable Docling JSON rather than a Pi5 before/after presentation. For each text-correction entry it loads the exact Docling `source_index` and shows up to three non-empty text blocks **above**, the **corrupted OCR block**, and up to three text blocks **below**, restricted to the same page and preserved in Docling reading order.

The editable correction field starts from the corrupted Docling text itself; Pi5 suggestions are not prefilled into the manual review. The reviewer can use the original PDF page plus neighboring Docling blocks to reconstruct only the damaged target span, then save it as a `human_verified_manual_correction` overlay. Raw Docling JSON and the converted ZIP remain unchanged.

API: `GET /api/postprocess/jobs/{job_id}/corrections/{entry_id}/docling-context`.

## Stage 3 HybridChunker via existing Docling Serve

Stage 3 now builds `chunks.jsonl` by calling the same `docling_url` used by this project (default `http://192.168.68.63:5001`). It does not install Docling locally in this container and does not reconvert the PDF. Accepted Stage 2C text overlays are applied only to an in-memory copy of the Docling JSON, which is sent as `json_docling` to Docling Serve's HybridChunker endpoint. See `docs/stage3-docling-hybrid-chunking.md`.


## Remote HybridChunker · version 2026.09.07.9

Stage 3 uses the project's existing `docling_url` for HybridChunker. After Stage 2C, accepted text overlays are applied only to an in-memory copy of the Docling JSON and uploaded as `json_docling` to `/v1/chunk/hybrid/file/async`. No PDF/OCR reconversion occurs. The resulting `chunks.jsonl` preserves Docling headings, captions, page numbers and `doc_items`, and adds Stage 2C correction/vision provenance.


## Human correction precedence · version 2026.09.07.9

Human review is authoritative. Once a Stage 2C text-correction entry has `human_verified: true`, later automatic Pi5/Stage 2C writes for the same stable entry ID cannot replace it. Rebuilding Stage 2C therefore preserves human corrections, and rerunning the same verifier route cannot silently restore an automatic proposal over a human-approved value. Only a later human action may update that human-verified entry.

## Stage 3 table-limit + human audit cleanup · version 2026.09.07.9

- Human-reviewed corrections now set both `reason` and `status_reason` to `HUMAN_VERIFIED`; the previous automatic reason/status_reason is retained under `human_review` for audit.
- Stage 3 post-validates HybridChunker output against `stage3_chunk_max_tokens`.
- Oversized canonical Markdown tables are split only at complete row boundaries and repeat the Markdown header/separator in every child chunk.
- Non-table or otherwise unsafe-to-split chunks are never blindly cut; they are retained and explicitly flagged for audit.
- Post-split token counts are conservative estimates derived from the parent Docling tokenizer count and marked with `num_tokens_estimated=true`.
- Real Anemometer replay: 29 remote Docling chunks -> 30 final chunks; 275-token table -> 253 + 165 estimated-token children; 0 remaining over 256.

- Existing human-verified ledger entries from older builds are normalized during Build Stage 2C so stale automatic live status_reason values are cleaned without changing the human text or decision.

## 2026.09.08.12

- Fix Stage 3 terminal-state UI: persisted/in-memory `completed` HybridChunker jobs now render green and expose **Download chunks** + **Rebuild chunks** instead of another Build button.
- Keep Pi5 detection and correction separate: high-confidence `LIKELY_CORRUPT` / `OCR_GARBLE` findings can receive a conservative correction suggestion even below the 0.12 automatic-apply garble gate.
- Automatic Stage 2C application remains unchanged and still requires the strict garble/fidelity/re-verification gates.
- Human Review can optionally show a Pi5 correction candidate with **Use Pi5 suggestion**; the editor still starts from raw Docling text and the human decision remains authoritative.
- Existing completed books can use **Generate missing Pi5 suggestions** without rerunning Docling, Stage 2A, Stage 2B, or OnePlus.

## Long-running Docling conversions (2026.09.08.13)

`document_timeout_minutes` is now a **long-running warning threshold**, not a hard failure cutoff. Large OCR-heavy manuals can legitimately run for several hours on CPU. As long as Docling Serve has accepted the document and the app still has its `docling_task_id`, the worker keeps polling that same remote task and survives app/container restarts without resubmitting the PDF.

Older releases could mark a job `Timeout` after 120 minutes while Docling continued processing. On upgrade, Retry preserves a saved remote task id and resumes polling it instead of submitting a duplicate conversion. Only a terminal failure explicitly reported by Docling clears the remote task id and allows a fresh submission.

Successful result downloads use the separate `docling_result_timeout_seconds` setting (default 600 seconds), so a large returned ZIP is not constrained by the ordinary 60-second API read timeout. The Overview queue shows long-running jobs, elapsed minutes, task IDs, and conversion error details with a direct Error Log link.



## Historical: Pi5 uncertainty recovery (2026.09.08.15, superseded for live text reconstruction)

Pi5 evidence handling is now tolerant of presentation mistakes without becoming fuzzy. Overlong copied evidence, harmless wrappers such as `SUSPECT TEXT: '...'`, and truncated JSON with all required fields already closed can be recovered only when the resulting evidence is still an exact normalized span from the Docling suspect text. Invalid/context-only evidence remains `UNCERTAIN`.

Generic Stage 2A recall candidates no longer lose a source-grounded `LIKELY_CORRUPT` verdict solely because their structural garble score is low; plausible alphabetic OCR errors are expected to look structurally normal. Technical-format protection remains authoritative, and low garble still prevents automatic Stage 2C application.

That release used `LIKELY_OK / LIKELY_CORRUPT / UNCERTAIN` as the live text-verification policy. Version 2026.09.11.25 supersedes that path for new Text routes with direct target-crop source transcription. The legacy verdict/parser code is retained only for old saved-result compatibility and historical re-evaluation. New source-image reconstruction does not require a human gate. Human-verified entries remain authoritative.

## Generic Stage 2A OCR recall (2026.09.08.14)

Stage 2A now has two independent OCR candidate generators:

1. **Structural corruption scan** — replacement/control characters, numeric/unit confusions and fragmented OCR.
2. **Document-internal recall scan** — learns only from the current document and finds rare near-matches to frequent in-document tokens, suspicious split/join forms, spaced apostrophes, and corroborated variants of repeated text blocks.

The recall layer uses **no external spellchecker, no manufacturer vocabulary and no book-specific rules**. It never changes Docling text. It only identifies candidate target blocks. Version 2026.09.11.25 then re-reads those targets from isolated source-image crops with the selected Text processor; the recall heuristic itself is never allowed to invent a correction.

Default controls:

```yaml
stage2a_ocr_recall_enabled: true
stage2a_ocr_recall_max_candidates: 300
stage2a_ocr_recall_common_min_count: 5
stage2a_ocr_recall_rare_max_count: 2
```

The candidate cap bounds Pi5 load on very large manuals. Existing converted ZIPs do not need Docling reconversion; rerun **Stage 2A** to generate the new recall routes. Existing human-verified corrections remain authoritative.

## Selectable Groq/Offline verification + usage audit · version 2026.09.10.18

The Verification page exposes two independent persisted selectors:

- **Text:** Cloud · Groq (`openai/gpt-oss-20b`) or Offline · Pi5.
- **Vision:** Cloud · Groq Vision (`qwen/qwen3.8-27b`) or Offline · OnePlus.

There is **no automatic cloud↔offline fallback**. If Cloud is selected and its request cannot run, the route remains on the selected cloud path until you change the selector or retry later. Manual crossover also respects the selected opposite modality. The API key is read only from the `GROQ_API_KEY` environment variable and is never returned by settings or stored in `config.yaml`.

### Groq usage audit

Every Groq HTTP response made by this app is recorded as metadata in `/data/db/groq_quota.json`. The Verification page shows calls in the last 24 hours, success/failure counts, input/output/total tokens, paid-equivalent estimated cost, per-model/kind totals, and a recent-call table containing timestamp, model, call kind, book/route, HTTP code, token counts, latency, request ID and provider error code. **Prompts, images and API-key values are never stored in the usage ledger.** The ledger starts with calls made by this release; it does not import historical Groq-console traffic.

### Free-tier quota guard

The guard is deliberately conservative and applies to whichever selected modality uses Groq. Defaults warn at 80% and pause new Groq requests at 90% of the configured free daily request/token allowance. The request counter uses both local usage and Groq's RPD response headers. Because Groq token headers represent TPM rather than TPD, the app persists actual response token usage and protects daily usage with a conservative rolling 24-hour counter. It also checks the server-reported TPM remainder before sending another request.

When the safety reserve is reached, the current in-flight request may finish but no new Groq request starts; queued Groq routes stay pending and do not consume retry budget; roles assigned to Pi5/OnePlus and Docling can continue; and the UI shows the pause/resume state. An unexpected HTTP 429 is treated as a quota/rate pause, not an OCR failure.

```yaml
text_verifier_provider: pi5        # pi5 | oneplus | groq
vision_verifier_provider: oneplus  # pi5 | oneplus | groq
text_cloud_model: openai/gpt-oss-20b  # legacy text-only compatibility utilities
vision_cloud_model: qwen/qwen3.8-27b
text_cloud_quota_guard_enabled: true
text_cloud_free_daily_request_limit: 1000
text_cloud_free_daily_token_limit: 200000
text_cloud_quota_warn_fraction: 0.80
text_cloud_quota_stop_fraction: 0.90
text_cloud_quota_state_path: /data/db/groq_quota.json
groq_usage_log_max_entries: 2000
```

The configured free limits are safety defaults, not an entitlement check; update them if the limits shown for your Groq organization differ. For the strongest daily-token protection, dedicate the configured Groq organization/key to this application because token use made by other applications cannot be reconstructed locally.


### Source-image direct text reconstruction (.21)

Direct text reconstruction is now provider-neutral. Pi5, OnePlus, or Groq may be selected for the Text role. The app renders only the Docling target bbox from the original PDF/raster source and asks the selected vision-capable processor for an exact transcription. The response contract is `READABLE`/`UNREADABLE`, not `AGREES`/`DISAGREES`. READABLE text is applied directly when it differs; UNREADABLE preserves immutable Docling. Human-verified corrections remain authoritative and are never overwritten automatically.


## 2026.09.12.26

- Adds a deterministic **wrong-region auto-apply guard**. A direct source transcription with zero target-token recall and poor character alignment is `UNRESOLVED`; immutable Docling is preserved instead of replacing the target with a nearby label.
- Adds a **high-risk technical-token alignment guard** for numeric values, units, terminal/component identifiers and similar technical tokens. These may still be corrected, but only when the surrounding target alignment is strong enough to prove localization.
- Adds an independent **Stage 2C fail-safe**. Even if an older/saved Stage 2B result says `applied`, Stage 2C refuses to emit an overlay when deterministic source/target alignment is contradictory. Human-verified decisions remain authoritative.
- `revalidate-saved-pi5` now re-checks completed direct source-image reconstructions without model calls, so existing `.25` results can be demoted safely instead of rerunning hundreds of Pi5 requests.
- Vision results now protect line/diagram-like images from automatic exclusion. `DECORATIVE_OR_LOW_VALUE + diagram_like=true` becomes `UNCERTAIN / VISION_DIAGRAM_CONFLICT_REVIEW` unless the stronger technical-diagram corroboration rule promotes it to `TECHNICAL_USEFUL`. Stage 2C independently enforces the same protection on saved/legacy results.
- `_processed (8).zip` replay: **35 / 441** previously auto-applied text reconstructions are now conservatively blocked, including the observed `Transmilter Terminal -> +1 SW RX NIX TX` and `No.6 Operator Workstatlon -> C 220V` wrong-region cases. **17 / 25** previously excluded Vision images are restored to review because deterministic structure says they are diagram-like. Hydraulics and AD136TI retain **0** newly-blocked text corrections under the new alignment policy.

## 2026.09.11.25

- Adds a dedicated **Vision Verifier Audit** page at `/vision-audit`. It is read-only and never starts/reruns verification.
- Each completed image route shows the exact full source image, every crop actually inspected, Stage 2A route reason, selected provider/model, parsed verdict/confidence/category, visible text, model-described objects, unresolved state, deterministic structural override, raw verifier response, and downstream Stage 2C action.
- Vision result rows on the Verification page now include **Audit classification**, linking directly to the matching audit entry.
- Audit image endpoints reconstruct evidence from the immutable converted Docling ZIP; crop reconstruction uses the crop settings persisted with the original verification request.
- New verification runs persist the exact prompt used for each crop so full-image and crop prompts can both be reviewed later.

## 2026.09.11.24

- Rejects source-image transcriptions that introduce a new adjacent duplicate token (for example `on` -> `on on`) relative to immutable Docling text.
- Keeps the guard non-corrective: original Docling is preserved instead of deleting one duplicate automatically.
- Retains `.23` dynamic 512-1024 direct-transcription token budgets and truncation rejection.
