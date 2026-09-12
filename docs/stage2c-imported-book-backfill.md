# Stage 2C reconciliation/backfill for imported converted ZIPs

Version: 2026.09.11.21

Converted-folder imports are represented by their completed Stage 2A/post-process record rather than a normal watcher conversion row. **Build Stage 2C** reuses current persisted Stage 2B rows and never reruns Docling.

For current source-image reconstruction results, the backfill reuses the already stored READABLE/UNREADABLE outcome and direct correction overlay. It does not resend a completed route merely to obtain another opinion. Current visual-enrichment results are likewise reused.

Older pre-2026.09.11.21 saved results may still use legacy Pi5 text-triage/correction fields. The compatibility reconciliation path preserves those historical results conservatively rather than silently converting them into new source-image evidence.

The operation writes/upserts `correction_ledger.json`, rebuilds `chunk_overlays.jsonl`, and writes `stage2c_backfill.json` progress/completion metadata without modifying the converted Docling ZIP. Failed Stage 2B routes are never fabricated; a book with failed verification may finalize as `partial` using only available completed evidence.
