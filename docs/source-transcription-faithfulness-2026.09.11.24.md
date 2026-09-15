# Source transcription faithfulness hardening — 2026.09.11.24

Stage 2B direct source-image transcription now rejects a newly introduced adjacent duplicate lexical token when that repetition is absent from the immutable Docling target.

Example regression:

- Docling: `depending on the oil ' s viscosity grade`
- Pi5: `depending on on the oil’s viscosity grade`
- Result: `UNRESOLVED`, reason `NOVEL_TOKEN_DUPLICATION`; no automatic overlay replacement.

The guard is deliberately non-corrective. It never removes one copy of a repeated word because the source image may genuinely contain a repetition. It only blocks automatic application and preserves Docling until a source-faithful transcription or human review is available.

A duplicate already present in Docling is not rejected by this rule. Completion-token truncation safeguards from `.23` remain unchanged.
