## After `.40.8A.1`

### `.40.8A.1` closure
- [x] Human-verified entries survive Stage 2A regeneration.
- [x] Manual cross-check respects human/superseded state and revalidates every new READABLE candidate.
- [x] Remedy/action preservation operates on counted obligations instead of verb types.
- [x] Table-cell structural row/column spans are persisted end-to-end through Stage 2A/2B correction metadata.
- [x] Exact regression cases are included; full suite 450/450.

### `.40.8B` — transactional/freshness correctness
- [ ] Publish retrieval/embedding corpus as immutable generation directories with one atomic `CURRENT` pointer.
- [ ] Add explicit Stage 3 / retrieval / embedding rule versions and fingerprints with correct downstream invalidation.
- [ ] Enforce persisted table-cell structural binding when overlays are consumed; do not infer movement from repeated values.
- [ ] Treat forgotten Docling 404 task IDs as resubmission events instead of retrying the dead ID forever.
- [ ] Add reserved/in-flight Groq quota accounting so concurrent calls cannot pass the same reserve check.
- [ ] Add direct regression tests for every `.40.8B` bug before release.

### `.40.8C` — robustness/extraction quality
- [ ] Add short-caption/nameplate/table-header OCR corruption checks without the current eight-word blind spot.
- [ ] Make normal Stage 2B claiming transactional/atomic rather than relying on one-task scheduling.
- [ ] Add bounded TEI retries for transient embedding calls.
- [ ] Warn/block conflicting multiple authoritative manuals for the same equipment/manual type.
- [ ] Improve heading-depth instability and reading-order/table reconstruction only where measured failures justify it.

### Benchmark gate
- [ ] Do **not** publish a fresh production benchmark until `.40.8B` correctness/freshness work is complete.
- [ ] After `.40.8B`/`.40.8C`, run Revalidate all + rebuild, then fresh N150 machine-scoped BGE benchmark and electrical holdout.

## After .40.6.3

- [x] Add provider circuit breaker for normal Stage 2B endpoint/connection outages; endpoint-down failures no longer consume per-job retry budgets and old ConnectError failures auto-requeue.
- [ ] Add safe Delete book lifecycle action that removes derived book state, detaches equipment assignments, and invalidates affected machine embeddings while preserving raw/converted source unless explicitly purged.

- [ ] Let the current artifact-sweep queue finish; do not count pending jobs as failures.
- [ ] Run `.40.5` deterministic legacy correction revalidation on deployment and review newly pending high-risk technical corrections.
- [ ] Deliberately assign the electrical troubleshooting manuals to real equipment scopes where applicable; do not infer physical-machine membership from title/category.
- [ ] Run fresh N150 machine-scoped BGE hybrid benchmark and save Top-1/3/5/10, MRR, per-category metrics, latency and index fingerprint.
- [ ] Run the electrical troubleshooting holdout after equipment assignment.
- [ ] Continue cross-reference and exact-value ranking only from verified fresh benchmark misses.
- [ ] Consider dense-table target-row crop retry for unresolved source transcriptions.
- [ ] Add manual revision/supersession authority workflows beyond the current groundwork.

# Project implementation TODO

Last updated: 2026-09-21
Current implementation target: `2026.09.21.40.8A.1`

## Complete in `.40.4`

### Retrieval structure
- [x] Extend safe same-table reconstruction across an adjacent page boundary.
- [x] Add nearest safe preceding table-header/continuation metadata.
- [x] Preserve canonical Stage 3 chunks and complete provenance.
- [x] Add prose-style technical/fault/procedure detection.

### Verification triage
- [x] Add deterministic per-page OCR-risk signal.
- [x] Add deterministic review-priority score inside existing high/medium/low/info bands.
- [x] Prioritize technical diagrams/fault/safety/value anomalies above decorative/low-value work within a band.

### Ranking
- [x] Weight structured IDs by likely query-subject role/position.
- [x] Add semantic intent prototype classifier using the existing BGE query vector.
- [x] Restrict semantic intent to near-tie ranking so it cannot override a clear fused leader.
- [x] Keep no extra per-query LLM call for intent detection.

### Machine embeddings
- [x] Keep one authoritative machine embedding corpus.
- [x] Reuse exact unchanged vectors from the prior machine index.
- [x] Embed only changed/new rows.
- [x] Assemble and atomically replace the full current machine corpus.
- [x] Keep machine RAG unavailable while the current corpus is stale/rebuilding.

### Manual revisions
- [x] Add revision and authority state (`Current`, `Historical`, `Draft`).
- [x] Exclude non-current revisions from normal Machine RAG while retaining audit visibility.
- [x] Add backend supersession relation groundwork.

### Benchmarking
- [x] Save expected machine ID with new expected-source cases.
- [x] Make lexical benchmark use machine scope when supplied.
- [x] Add explicit fresh machine-hybrid benchmark using current N150/TEI when deployed.
- [x] Keep old replay metrics clearly separate from a fresh run.

## Deployment acceptance — required next

- [ ] Deploy `.40.4` on N150 and preserve `processed/`, `converted/`, `input/`, `data/`, `.env`, and config.
- [ ] Rebuild/refresh any stale retrieval indexes in strict stage order.
- [ ] Rebuild affected machine embedding indexes and verify `reused_vectors` / `embedded_vectors` behavior.
- [ ] Run **Run fresh machine hybrid** against the real N150 BGE service.
- [ ] Save fresh Top-1/3/5/10, MRR, skipped cases and query latency data.
- [ ] Inspect misses before introducing stronger ranking boosts.

## Ranking work only if fresh misses justify it

- [ ] Manual-type boosts inside a machine.
- [ ] Stronger cross-reference-specific ranking.
- [ ] Remaining reconstructed table/spec ranking misses.
- [ ] Ranking-contribution explanation in the result audit.
- [ ] Create an equipment-aware holdout set; do not modify the frozen 133 for tuning.

## Revision-management follow-up

- [ ] UI control for `supersedes_postprocess_job_id`.
- [ ] Optional revision conflict warning when multiple current manuals appear to be revisions of the same document; never auto-decide authority from filename alone.
- [ ] Revision/supersession audit view.

## Later capability roadmap

- [ ] Cross-manual part/entity linking within one machine.
- [ ] Diagram callout OCR -> parts-list row linking.
- [ ] Structured maintenance-schedule extraction.
- [ ] Fault-code -> cause -> remedy graph.
- [ ] Full manual revision/supersession workflow.
- [ ] Diagrams-only retrieval mode.
- [ ] Exportable offline job-card PDF.
- [ ] QR/barcode -> physical machine RAG shortcut.
- [ ] Beelink/N150 <-> Pi5 backup/sync plan.
- [ ] Manual-gap detection by expected manual type.
- [ ] Query-log analysis for weak-evidence questions.
- [ ] Deeper upstream Docling cell reconstruction where semantics are absent from all fragments.

## Release hardening — every release

- [x] mandatory project-state docs retained in ZIP;
- [x] final full pytest on `.40.4` source tree — 398/398 pass;
- [x] Python compileall;
- [x] every static JS `node --check`;
- [x] shell `bash -n`;
- [x] YAML/Compose parse;
- [x] benchmark JSON parse;
- [x] static DOM/responsive-structure audit (viewport, skip-link/main target, responsive CSS contract); live Chromium screenshot navigation is restricted in the validation sandbox;
- [x] clean caches before ZIP;
- [x] ZIP integrity + clean extraction;
- [x] repeat validation on the exact extracted final ZIP — candidate gate passed 398/398 before final publication;
- [x] publish SHA-256 outside the archive at release publication.
