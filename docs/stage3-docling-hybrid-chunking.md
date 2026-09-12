# Stage 3 — Remote Docling HybridChunker

Stage 3 uses the **same `docling_url` configured for conversion**. The main app does not install or run a second Docling/HybridChunker stack.

Flow:

1. Read the immutable Docling JSON from `converted/<book>.zip`.
2. Make an in-memory copy.
3. Apply accepted Stage 2C `text_correction` overlays by `source_index`.
4. Keep vision enrichment as provenance metadata; never rewrite image/source text with a generated summary.
5. Upload the corrected in-memory JSON to:
   `POST {docling_url}/v1/chunk/hybrid/file/async`
   as `json_docling` input.
6. Poll normal Docling task status and fetch the result.
7. Write `chunks.jsonl` and `stage3_chunking.json` under the processed book directory.

Default options:

```yaml
stage3_enabled: true
stage3_chunk_tokenizer: "sentence-transformers/all-MiniLM-L6-v2"
stage3_chunk_max_tokens: 256
stage3_chunk_merge_peers: true
stage3_chunk_use_markdown_tables: true
stage3_chunk_include_raw_text: true
stage3_enforce_max_tokens: true
stage3_table_split_repeat_header: true
stage3_timeout_minutes: 30
```

The remote request disables OCR because the input is already a DoclingDocument JSON. It does **not** resend the original PDF and does not rerun Docling OCR/layout extraction.

Each saved chunk preserves Docling's `doc_items` and `page_numbers`, then adds Stage 2C correction provenance and page-matched vision enrichment plus the source ZIP hash and exact chunker settings.

## Oversized Markdown table safety

Docling HybridChunker can intentionally return a coherent Markdown table slightly above `stage3_chunk_max_tokens`. When `stage3_enforce_max_tokens` is enabled, Stage 3 post-validates returned chunks. Oversized Markdown tables are split only on complete table rows, and the Markdown header plus separator are repeated in every child chunk when `stage3_table_split_repeat_header` is enabled. The original Docling chunk index and parent token count remain in provenance. Non-table chunks are never blindly cut; if Docling returns an oversized non-table chunk it is retained and flagged for audit.

Post-split token counts are conservative estimates derived from the parent chunk's exact Docling token count and character density; they are explicitly marked `num_tokens_estimated=true`.
