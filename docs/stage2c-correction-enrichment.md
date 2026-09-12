# Stage 2C — source-image text overlays and visual enrichment

Version: 2026.09.11.21

Stage 2C consumes completed Stage 2B results and never edits the converted Docling ZIP/JSON.

## Text overlay policy

New live Text routes use direct source-image target reconstruction, not semantic OCR voting:

- `READABLE` transcription equal to Docling → no text patch is necessary.
- `READABLE` transcription different from Docling → store an `applied` text-correction ledger entry with reason `SOURCE_IMAGE_TARGET_RECONSTRUCTION`.
- `UNREADABLE` or source target crop unavailable → preserve original Docling text; retain an unresolved/pending audit entry.

The selected processor may be Pi5, OnePlus, or Groq. The model sees an isolated target crop from the original PDF/raster source. BEFORE/AFTER Docling blocks are context only. Whole-page fallback is disabled by default.

Direct transcription is not subjected to a second semantic model vote. This avoids rejecting a correct visual reading merely because another text model prefers the existing OCR. Human-verified manual edits remain protected and always outrank automatic writes.

Legacy saved Stage 2B results can still contain the older `LIKELY_OK / LIKELY_CORRUPT / UNCERTAIN` triage and deterministic correction/fidelity metadata. The compatibility backfill may preserve/reconcile those historical entries, but new live Text routes do not depend on that gate chain.

## Visual enrichment

The independently selected Image processor may also be Pi5, OnePlus, or Groq. Vision output keeps source-like evidence separate from interpretation:

- `visible_text`: exact legible strings only;
- `visible_objects`: model descriptions, never treated as extracted text;
- `diagram_category`: controlled technical/decorative category;
- `summary`: model-generated visible-structure description.

A technical diagram category can override a decorative verdict only when the existing N150 structural corroborator also finds diagram-like structure. `TECHNICAL_USEFUL` images become `applied` enrichment entries, decorative/low-value images become `excluded`, and uncertain images remain `pending` without inventing source facts.

## Ledger and overlays

`correction_ledger.json` is schema v2 and records route/generation, source hash, selected processor/model, status, provenance, crop/transcription evidence and manual overrides. Stage 2A reruns preserve older entries as superseded history.

`chunk_overlays.jsonl` contains only accepted/applied overlays. Stage 3 applies them in memory before sending the corrected Docling JSON to the existing HybridChunker service. Raw Docling remains immutable.

## Human review

Human Review is optional by default. When a user manually verifies an edit, provenance becomes `human_verified_manual_correction`; later automatic reconstruction cannot overwrite it.
