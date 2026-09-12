# Stage-wise book workflow

Version: 2026.09.11.21

## State contract

1. **Docling** converts the source and writes an immutable converted ZIP.
2. **Stage 2A** validates/profile-checks the ZIP and creates candidate Text and Image routes. It does not correct source text.
3. **Stage 2B Text** uses the selected `Pi5 | OnePlus | Groq` vision-capable processor to transcribe an isolated original-source target crop. Neighboring Docling blocks are context/boundaries only.
4. **Stage 2B Image** uses the independently selected `Pi5 | OnePlus | Groq` processor for routed technical images/figures.
5. **Stage 2C** reconciles current Stage 2B results into correction/enrichment overlays. READABLE source-image target transcriptions may be applied directly; UNREADABLE targets keep immutable Docling text. Human review is optional by default and human-verified edits always win.
6. **Stage 3** applies accepted overlays in memory and calls the configured existing Docling Serve HybridChunker. Raw Docling is never rewritten.

## OCR target reconstruction

The source-image crop is the scope boundary. Stage 2B does not ask a model to decide whether a suspicious word is semantically right or wrong.

`Stage 2A candidate → Docling bbox → target-only source crop → selected processor → exact transcription → overlay/keep original`

Whole-page OCR fallback is disabled by default. Missing coordinates therefore resolve to keep-original/unreadable rather than a whole-page guess.

## Processor ownership

Both selectors expose the same choices:

- Text / OCR reconstruction: Pi5, OnePlus, Groq
- Image / figure analysis: Pi5, OnePlus, Groq

There is no automatic fallback. If both logical roles select the same physical processor, its shared lock serializes requests. Different local devices may run concurrently.

## Optional audit

Human Review is an audit/manual-override surface, not a normal progression gate. Manual Text → Image re-read uses the isolated target crop with the currently selected Image processor. Human-verified entries cannot be overwritten automatically.

## UI ownership

- **My books:** library/navigation.
- **Book workflow:** stage progression and completion state.
- **Extraction checks:** Stage 2A diagnostics.
- **Verification:** processor selection, queues, results, reruns, re-read actions and Groq usage when selected.
- **Review:** optional page/context audit and manual override.
- **OnePlus:** phone llama.cpp lifecycle controls.
