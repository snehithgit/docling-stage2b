# Visual RAG evidence — 2026.09.16.38

## Goal

Consume the completed `.37` technical-artifact sweep without rerunning Docling or vision. Persist normalized, auditable visual evidence and use it together with Stage 3 text for retrieval and grounded generation.

## Derived visual evidence

For each Stage 2C `vision_enrichment`, `.38` writes derived records to:

- `visual_evidence.jsonl` — every normalized visual record, including excluded/unresolved rows for audit.
- `visual_evidence_index.jsonl` — only RAG-eligible visual records.
- `visual_evidence_summary.json` — eligibility totals/reasons.

This normalization is deterministic and makes **zero Docling, Pi5, OnePlus, or Groq calls**. Raw Docling remains immutable.

Stable IDs use `V-<postprocess_job_id>-<picture_index>`, for example `V-7-000012`.

## Eligibility policy

A visual is automatically searchable only when all of these are true:

1. Stage 2C status is `applied`.
2. The verifier verdict is `TECHNICAL_USEFUL` (when present).
3. `unresolved` is false.
4. At least one useful visible-text/object/summary field exists.

Pending, uncertain, unresolved, decorative, excluded, or empty visual records remain visible for audit but are not trusted by RAG.

## Evidence model

- `[S#]` — Stage 3 text evidence.
- `[V#]` — normalized visual evidence from a Docling picture artifact.

`[V#]` keeps explicit provenance: book, page, picture index, Docling picture ref, artifact path, Stage 2C state, verification job/route/model/provider when recorded, source hash, and raw-Docling immutability.

`.37` did not ask the vision model to emit rich controls/relationships/procedure arrays, so `.38` leaves those fields empty instead of inventing data. Existing `visible_text`, `visible_objects`, category and summary are preserved faithfully.

## Retrieval

Text and visual evidence are searched separately. Ordinary questions remain scoped to the anchor/Top-1 book; explicit comparison/across-manual questions may search across books. Relevant text pages provide only a weak visual page-affinity boost after lexical matching — same-page proximity alone never makes a visual eligible.

Procedure/troubleshooting text expansion is also tightened. A nearby chunk is included as generation context only when it shares a Docling item or the same deepest heading/section. Raw adjacency alone is no longer enough.

## Generation safety

The generator receives a mixed evidence packet with stable `[S#]` and `[V#]` labels. When visual evidence exists, up to two source slots are reserved for it so weak same-book text does not crowd out a relevant technical artifact.

Prompt policy distinguishes visual fields:

- `visible_text` = model-read source text from the image.
- `visible_objects` and `summary` = model-generated interpretation.

Exact values, identifiers, switch positions, directions, limits, or procedures must not be asserted from visual interpretation alone unless the exact item is present in `visible_text` or corroborated by `[S#]`.

`Copy for other LLM` exports the same mixed `[S#]/[V#]` packet and makes no model call.

## UI/audit

- RAG quality shows the number of eligible visual records and matched visual artifacts.
- Generated answers show the exact `[S#]/[V#]` records sent to the model.
- Artifact Audit shows each picture's stable visual-evidence ID, RAG eligibility, and exclusion reason.
- Pi5 picture jobs on the legacy Verification page link to Vision Audit, not Text Audit.

## Pi5 picture source-type fix

Logical evidence type is now separated from physical worker. A Pi5 job whose source type is `picture` is treated as vision evidence for ledger lookup/manual cross-check persistence, preventing `generation:text:AV...` lookups for entries actually stored as `generation:vision:AV...`.

## Validation

Release validation must include:

1. `PYTHONPATH=. pytest -q`
2. `python -m compileall -q app tests scripts`
3. `node --check` for every `app/static/*.js`
4. `bash -n` for every shell script
5. clean caches/bytecode
6. ZIP integrity test
7. clean extraction and the same checks on the exact packaged copy
8. SHA-256 calculation
