# Current handoff — .40.8A

`.40.8A.1` is the current correctness baseline. Stage 2A regeneration now preserves `human_verified` ledger entries, manual text→vision cross-checks cannot reactivate superseded state or bypass the deterministic source-fidelity gate, and action/remedy preservation uses counted `(verb, object-span)` obligations under `stage2c-source-fidelity-v8`. Table-cell routes/crops/ledger entries now retain full Docling row/column spans, including merged cells.

Do **not** implement a table-wide repeated-value search to infer that a value moved rows; repeated technical values are normal and would cause false positives. `.40.8B` should enforce structural binding at overlay/application time using the persisted table/cell/span identity.

Next work is `.40.8B`: immutable retrieval/index generations with one atomic current pointer, explicit Stage 3/retrieval/embedding freshness versions, Docling forgotten-task resubmission, Groq in-flight quota reservations, and table structural-binding enforcement. `.40.8C` follows for measured robustness issues. Fresh production benchmarking comes only after those correctness/freshness changes.

Verifier Audit remains the human decision gate. Telegram remains monitoring-first. Raw Docling output is immutable and human decisions are highest authority.

Current release target: `2026.09.21.40.8A.1`

## Architecture

`Docling -> Stage 2A -> normal Stage 2B -> Artifact Sweep -> Stage 2C ledger -> Verifier Audit gate -> Stage 3 canonical chunks -> table/context reconstruction -> retrieval index -> physical-machine embedding corpus -> Machine RAG -> optional explicitly selected generator`

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
- Do not run the final production benchmark yet; complete `.40.8B` transactional/freshness correctness first, then `.40.8C` robustness and Revalidate all + rebuild.

## Read first

- `docs/PROJECT_TRACKER.md`
- `docs/PROJECT_COMPLETED.md`
- `docs/PROJECT_IMPLEMENTATION_TODO.md`
- `docs/PROJECT_ACQUIRED_STATE.md`
- `docs/REQUIRED_FOR_NEXT_PHASE.md`
- `docs/STAGEWISE_WORKFLOW.md`
- `docs/retrieval-structural-hardening-2026.09.18.40.4.md`
