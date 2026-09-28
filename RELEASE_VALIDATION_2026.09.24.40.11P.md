# Release validation — 2026.09.24.40.11P

Comprehensive correctness/hardening pass based on 40.11O plus the 2026-09-24 book-corpus audits.

## Human-decision integrity / frontend
- Review save now locks queue navigation and keyboard shortcuts until persistence finishes; Alt+Arrow no longer navigates while editing.
- Text Audit ignores out-of-order/stale page responses.
- Vision/Artifact audit decision pairs lock together; navigation is blocked during a decision and late responses no longer snap the reviewer backward.
- Add-book filename output is HTML-escaped.
- Retrieval search/generation uses abort + generation/request correlation; old answers/results cannot repaint a new query/scope.
- Dashboard/errors preserve busy destructive/retry actions across SSE refreshes.
- Quality rerun handles network failures and always restores its control.
- Equipment delete has an in-flight guard; convert task position and review reason rendering are escaped.

## Ledger / Stage 2B / Stage 3
- Stage 3 overlay rebuild uses the shared Stage-2C ledger lock, rebuilds even for an empty ledger, uses unique temp files and a swap lock, and logs failures.
- Suppressed/superseded verification rows cannot resurrect Stage-2C ledger entries; Stage2B processing CAS requires is_current=1; interrupted recovery only requeues current rows.
- Stage 3 uses canonical Stage2C freshness including rule version.
- Stage2A structural HUMAN_REVIEW routes are now durable review obligations with API/UI decisions and a real Stage3/sequencer gate; diagnostic-group routes no longer collapse different codes into one route.
- R00160-style verifier token-limit/repetition failures are classified as VERIFIER_TRANSCRIPTION_TRUNCATED rather than SOURCE_IMAGE_UNREADABLE; immutable Docling text is still preserved.

## OCR/text-layer recall
- Consecutive lowercase one-letter OCR fragmentation catches the AIR COND #7266/#7267 class while uppercase engineering terminal runs remain protected.
- Document-local one-edit recall now catches damaga/damage-style errors; inconsistent rare all-cap label variants such as RELEASFD/RELEASFT are routed for source-image review without an external dictionary or automatic correction.
- Added conservative TEXT_LAYER_MAPPING_SUSPECT review routing for the observed broken-PDF-font/cmap cipher-like class (including the MacGregor `6LQJOH DQG 7ZLQ ...` case). It never auto-decodes source text.
- Large manual-like documents with zero Docling section headers emit NO_SECTION_HEADERS_DETECTED; small drawing packages are not blanket-flagged.

## RAG/source conservation
- Missing technical Docling text children under pictures are preserved as bounded provenance-rich canonical chunks when HybridChunker omitted them. Verified against real corpus exports: Anemometer +5V/25A and GRAB-SMAG 2450 Nm are recovered by the new conservation pass.
- Generic multi-page `Engineering drawing` placeholders are conservatively removed from mixed canonical chunks and citation pages are narrowed to remaining real provenance when safe.
- Exact duplicate canonical chunks are diversified only when normalized evidence, page set and Docling provenance are identical. Real GRAB query validation no longer spends top-3 on CHK-000224/235/246 duplicates.
- Near-empty table-associated chunks are classified table_empty/ineligible instead of quality-100 searchable tables.
- Retrieval index cache is explicitly invalidated when the production Stage3 writer publishes retrieval_index.jsonl.
- Retrieval status exposes current/review_pending/rebuild_pending/rebuild_failed index state and pending human-review counts; search/generation scope carries index-freshness metadata.
- Retrieval rule version advanced to v6 for the canonical-duplicate behavior.

## Lifecycle / persistence / reliability
- Managed-book deletion now uses per-book lifecycle exclusion plus a SQLite deletion reservation before quarantine. New Stage2C/Stage3 starts cannot race deletion, and pending/processing current verification blocks destructive deletion.
- Rollback remains collision-safe and manifest failures roll file moves back.
- Conversion and Stage2A status-changing writes use CAS-style guards so a deleting/changed row cannot be overwritten by a late worker.
- Equipment index writes/deletes remain off the event loop and share the query/publish lock; unique rebuild temp files prevent concurrent collisions.
- Groq resume_at now uses reservation-adjusted request/token state, matching the pause decision.
- Docling automatic watcher submission retries only unambiguous connection-establishment failures; ambiguous read/write failures are not blindly resubmitted.
- Conversion failure counts are unbounded consistently across status/errors/Telegram paths.
- Telegram delivery failures remain logged; delivery is still in-process rather than a durable outbox.
- Artifact Sweep reports pictures missing classification metadata instead of silently implying they were swept.
- Previously silent Stage2B context/notification exceptions now log diagnostics.

## Validation
- `python -m compileall -q app`: PASS
- `node --check app/static/*.js`: PASS (all JavaScript files)
- `PYTHONPATH=. pytest -q`: **573 passed, 1 skipped**
- Real-corpus spot validation:
  - Anemometer picture-child conservation: +5V and 25A recovered.
  - GRAB-SMAG picture-child conservation: 2450 Nm recovered.
  - GRAB-SMAG duplicate query `Pumpe V30D-095-BKN-L-LS-2 ohne`: one canonical duplicate remains, top-3 no longer contains the same evidence three times.
  - MacGregor broken-font sample: TEXT_LAYER_MAPPING_SUSPECT emitted for the observed cipher-like rows.
  - AIR COND: #7267 fragmentation detected; damaga and RELEASFD/RELEASFT enter document-local OCR recall.

## Deliberately not changed without stronger evidence
- NEW-2 technical-header disappearance claim was contradicted by the latest GRAB run and is not implemented.
- Generic multi-page troubleshooting-table stitching/row-repair was not changed from one isolated row-misalignment example; more source-image evidence is required before changing associations.
- Intrinsic chunk quality was not mixed with troubleshooting relevance; the earlier claimed ranking penalty was only 3.5–5.25% and query-time relevance remains separate.
- Rotated-crop retry and physical-reading-order context were plausible but not yet verified as safe general rules.
- Table-cell audit-trail v7/v8 discrepancy remains a verification item after fully current rebuilds, not a proven current write-path defect.
