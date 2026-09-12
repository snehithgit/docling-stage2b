# Stage 2A generic OCR recall

This stage supplements the structural OCR detector with document-internal consistency evidence.

## Rules

- No external dictionary or manufacturer/book vocabulary.
- No automatic rewrite.
- Raw Docling JSON remains immutable.
- Rare tokens are considered only when a very similar token is frequent in the same document.
- Obvious all-cap engineering labels and alphanumeric identifiers are protected.
- Adjacent parallel-language/glossary variants are suppressed.
- Split/join candidates require a visible lowercase non-function single-letter fragment and a frequent joined form in the same document.
- Repeated-block variants are corroborating evidence, not a free-standing correction rule.
- Candidates are ranked and capped before Pi5 routing.

The Pi5 prompt receives the document-internal evidence as advisory context and is explicitly told that a frequent variant is not proof. Evidence returned by Pi5 must still be a verbatim span from the suspect text.
