# Current release — 2026.09.21.40.9

## `.40.9` complete — remaining September 21 audit list
- [x] Config list-type validation.
- [x] Stage 3 table-cell correction provenance.
- [x] Equipment-scope hard allow-list before anchor/neighbor selection.
- [x] Identified blocking PDF/ledger/audit work moved off async request loop.
- [x] Conservative multi-signal Stage 3 token estimates.
- [x] Verifier HTTP lifetime audited safe and regression-tested.
- [x] Iterative/depth-bounded Docling group traversal.
- [x] Explicit verifier schema modes; no prompt-substring schema switching.
- [x] Telegram regression tests.
- [x] Deterministic claim-to-cited-evidence support audit with exact visual-value rules.
- [x] Stage 3 canonical rule version added so these output changes invalidate older Stage 3 artifacts.
- [-] SQLite/SMB finding omitted as non-applicable to the real deployment.
- Full validation before packaging: 481/481 tests pass.

# Previous release — 2026.09.21.40.8A.2

## .40.8A.2 complete Stage 2A verification-candidate coverage
- Stage 2A collects and deduplicates the complete candidate set before any route ceiling is applied.
- Existing review priority/score sorting runs globally, so late signals such as `LOW_CONFIDENCE_VISUAL` cannot be starved by earlier OCR/unicode/table noise.
- `max_routes_per_document` is now a 5000-route runaway/corruption safety ceiling, not a routine limit.
- A triggered ceiling is loud: `routes.json` records total/queued/deferred counts plus deferred breakdowns and retains every deferred candidate for audit/revalidation.
- Stage 2B queue semantics are unchanged: all queued routes enter the durable `verification_jobs` pending backlog and workers drain one job at a time.
- Quality/Book UI and Telegram monitoring expose queued/total/deferred route coverage.
- Stale/duplicate correction reconciliation across generation reruns remains a separate open correctness task; this hotfix does not claim to solve it.


## .40.8A trusted-decision and correction integrity
- Stage 2A regeneration preserves every `human_verified: true` correction-ledger entry exactly; only unreviewed automatic entries are superseded.
- Fresh ledgers use the live `STAGE2C_RULE_VERSION`; current fidelity rule is `stage2c-source-fidelity-v8`.
- Manual text→vision cross-checks are non-destructive unless a new READABLE source transcription independently passes the deterministic Stage 2C safety gate. Human and superseded state cannot be overwritten; UNREADABLE does not erase prior decisions.
- Troubleshooting action preservation is obligation-level `(verb, object-span)` with occurrence counts, not a set of verb names. Compound objects stay intact and small OCR spelling cleanup is tolerated deterministically.
- Table-cell routes now persist full Docling structural identity: table/cell plus row/column start/end spans, including merged cells.
- Deliberately deferred: table-wide repeated-value inference. Structural binding must be enforced at the overlay/application boundary in a later correctness pass rather than guessed from duplicate values.
- Full validation: 450/450 tests pass.

## .40.7.1 audit/Telegram baseline retained
- Verifier Audit remains the human visual decision gate; human decisions have highest authority.
- Telegram remains monitoring-first with the compact dashboard layout; web pipeline controls remain separate.


## .40.6.3 evidence arbitration + endpoint outage hardening
- Vision overlap rule is now quality-preserving: applied evidence wins; normal route only breaks ties. Dry-run against the uploaded processed snapshot: 162 overlaps -> 11 sweep winners + 151 normal winners, preserving the exact 11 applied-sweep/pending-normal cases found in review.
- Pi5/OnePlus connection outages use a provider circuit breaker with health-probe backoff and zero retry-budget consumption. One outage file is updated per outage.
- Older current `ConnectError` failures are automatically requeued on startup.
- Artifact sweep can be made optional for Stage 2C/RAG readiness while remaining prepared and sequenced after normal verification.
- Stale-file deletion requires a matching preview token.
- Upgrade acceptance: run **Revalidate all + rebuild** because legacy overlap suppression changes verification signatures on existing books.
- 436/436 tests pass.

## .40.6.2 sequential artifact-sweep orchestration
- Stage 2A discovery automatically prepares full technical-artifact sweep rows.
- Normal Text/Vision verification always runs first; artifact rows cannot be claimed until all current normal routes for that book complete successfully.
- Verify book/Start does not directly authorize sweep work. It arms the sweep, which releases automatically when the normal-route dependency is satisfied.
- Current normal picture routes suppress same-picture artifact-sweep jobs. Legacy overlapping sweep rows are made historical and their derived vision entries are superseded.
- Stage 2C stays blocked until the prepared sweep is complete, preserving `2A -> normal 2B -> artifact sweep -> 2C -> 3 -> retrieval -> embeddings`.
- 428/428 tests pass.


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

Deployment acceptance pending: complete the still-open transactional retrieval/freshness correctness and measured robustness work, then run Revalidate all + rebuild before the fresh N150 machine-scoped BGE benchmark and electrical holdout.

# Docling Visual RAG — Project tracker

Last updated: 2026-09-21
Current release: `2026.09.21.40.9`
Current phase: **`.40.9` September 21 audit backlog closure implemented and release-validated**
Next phase: **separately tracked retrieval transaction/freshness + Docling dead-task recovery, then Revalidate all + rebuild and fresh N150 benchmarking**

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

`Docling -> 2A -> normal 2B -> Artifact Sweep -> Stage 2C ledger -> Verifier Audit gate -> Stage 3 canonical chunks -> table/context reconstruction -> retrieval index -> machine embeddings -> Machine RAG -> optional selected generator`

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
