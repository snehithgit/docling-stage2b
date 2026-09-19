# Current handoff — .40.6

`.40.6` fixes Verification-page visibility: per-book work is now separated into Text, normal Vision, and the full technical Artifact sweep, with explicit pending/processing/failed counts. `FULL_TECHNICAL_VISUAL` is classified by route code rather than its historical worker lane, so old Pi5/OnePlus rows remain compatible without misleading the operator.

`.40.5.1` is a UI hotfix on top of `.40.5`: the Convert / Folder watcher Recent documents table no longer crashes when the removed optional `#failed-nav` badge is absent. The same stale reference is guarded on Manual Convert and Errors pages.

Current architecture:
`Docling -> Stage 2A -> Stage 2B -> Stage 2C source-fidelity gate -> Stage 3 canonical chunks -> table/context reconstruction -> retrieval index -> physical-machine embedding corpus -> Machine RAG -> optional selected generator`.

`.40.5` protects source-critical tokens and troubleshooting actions from unsafe automatic correction, revalidates legacy automatic overlays without LLM calls, versions Stage 2C/retrieval rules, refreshes retrieval-only artifacts when software rules change, audits persisted job identity, prevents benchmark all-books fallback, diversifies duplicate `TBL-*` results and adds an electrical troubleshooting holdout.

The uploaded corpus dry-run identified 64 unsafe/pending legacy corrections; human-verified entries remain untouched. Fresh N150 hybrid acceptance must be run after the user's pending artifact jobs settle.

# New-chat handoff — Docling Visual RAG

Current release target: `2026.09.19.40.6`

Continue the existing project; do not redesign completed pipeline stages or reacquire data/models without evidence.

## Architecture

`Docling -> Stage 2A -> Stage 2B -> Stage 2C -> Stage 3 canonical chunks -> table/context reconstruction -> retrieval index -> physical-machine embedding corpus -> Machine RAG -> optional explicitly selected generator`

Every downstream stage must consume a completed/current immediately-upstream stage. Upstream changes invalidate affected downstream artifacts.

## Machine RAG

- Production embeddings are one complete corpus per physical machine across all **Current/authoritative** manuals.
- Historical/Draft revisions stay auditable but are excluded from normal Machine RAG.
- `.40.4` may reuse unchanged vectors internally during rebuild, but it still atomically publishes one complete machine index.
- Single-book scope is lexical/audit only. No normal all-books RAG exists.
- Generation may combine multiple manuals only inside the explicitly selected machine.

## `.40.4` additions

- adjacent-page same-Docling-table reconstruction;
- bounded table-header/continuation anchors;
- technical conditional/imperative/fault-remedy prose signal;
- page OCR-risk + 0–100 verifier review-priority ordering inside existing bands;
- structured-ID subject/position weighting;
- BGE semantic-intent near-tie signal using cached prototypes and the existing query embedding;
- incremental vector reuse for machine rebuilds;
- manual revision/authority metadata;
- equipment-aware benchmark cases;
- explicit fresh machine-hybrid benchmark action for deployed N150/TEI.

## Benchmark references

- `.40.3` frozen 133 lexical source result: Top-1 72.18%, Top-3 88.72%, Top-5 92.48%, Top-10 94.74%, MRR 0.80944.
- historical structured-ID guarded candidate replay: Top-1 83.46%, Top-3 93.23%, Top-5 96.99%, Top-10 98.50%, MRR 0.89105.
- Do **not** call 83.46% a fresh `.40.4` N150 result.
- Next deployment action is to run the new fresh machine-hybrid benchmark on the actual N150.

## Read first

- `docs/PROJECT_TRACKER.md`
- `docs/PROJECT_COMPLETED.md`
- `docs/PROJECT_IMPLEMENTATION_TODO.md`
- `docs/PROJECT_ACQUIRED_STATE.md`
- `docs/REQUIRED_FOR_NEXT_PHASE.md`
- `docs/STAGEWISE_WORKFLOW.md`
- `docs/retrieval-structural-hardening-2026.09.18.40.4.md`
