# Stage 2A accuracy hardening — 2026.09.11.22

This release keeps Stage 2A deterministic and book-agnostic. Raw Docling archives remain immutable.

## Added

- Empty-text, missing/degenerate/out-of-page bbox and provenance diagnostics.
- Docling graph integrity checks: broken references, cycles, unreachable text.
- Unicode replacement/private-use/control/noncharacter/placeholder diagnostics plus mixed-script review.
- Table span/overlap/sparse-grid validation.
- OCR and document-internal lexical recall across table cells.
- Weak isolated lexical candidates are low-priority source-image verification routes; strong corroborated candidates remain higher priority.
- Strong-first recall budgeting with explicit eligible/emitted/omitted counts. Default recall candidate cap increased to 300.
- Geometry-aware running header/footer candidates and cross-page word-break evidence.
- Explicit limited coverage when most headings cannot be hierarchy-validated.
- Optional source-PDF page-count, geometry, native-text coverage and image-page checks. If page counts differ, page-aligned comparisons are withheld.
- Route de-duplication and priority upgrading so one source target does not waste multiple device calls.
- Table-cell corrections flow through Stage 2B → Stage 2C → Stage 3 using table/cell provenance.

## Safety

- No manufacturer dictionaries or book-specific rules.
- No lexical auto-correction.
- Vision source crop remains authoritative for text reconstruction.
- Unreadable/unsafe targets preserve original Docling text.
- Human-verified entries retain highest precedence.
- Stage 3 still uses corrected in-memory Docling JSON and never rewrites the converted archive.
