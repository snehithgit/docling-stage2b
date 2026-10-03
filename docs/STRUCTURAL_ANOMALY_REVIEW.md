# Structural and table anomaly review (AH8)

The Anomalies page now combines Stage 2C text/vision corrections with current Stage 2A human-review routes. No correction ledger or Stage 2A rerun is needed for existing structural findings to appear. Both pending and resolved/dismissed human routes appear so operators can request a fresh Colab audit of earlier human decisions.

Included structural codes are those already generated as human-review routes: TABLE_ROW_COLLAPSE, TABLE_GRID_ANOMALY, READING_ORDER_ANOMALY, DOCLING_GEOMETRY_ANOMALY, HEADING_HIERARCHY_INCONSISTENCY and DOCLING_GRAPH_INTEGRITY. Other human-route codes use the same generic structural audit path. Informational/rule-only signals are not converted into new structural blockers by this change.

Individual and batch Colab actions enqueue `anomaly_structural` jobs on the existing assigned anomaly workers. Primary verification remains higher priority. Jobs share existing deduplication, provider reservation, retry limits, failed-job recovery and lifecycle protections. The Review workers totals include structural jobs.

A structural worker inspects every known relevant PDF page, together with diagnostics, immutable table matrices and existing human repairs. Page-less findings use diagnostics-only text review and always retain NEEDS_HUMAN: they cannot claim that the source was visually verified. Missing source pages or truncated/invalid model outputs fail visibly rather than publishing an incomplete audit. Very large evidence exceeding the prompt limit remains for manual review.

Table proposals require a rectangular matrix of strings, an identified source table and a complete single-page table. Partial proposals for multipage tables are rejected and retain NEEDS_HUMAN. Valid proposals are displayed as copyable TSV. Operators still must compare the source and explicitly save an approved repair through the existing human workflow. Existing repaired/resolved routes remain human-owned; this change does not reopen their save controls or apply new AI matrices automatically.

Advisory results/history live in `structural_anomaly_reviews.json`, separate from `routes.json`, `table_structure_repairs.json` and `docling_page_repairs.json`. Before publication, the worker rechecks diagnostic/human-repair signatures under the ledger lock. Changed evidence discards the stale answer. Storing an advisory audit does not invalidate Stage 3 or resolve a blocker.

The UI provides structural/table filters, evidence/source-page links, current human-route state, Colab page audits and the appropriate human review link (table repair, reading order or general structural review).

Validation: 14 structural backend regressions cover population without a ledger, all six known structural types, resolved human routes, batch/individual/HTTP queueing, dispatch/counts, advisory history/freshness, changed-evidence rejection, complete page coverage, diagnostics-only constraints, missing pages, truncated output, malformed matrices, table-specific evidence and multipage-table protection. Four frontend behavior tests cover workflow links, the structural action endpoint, table filtering and safe proposal rendering. The existing text/vision regressions remain in the full suite.

Deploy AH8 after merging the source change, enable Review workers, assign and resume a Colab Anomaly review worker, and use either the individual review action or the batch Yes action. Actual live Colab inference and deployment were not performed as part of this source change.
