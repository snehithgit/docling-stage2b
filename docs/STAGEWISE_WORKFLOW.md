## .40.5 rule/freshness behavior

- Stage 2C is not just content-signature gated; its source-fidelity rule version is part of readiness.
- An unsafe automatic source transcription keeps immutable/original text downstream until reviewed.
- Canonical Stage 3 chunks remain immutable during retrieval-rule-only upgrades.
- Retrieval-rule changes rebuild only derived retrieval artifacts, then invalidate the affected complete machine embedding.
- Benchmark execution must use one explicit/uniquely resolved equipment scope; no normal all-books fallback is allowed.

# Stage-wise book and Machine RAG workflow

Version: `2026.09.19.40.4.2`

## Hard sequence

1. **Docling conversion** — immutable converted ZIP/JSON.
2. **Stage 2A** — deterministic quality profiling/routing plus per-page OCR-risk; source is not mutated.
3. **Stage 2B** — selected text/vision verification. Existing priority bands are refined by review-priority score; every current required route must finish successfully.
4. **Stage 2C** — correction/enrichment overlay; consumes/verifies the current Stage 2B signature. Technical-prose signals are conservative preservation/risk cues.
5. **Stage 3 HybridChunker** — consumes only completed/current Stage 2C and stores its input signature.
6. **Retrieval structural enrichment** — keeps canonical chunks intact while adding table quality metadata, safe same/adjacent-page `TBL-*` reconstruction, and table-header continuation anchors.
7. **Retrieval index** — deterministic searchable text evidence with full provenance.
8. **Machine embedding** — one complete corpus across all Current manuals assigned to the physical machine. Rebuild may reuse unchanged vectors internally, but publishes the complete corpus atomically.
9. **Machine RAG retrieval** — lexical + BGE vector RRF + structured-ID subject guard + weak semantic-intent near-tie signal, scoped only to that machine.
10. **Optional generation** — mixed `[S#]/[V#]` evidence goes to exactly one explicitly selected Pi5, OnePlus or Groq provider.

No stage may remain “ready” against newer upstream data.

## Revision authority

Each assigned manual may be Current/authoritative, Historical, or Draft. Only Current manuals are in the normal machine retrieval/embedding set. Historical/Draft copies remain auditable. Authority must be set from operator knowledge, not guessed from filenames.

## Incremental machine rebuild

If one current manual changes:

- unchanged vectors from other/current rows may be reused when their exact embedding key still matches;
- only changed/new rows are sent to TEI;
- the system assembles a full new machine matrix;
- atomic replacement occurs only after the full corpus exists;
- Machine RAG is stale/not-ready until that commit completes.

This is an optimization inside a machine corpus, not per-book production retrieval.

## Table continuity

Cross-page stitching is allowed only when chunks remain consecutive, share the exact same Docling table ref, and move at most to the adjacent page with fragmentation evidence. Ambiguous different-table-ref page continuations are not silently merged.

## UI ownership

- **My books:** whole-pipeline/machine-readiness overview and next action.
- **Book workflow:** strict per-book chain to current Stage 3 and machine-RAG readiness.
- **Extraction checks:** Stage 2A diagnostics.
- **Verification:** Stage 2B prioritized queues/results/provider controls.
- **Review:** optional audit/human override.
- **Machine RAG:** machine/revision management, embedding readiness, search, benchmark and generation.
- **Chunk Viewer:** current retrieval evidence, full provenance and original page.

## Full technical-artifact worker pool

This is intentionally separate from normal Vision verification. Normal Vision routes use exactly the selected Pi5, OnePlus, or Groq provider. `FULL_TECHNICAL_VISUAL` sweeps are large local batch work and use one shared Pi5 + OnePlus pool. An idle worker atomically claims one pending artifact, finishes it, then asks for another. Faster/healthier devices therefore complete more work naturally. A paused worker claims nothing. Transport/server failures trigger a short per-device cooldown while the other worker continues; the failed row returns to the shared pool and can be completed by either device after its retry delay. A request already in flight is never duplicated; if a device hangs mid-request, the other device continues with the rest of the pool and the stuck row becomes stealable only after the existing request liveness timeout returns it to pending.
