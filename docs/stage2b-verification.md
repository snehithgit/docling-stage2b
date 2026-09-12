# Verification execution (Stage 2B)

Version: 2026.09.11.21

Stage 2B executes only routes already produced by Stage 2A. It never reconverts the source document and never edits the raw Docling ZIP/JSON.

## Explicit processor selection

The two logical roles are independent and persistent:

- **Text / OCR reconstruction:** `Pi5 | OnePlus | Groq`
- **Image / figure analysis:** `Pi5 | OnePlus | Groq`

Pi5 and OnePlus are both vision-capable. The selected processor is the only processor used for that role until the user changes it. There is no automatic provider fallback, cloud/offline rotation, or quota-key rotation.

Queues retain the historical internal target names `pi5` for Text and `oneplus` for Image only for database compatibility. User-facing labels always show the actual selected processor.

Requests are serialized by physical processor. If Text=Pi5 and Image=OnePlus, both devices can work concurrently. If both roles select Pi5, the shared Pi5 lock keeps one request at a time. Groq uses the same explicit-role rule and its quota guard.

## Text / OCR reconstruction

A routed OCR target is not sent to a language model for semantic voting. Stage 2B reconstructs it from the original source image:

1. Load the immutable Docling document and target text index.
2. Resolve the target provenance bbox.
3. Render only that target region from the original PDF or raster image.
4. Use the nearest same-page BEFORE and AFTER Docling blocks only as crop boundaries and textual location context. Their text is not deliberately included in the crop.
5. Send the isolated crop to the selected Pi5, OnePlus, or Groq vision-capable processor.
6. Ask for exact target transcription only: no summary, diagram description, grammar repair, technical inference, or semantic substitution.
7. `READABLE` + unchanged transcription keeps the original Docling text.
8. `READABLE` + different transcription writes the new text directly to the Stage 2C overlay.
9. `UNREADABLE` or unavailable target crop preserves the original Docling text and records the unresolved state for optional audit.

Whole-page fallback is disabled by default. If a target has no usable bbox, the system preserves Docling instead of showing a dense whole page and asking the model to infer which region was intended.

The crop renderer supports PDF and common raster-image sources. Raw Docling remains immutable.

## Image / figure analysis

Image routes remain separate from OCR reconstruction. The selected Image processor receives the routed Docling picture artifact and returns only visible evidence plus a compact classification. If a full image remains unresolved, the existing overlapping-crop path may inspect subregions. Model-generated object descriptions and summaries remain distinct from source-visible text.

## Human review

Human Review is optional audit/manual override by default (`stage2c_require_human_review: false`). It does not block Stage 2C or Stage 3. A human-verified correction always has higher precedence than automatic entries and cannot be overwritten by later model runs.

## Manual re-read / crossover

For a completed Text route, **Re-read target** sends the same isolated target crop to whichever processor is currently selected for the Image role. A readable transcription may update the unreviewed overlay directly. It cannot overwrite a human-verified edit.

For a completed Image route, the optional Text consistency action compares the extracted visual result against same-page Docling text. It is audit-only and does not constitute visual confirmation.

## Persistence and restart safety

Verification jobs live in the existing SQLite `verification_jobs` table. Running work interrupted by restart returns to a recoverable state. Retryable connection failures and rate limits do not cause Docling reconversion. Completed verification writes an audit JSON under:

`processed/<document>/verification/stage2b_job_<id>.json`

The audit stores the logical request, selected processor, crop metadata/hash (not a second mutable source), parsed result, endpoint/model, timing and provenance. The original Docling ZIP is never changed.

## API

- `GET /api/stage2b/status`
- `PUT /api/stage2b/providers/text`
- `PUT /api/stage2b/providers/vision`
- `POST /api/stage2b/pi5/start` — starts the logical Text queue
- `POST /api/stage2b/oneplus/start` — starts the logical Image queue
- `PUT /api/stage2b/pi5/auto-run`
- `PUT /api/stage2b/oneplus/auto-run`
- `POST /api/stage2b/jobs/{id}/retry`
- `POST /api/stage2b/jobs/{id}/rerun`
- `POST /api/stage2b/jobs/{id}/crosscheck`
- `GET /api/stage2b/jobs/{id}/result`

The historical route names in these URLs are retained for compatibility; processor selection is independent of them.
