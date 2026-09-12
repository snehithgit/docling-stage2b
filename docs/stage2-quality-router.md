# Stage 2A — Quality profiler and router

Stage 2A begins after a Docling conversion ZIP is successfully written. It is intentionally **non-destructive**: the raw Docling ZIP remains the source of truth and is never rewritten.

## Automatic flow

```text
input PDF
  -> Docling conversion worker
  -> converted/<book>.zip
  -> Stage 2A post-process queue
      -> profile.json
      -> diagnostics.json
      -> routes.json
      -> correction_ledger.json
      -> source_manifest.json
      -> summary.json
```

The post-process queue uses the same SQLite database but a separate `postprocess_jobs` table. It processes one quality job at a time and can be retried independently from OCR/conversion.

## Generic document profiling

The profiler does not depend on a manufacturer or filename. It measures Docling structure and classifies pages into broad structural types such as text, table/list, mixed, and engineering visual. It also inventories picture classes, heading levels, technical values and likely procedure/troubleshooting/parts/specification structures.

A heading level greater than 5 is recorded as metadata, **not** treated as an error. Stage 2A now validates hierarchy consistency against the document's own numbered-outline pattern. For example, if `1.2.x` headings consistently map to one Docling level, a strong outlier is flagged; deep but internally consistent level-6 headings are accepted.

Reading order is validated with a conservative layout-aware check. It follows Docling body/group order, ignores page furniture, repeated title-block metadata, table/image OCR overlays, and schematic/grid labels, and accepts the better of row-major or column-major flow on two-column pages. Only material geometric inversions become `HUMAN_REVIEW` candidates.

## Diagnostics policy

Diagnostics produce one of these classes:

- `INFO` — useful evidence, not an error.
- `RULE_FIX` — safe, non-destructive retrieval/chunking operation such as excluding page furniture from semantic chunks or stitching a preceding heading onto a repeated troubleshooting table.
- `TEXT_REVIEW` — suspicious OCR that may later require evidence and Pi5 verification.
- `VISION_REVIEW` — uncertain technical visual that may later go to OnePlus.
- `HUMAN_REVIEW` — strong structural anomalies that should not be auto-rewritten, including heading-hierarchy inconsistencies and material reading-order anomalies; later unresolved verifier conflicts also use this class.

Non-ASCII engineering symbols are not automatically treated as OCR noise. Tables without a Docling `column_header` flag are INFO, not automatically repaired.

## Vision routing

High-confidence technical drawings are inventoried but are **not automatically sent to the OnePlus**. Only technical visuals with missing/low classification confidence are queued in Stage 2A. Later vision execution follows:

```text
full image -> quality gate -> overlapping crops only when unresolved
```

This avoids sending hundreds of drawings to a 2B phone VLM unnecessarily.

## Pi5 routing

Stage 2A only creates Pi5 routes for strong OCR-suspicion patterns. It does not create or apply engineering corrections. The validated Pi5 role remains source-vs-candidate verification (`SUPPORTED`, `CONTRADICTED`, `NOT_ENOUGH_EVIDENCE`) once trusted evidence and a candidate exist.

## Correction ledger

Every analysis package starts with an empty `correction_ledger.json`. Later stages must append explicit before/after/evidence/verifier/confidence entries. No silent mutation is permitted.

## Configuration

```yaml
postprocess_enabled: true
processed_dir: /data/processed
postprocess_poll_interval_seconds: 5
max_routes_per_document: 500
picture_review_confidence: 0.55
heading_consistency_min_group: 4
reading_order_inversion_threshold: 0.18
reading_order_min_items: 5
pi5_url: http://192.168.68.55:8080
oneplus_url: http://192.168.68.60:8080
external_verifiers_enabled: false
verifier_health_interval_seconds: 30
```

`external_verifiers_enabled` is deliberately false for Stage 2A. The UI still checks endpoint health/model information, but analysis does not yet spend model inference time or trust model-proposed corrections.

## Quality page

Open `/quality` to see Stage 2 queue state, Pi5/OnePlus endpoint health, profile kind, route counts, and download the generated JSON artifacts.

## Manual test of an existing ZIP

From the project root:

```bash
PYTHONPATH=. python scripts/analyze_docling_zip.py /path/to/converted_book.zip
```


## Structural verification added after Stage 2A validation

Two previously deferred checks are now implemented:

1. **Heading hierarchy consistency** — uses numbering depth and document-internal level patterns. `level > 5` alone is never an error.
2. **Layout-aware reading order** — compares Docling body order with plausible page geometry while filtering furniture, title blocks, visual overlays and schematic/grid pages. Two-column pages are allowed to be row-major or column-major.

Real-manual regression results with the default thresholds:

| Manual | Heading inconsistencies | Reading-order candidate pages |
|---|---:|---:|
| Engine Room electrical | 0 | 0 |
| Deck & Hull electrical | 0 | 4 |
| Hydraulics for Mariners | 0 | 2 |
| MacGregor Crane | 0 | 9 |

These reading-order pages are review candidates, not automatic corrections. The raw Docling document remains immutable.
