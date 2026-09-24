# Stage 2B source-transcription hardening — 2026.09.11.23

This release fixes a real failure where a valid long Docling paragraph was shortened by a Pi5 source-image transcription that ended with `finish_reason=length`.

Changes:

- Direct source-image transcription budget is dynamic: **512–1024 output tokens**.
- General Pi5 verifier/reverification budget default is **512** tokens.
- Stage 2C Pi5 correction budget default is **512** tokens.
- Historical deployed defaults of 160/220 are migrated in memory to 512; explicit custom values are preserved.
- A direct transcription with `finish_reason=length` is always rejected and immutable Docling text is preserved.
- Long prefix/suffix-only reconstructions are rejected even if a provider incorrectly reports a clean stop.
- Existing automatic source-transcription overlays that are detectably incomplete are demoted so Stage 3 cannot chunk the shortened text.
- Target crop recovery may use the bounded physical gap between neighboring Docling blocks when the target bbox is missing or demonstrably misses the PDF native text.

No manufacturer- or book-specific vocabulary is used. Raw Docling ZIP/JSON remains immutable.
