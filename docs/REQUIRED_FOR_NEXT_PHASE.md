# Required before the final retrieval benchmark phase

Current baseline: `2026.09.21.40.9`

The September 21 application-audit list is closed except for the explicitly omitted SQLite/SMB item, which is not applicable to the real container deployment.

Before calling the full project correctness/freshness work complete, finish the separately tracked items below:

1. **Atomic machine-index generations** — build vectors/rows/metadata as one immutable generation and switch one current pointer only after validation.
2. **Embedding-rule freshness** — persist an explicit embedding input/model/index-format fingerprint/version and invalidate stale machine indexes when it changes.
3. **Docling dead-task recovery** — a forgotten/404 task must clear the stored task ID and safely resubmit instead of retrying the dead ID forever.
4. **Table structural-binding enforcement** — when a table-cell correction overlay is consumed, verify the persisted table/cell row/column span still identifies the same source structure; do not use table-wide repeated-value guessing.

Then:

5. deploy without deleting `processed/`, `converted/`, `input/`, `data/`, equipment registry state or human audit decisions;
6. run **Revalidate all + rebuild** so `.40.9` Stage 3 rule-version changes are applied;
7. confirm TEI health and 384-dimensional `BAAI/bge-small-en-v1.5` output;
8. rebuild affected complete physical-machine embedding indexes;
9. run **Run fresh machine hybrid** and the electrical holdout;
10. inspect individual misses before adding new ranking heuristics.

## Invariants to preserve

- Raw Docling ZIP/JSON is immutable.
- Human decisions have highest authority.
- No automatic provider fallback.
- One physical machine/equipment is the normal RAG boundary.
- Current manuals form one complete machine corpus; Historical/Draft manuals remain audit-only.
- `[S#]` text and `[V#]` visual evidence retain original manual/page/chunk/artifact provenance.
- No new model/dependency should be acquired unless a controlled benchmark demonstrates a need.
