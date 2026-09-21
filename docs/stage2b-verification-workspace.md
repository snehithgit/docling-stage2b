# Stage 2B verification workspace

Version: 2026.09.11.21

`/verification` owns Stage 2B execution and processor selection.

Both logical roles expose exactly three choices:

- Text / OCR reconstruction: **Pi5 | OnePlus | Groq**
- Image / figure analysis: **Pi5 | OnePlus | Groq**

Selections persist in config. No automatic provider fallback occurs. Start/Stop and Auto Run operate on the logical queues; the worker resolves each queued route to the processor selected at execution time.

For Text routes, the result table represents source-image target reconstruction rather than semantic OCR voting. The app crops the target from the original source using Docling provenance, supplies BEFORE/AFTER Docling strings only as contextual anchors, and applies a READABLE transcription directly to the overlay when it differs. UNREADABLE preserves the original.

The **Re-read target** action uses the currently selected Image processor on the same isolated crop. Review links are optional audit/manual override.

When Groq is selected for either role, the workspace also shows the app-side Groq usage ledger and quota guard. Local Pi5/OnePlus processing is unaffected by Groq quota state.
