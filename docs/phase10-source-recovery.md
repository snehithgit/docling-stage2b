# Source text recovery · V5.0.9.1

Stage 3 conserves complete prose omitted by HybridChunker after applying current correction overlays and source-page repairs. Each recovered chunk retains its exact corrected text, original Docling reference and one source page. Recovered chunks pass through ordinary retrieval annotation, indexing and technical detection; this is conservation of existing text, not AI correction or human validation.

Eligibility is deliberately bounded: text must belong to the document body through a proven parent chain, contain at least eight words and punctuation, have one valid source page, fit the token estimate limit, and not already be referenced or represented literally on that page. Non-warning section headings, page furniture, picture/table descendants, short fragments, unknown parent chains and oversized passages remain for review.

Existing current books can use Technical Evidence → Recover omitted text. The API defaults to preview:

```
POST /api/stage3/books/{job_id}/recover-prose
POST /api/stage3/books/{job_id}/recover-prose?apply=true
```

The endpoint requires current Stage 2C and Stage 3 output and rejects active indexing. It holds the book lifecycle lock, reloads the original source, applies current text overlays and approved page repairs, and creates a backup of changed derived files before reindexing. Correction ledgers and original archives are never written. Repeated recovery is idempotent. Corpus changes require updated embeddings before hybrid search can use the new passages.

No Docling server or GPU/model call is needed for historical recovery. Diagram relationships, table structure, unresolved OCR and semantic answer correctness still require their respective review and evaluation stages. Phase 10 remains in progress.
