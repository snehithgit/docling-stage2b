# Current release — 2026.09.19.40.6

## .40.6 verification-status visibility
- Per-book verification status is split into logical **Text**, **Vision**, and **Artifact sweep** counters.
- `FULL_TECHNICAL_VISUAL` is counted as artifact work regardless of its historical Pi5/OnePlus storage lane.
- Each stage now exposes completed/total plus pending/processing/failed; **Verify book** shows the exact pending breakdown.
- Worker scheduling, route semantics, Stage 2C/3 and retrieval behavior are unchanged.

## .40.5.1 hotfix
- Fixed Convert / Folder watcher Recent documents rendering failure caused by a stale `#failed-nav` DOM write after the sidebar badge was removed.
- Guarded the same optional element in `dashboard.js`, `convert.js`, and `errors.js`.
- Added static regression coverage for optional/removed navigation targets and defensive queue rendering.


**Focus:** Source Fidelity & Retrieval Integrity.

Completed: Stage 2C critical-token/action preservation, deterministic legacy correction revalidation, Stage 2C/retrieval rule-version freshness, persisted job-identity integrity repair, no all-books benchmark fallback, synthetic-table Top-K diversity, targeted interval/parts-item/run-in ranking, and a separate electrical troubleshooting holdout.

Deployment acceptance pending: finish the current artifact queue, allow the strict downstream chain to settle, then run a fresh N150 machine-scoped BGE hybrid benchmark and the electrical holdout after real equipment assignment.

# Docling Visual RAG — Project tracker

Last updated: 2026-09-19
Current release: `2026.09.19.40.6`
Current phase: **Retrieval structural hardening implemented and release-validated**
Next phase: **fresh N150 machine-hybrid benchmark, remaining ranking misses, and equipment-aware holdout expansion**

## Non-negotiable architecture

1. Raw Docling ZIP/JSON is immutable.
2. Human corrections have highest precedence.
3. No automatic LLM provider fallback.
4. Every derived stage consumes only a completed/current immediately-upstream stage.
5. Upstream changes invalidate affected downstream stages.
6. Canonical Stage 3 chunks remain intact; retrieval enrichment is derived and auditable.
7. Normal RAG scope is one physical machine/equipment; unrelated machines never compete.
8. One machine may contain multiple manuals while every result keeps original manual/page/chunk provenance.
9. Production hybrid vectors form **one complete machine corpus**.
10. Single-book mode is lexical/audit only; there is no normal all-books RAG path.
11. Historical/draft manual revisions are retained for audit but excluded from normal machine RAG.
12. `[S#]` is text evidence and `[V#]` is normalized visual evidence.

## Hard sequence

`Docling -> 2A -> 2B -> 2C -> Stage 3 canonical chunks -> table/context reconstruction -> retrieval index -> machine embeddings -> Machine RAG -> optional selected generator`

Readiness is signature/fingerprint based, not file-exists based.

## `.40.4.2` artifact work-stealing hotfix

- Full technical-artifact jobs form one shared local pending pool.
- Pi5 and OnePlus pull only while idle, so throughput automatically follows real device speed/availability rather than a fixed split.
- Transport/server failure cools down only the failed artifact worker; the other worker keeps draining and can claim retries.
- Manual Pause remains authoritative and Start/Retry never unpauses a hot/offline device.
- Normal Vision routes remain single-provider; only `FULL_TECHNICAL_VISUAL` uses this local shared pool.
- Actual artifact worker is recorded in request/result audit data.
- Source validation: 403/403 tests passed.

## `.40.4.1` routing hotfix

- Vision processor selection is authoritative for normal and full-artifact sweep jobs.
- Removed hidden legacy `source.processor` override from sweep execution.
- New full technical-artifact sweeps use one Vision-role queue; no Pi5/OnePlus round-robin physical assignment.
- Legacy queued sweep jobs remain compatible and follow the currently selected Vision provider.
- Regression: legacy sweep row marked `processor: oneplus` resolves to Pi5 when Vision=Pi5.
- Source validation: 399/399 tests passed.

## `.40.4` completed implementation

- adjacent-page continuation for fragmented chunks sharing the same Docling table reference;
- bounded nearest preceding table-header/continuation metadata;
- technical conditional/imperative/fault-remedy prose detection;
- deterministic per-page OCR-risk profile;
- 0–100 Stage 2B review priority score within existing priority bands;
- structured-ID subject/position weighting;
- BGE semantic-intent prototype tie-breaker using the existing query embedding, with no extra LLM call;
- one machine embedding corpus with exact-vector reuse for unchanged rows and TEI calls only for changed/new rows before atomic replacement;
- current/historical/draft manual revision authority metadata;
- equipment-aware saved benchmark cases;
- explicit fresh machine-hybrid benchmark endpoint/UI action;
- within-machine multi-manual generation remains supported and regression tested;
- `.40.3` table reconstruction, Chunk Viewer, strict sequencing, scope safety and accessibility retained.

## Retrieval runtime

- TEI: `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9`
- model: `BAAI/bge-small-en-v1.5`
- dimension: 384
- candidate depth: 60
- RRF k: 60
- exact/structured-ID guard + subject weighting
- semantic intent = near-tie ranking signal only

## Benchmark state

Historical measured references:

- fixed 133 lexical source baseline after `.40.3` table reconstruction: Top-1 72.18%, Top-3 88.72%, Top-5 92.48%, Top-10 94.74%, MRR 0.80944;
- raw BGE hybrid N150 run before later hardening: Top-1 79.70%, Top-3 90.98%, Top-5 96.24%, Top-10 97.74%, MRR 0.86456;
- structured-ID guarded deterministic candidate replay: Top-1 83.46%, Top-3 93.23%, Top-5 96.99%, Top-10 98.50%, MRR 0.89105.

The 83.46% result is **not** a fresh `.40.4` TEI run. `.40.4` adds an explicit fresh machine-hybrid benchmark path that must be run on the deployed N150 before publishing new production hybrid metrics.

## Next acceptance work

1. deploy `.40.4` while preserving processed data/config;
2. allow strict pipeline/retrieval indexes to become current;
3. rebuild affected machine indexes (unchanged vectors should be reused);
4. run **Run fresh machine hybrid** with BGE/TEI on N150;
5. inspect remaining misses by intent/manual/table class;
6. add `expected_equipment_id` to/derive it for frozen and new cases using the operator-maintained registry;
7. create a separate holdout set; never tune the frozen 133 in place;
8. consider manual-type boosts/cross-reference-specific ranking only if fresh misses justify them.

## Mandatory release documents

Every ZIP from `.40.3` onward includes current tracker/completed/TODO/acquired/next-phase/handoff/workflow state. See `docs/RELEASE_STATE_POLICY.md`.
