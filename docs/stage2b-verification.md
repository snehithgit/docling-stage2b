# Verification execution (Stage 2B)

Stage 2B executes only the `pi5` and `oneplus` routes already produced by document quality analysis. It never reconverts the source document and it never edits the raw Docling ZIP.

## Execution model

Pi5 and OnePlus have independent persistent queues. Each device has two modes:

- **Manual** — routes are discovered and wait. Pressing Start snapshots the routes currently pending for that device. Routes discovered after the press wait for the next manual batch.
- **Auto Run** — current pending routes are taken continuously.

Each device executes exactly one request at a time. Pi5 and OnePlus may run at the same time because they are separate endpoints.

Both Auto Run switches default to off and are persisted in `config.yaml`.

## Pi5 text triage

The first Stage 2B implementation intentionally does not correct OCR. It uses the Pi5 to judge whether a routed text block is likely OCR corruption:

- `LIKELY_CORRUPT`
- `LIKELY_OK`
- `UNCERTAIN`

The request contains the suspect Docling text and nearby text from the same page. The prompt forbids rewriting and outside engineering knowledge. This is designed to test whether the Pi5 can reject Stage 2A false positives before any correction workflow is introduced.

## OnePlus visual triage

The OnePlus receives the routed Docling picture artifact first as one full image and returns:

- `TECHNICAL_USEFUL`
- `DECORATIVE_OR_LOW_VALUE`
- `UNCERTAIN`

The prompt asks for only visible labels/structure and explicitly forbids inferred symbol semantics. If the full-image result is `UNCERTAIN` or says unresolved detail remains, N150 creates up to four overlapping 2x2 crops and sends them sequentially. No crop requests are sent when the full image is confidently resolved.

Defaults:

- overlap: 20%
- crop upscale: 1.25x
- maximum crops: 4

## Persistence and restart safety

Verification jobs live in the existing SQLite database in a separate `verification_jobs` table. No database reset is required.

A running job interrupted by a restart returns to pending. Connection failures, timeouts, rate limits, and server-side 5xx responses also remain pending and retry at a low frequency; an offline device does not force re-Docling or discard the route. Manual authorization is preserved, so an interrupted manual batch can resume. Auto jobs resume when Auto Run is still enabled.

Every completed verification also writes an audit JSON file under:

`processed/<document>/verification/stage2b_job_<id>.json`

It records the route, logical request, raw model response, parsed verdict, model, endpoint, processing time, and crop audit. It always states that the raw Docling source is immutable and that no correction was applied.

When Stage 2A is rerun and its routes change, Stage 2B treats that as a new route generation. Older verification results are retained as history but are no longer current. A completed Stage 2B job can also be rerun directly without rerunning Docling or Stage 2A.

## API

- `GET /api/stage2b/status`
- `POST /api/stage2b/pi5/start`
- `POST /api/stage2b/oneplus/start`
- `PUT /api/stage2b/pi5/auto-run`
- `PUT /api/stage2b/oneplus/auto-run`
- `POST /api/stage2b/jobs/{id}/retry`
- `POST /api/stage2b/jobs/{id}/rerun`
- `GET /api/stage2b/jobs/{id}/result`

## Safety boundary

Stage 2B is verification/triage only. It does not write corrections into Docling JSON, Markdown, tables, technical values, or the correction ledger. Correction approval remains a later stage after the real Pi5/OnePlus results have been evaluated.
