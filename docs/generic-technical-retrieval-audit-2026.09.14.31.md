# Generic technical retrieval audit · 2026.09.14.31

This release improves retrieval and source auditing without changing Stage 2A, Stage 2B, Stage 2C, or raw Docling content.

## Generic identifier-aware ranking

Definition-style queries (for example `What is K17?` or `What does 2141 refer to?`) prefer exact standalone identifiers and same-row/table definitions over substring occurrences inside longer codes such as `K17-104` or `2141-101`. Common engineering measurements are not treated as identifiers merely because they contain digits.

## Cross-reference following

Explicit references such as `See instruction "High pressure pumps" in section 6.1` are extracted generically. `Follow reference` searches only the same book and strongly prefers the referenced title/heading; the section number is supporting evidence, not the primary global search term.

## + Page source viewer

Retrieval results and followed references expose `+ Page`. The viewer renders the original source PDF page. When the result has usable Docling provenance, the Docling source items are outlined on the rendered copy so the operator can visually confirm what retrieval is seeing. If provenance is unavailable, the full original page is still shown without inventing a highlight. Raw PDFs and Docling ZIPs remain unchanged.
