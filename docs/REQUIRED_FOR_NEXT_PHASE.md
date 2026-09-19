# Required before the next retrieval phase

1. Deploy `.40.6` without deleting `processed/`, `converted/`, `input/`, `data/` or equipment registry state.
2. Allow current Stage 2B artifact jobs to finish.
3. Run **Revalidate all + rebuild** so saved automatic text corrections are checked by `.40.5` source-fidelity rules and downstream stages rebuild sequentially.
4. Confirm machine embeddings return to current/ready after retrieval refresh.
5. Run the fresh N150 machine-hybrid benchmark; do not substitute the historical candidate replay.
6. Assign electrical manuals only to known physical equipment before treating the electrical holdout as production acceptance.

# Required for next phase

Current baseline: `2026.09.19.40.6`

## No new model acquisition required

Use the existing N150 TEI CPU 1.9 + `BAAI/bge-small-en-v1.5` unless a later controlled benchmark justifies a change.

## Deployment acceptance required before stronger ranking tuning

After deploying `.40.4`:

1. confirm TEI health and 384-dimensional BGE output;
2. allow the strict pipeline to refresh any stale downstream artifacts;
3. rebuild affected physical-machine embedding indexes;
4. verify incremental build metadata (`reused_vectors`, `embedded_vectors`);
5. run **Run fresh machine hybrid** from Machine RAG;
6. record Top-1/Top-3/Top-5/Top-10/MRR and skipped cases;
7. inspect individual misses before adding manual-type or cross-reference boosts.

The old 83.46% Top-1 guarded BGE result is a deterministic candidate replay, **not** a `.40.4` fresh production result.

## Acceptance invariants

Any next-phase change must preserve:

- one physical-machine RAG boundary;
- one complete authoritative machine vector corpus;
- strict stage sequencing/freshness;
- current/manual revision authority rules;
- structured-ID protection;
- source provenance including reconstructed table source chunks;
- immutable raw Docling artifacts;
- human correction precedence;
- no automatic generator/provider fallback.

## Benchmark evolution

- Keep the original 133 cases frozen for regression continuity.
- New saved cases should record the expected machine when created in machine scope.
- Derive legacy case machine scope only from the operator-maintained registry when the expected manual has exactly one unambiguous current machine owner.
- Create a separate holdout set before aggressive ranking tuning.
- Keep specification/value, cross-page table, exact identifier, procedure, troubleshooting and cross-reference cases represented.

## Ranking candidates after fresh measurement

Only if real misses justify them:

1. manual-type boosts;
2. cross-reference-specific ranking;
3. remaining table/spec preference rules;
4. ranking explanation/audit output.
