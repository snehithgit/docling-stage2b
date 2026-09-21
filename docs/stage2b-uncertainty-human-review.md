# Optional Human Review and unresolved text

Version: 2026.09.11.21

Human Review is no longer mandatory for the live source-image reconstruction path. It remains available for audit and manual override.

For new Text routes:

- source crop `READABLE`, same as Docling → keep original;
- source crop `READABLE`, different → apply transcription to Stage 2C overlay;
- source crop `UNREADABLE` or target crop unavailable → keep original and mark unresolved for audit.

Stage 2C/Stage 3 may continue when `stage2c_require_human_review: false`, which is the default. Human-verified edits remain authoritative and are protected from automatic overwrite.

Older saved results may still contain `LIKELY_OK`, `LIKELY_CORRUPT`, or `UNCERTAIN` from the legacy text-judgement pipeline. Those fields are retained for compatibility and historical inspection; they do not define the new live reconstruction method.
