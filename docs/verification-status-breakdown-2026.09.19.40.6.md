# Verification Status Breakdown — 2026.09.19.40.6

## Problem

The Verification page previously summarized book work from the historical Stage 2B worker lanes (`pi5` and `oneplus`). Full technical artifact-sweep jobs (`FULL_TECHNICAL_VISUAL`) use a shared local work pool but remain stored under a historical worker target for compatibility. As a result, a book could show a large generic pending count without identifying whether the remaining work was text verification, normal vision verification, or the full technical artifact sweep.

## Change

The Stage 2B book summary now exposes logical work-type counters in addition to the existing worker-lane counters:

- `text_completed`, `text_pending`, `text_processing`, `text_failed`, `text_total`;
- `vision_completed`, `vision_pending`, `vision_processing`, `vision_failed`, `vision_total`;
- `artifact_completed`, `artifact_pending`, `artifact_processing`, `artifact_failed`, `artifact_total`.

Artifact work is identified by `code='FULL_TECHNICAL_VISUAL'`, independent of the stored worker lane. This preserves compatibility with old rows that may be stored under either Pi5 or OnePlus while accurately describing the logical work remaining.

## Verification UI

The per-book table now shows:

`Book | Text | Vision | Artifact sweep | Overall | Action`

Each stage cell displays `completed/total` plus explicit pending, processing, and failed counts. The **Verify book** action also shows a pending breakdown such as:

`Text 2 · Vision 0 · Artifact 94`

The button behavior is unchanged: it authorizes all pending verification work for that book, including the artifact sweep. Auto Run behavior and shared artifact work-stealing behavior are unchanged.

## Compatibility

The legacy `pi5_*`, `oneplus_*`, and `total` fields remain unchanged for existing API consumers and pipeline readiness logic. This release is a visibility/status correction only; it does not change route creation, model selection, verification decisions, Stage 2C, Stage 3, retrieval, embeddings, or persisted source data.
