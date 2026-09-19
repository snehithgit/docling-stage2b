# Generation evidence safety — 2026.09.15.35

## Why this release exists

A real `.34` test asked `Anemometer how to set zero`. Retrieval correctly ranked the Anemometer manual first, but an all-books generation packet also contained an unrelated crane slewing-pump zero-setting procedure. The generator cited that unrelated source and produced a technically grounded-looking but wrong answer. A syntactically valid citation is not sufficient if the evidence belongs to different equipment.

## Generic safety rule

For ordinary maintenance questions, answer generation is anchored to the Top-1 retrieval result's source book. The packet contains:

1. Top-1 source chunk.
2. Adjacent Stage 3 chunks attached by procedure/troubleshooting retrieval, when available.
3. Other high-ranked chunks from the same source book.

Chunks from other books are not sent merely because they share generic words such as `zero`, `pressure`, `adjust`, or `alarm`.

Cross-book evidence remains available when the question explicitly asks to compare/differentiate/across/all manuals.

## Insufficient source extraction

If the correct manual contains only a heading such as `5.1 Zero Setting` but the actual steps are absent from searchable Stage 3 text, the generator must stop with `Not enough information in the retrieved sources.` It must not borrow a procedure from another manual. Use `+ Page` to inspect the original PDF page.

## Provider errors

Pi5 and OnePlus connection failures now identify the configured endpoint. A message such as `Pi5 is unreachable at http://192.168.68.55:8080` means the web app could not connect to that llama.cpp server; check that llama.cpp is running and that the configured IP/port is current. No automatic provider fallback occurs.

## Portable prompt

`Copy for other LLM` uses the same safety-scoped evidence selection as built-in generation, including `[S#]` labels, book/page/chunk provenance, and adjacent context.
