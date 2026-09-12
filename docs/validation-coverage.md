# Stage 2A validation coverage and archive integrity

Stage 2A distinguishes **no anomaly detected** from **the checker could not evaluate enough of the document**. This matters for unknown manuals.

## Coverage artifact

Every completed quality job writes `coverage.json`. It reports:

- heading hierarchy: evaluable headings / total section headings;
- reading order: pages checked / total pages, eligible pages, and skip reasons;
- OCR-confusion scan: text blocks scanned and its Latin-oriented pattern scope;
- technical-value scan: text blocks scanned and its common SI/Latin-unit scope;
- vision inventory: pictures examined, technical visuals found, and visuals actively routed;
- archive integrity status.

Statuses include `consistent`, `anomaly`, `limited`, `not_evaluable`, and `not_applicable` where appropriate. A zero-anomaly result with low coverage must not be interpreted as a clean document.

Default minimum coverage thresholds are:

```yaml
heading_validation_min_coverage: 0.10
reading_order_min_coverage: 0.10
```

These are warning/status thresholds, not automatic source-correction thresholds.

## Heading schemes

The hierarchy validator recognizes conservative, independently evidenced numbering forms:

- decimal outlines such as `1.2` and `1.2.3`;
- alpha-decimal outlines such as `A.1` and `A.1.2`;
- uppercase Roman top-level headings;
- `Chapter`, `Section`, and `Appendix` labels.

Bare single-number headings are intentionally not used because numbered list items are frequently misclassified as section headers. Unnumbered headings remain visible in total-heading coverage and may cause the result to be `limited` or `not_evaluable`.

## Archive integrity

Before profiling, Stage 2A now:

1. validates the ZIP container;
2. runs CRC validation across ZIP members;
3. locates and decodes the primary Docling JSON;
4. checks relative `uri` references in the Docling document against archive members.

A CRC failure stops the post-process job. Missing referenced artifacts produce an `ARCHIVE_ARTIFACT_MISSING` human-review signal and are recorded in `integrity.json`. External/data URIs are reported but are not fetched.

## Real-manual validation

| Manual | Integrity | Heading coverage | Heading anomalies | Reading coverage | Reading anomalies |
|---|---|---:|---:|---:|---:|
| Engine Room electrical | OK | 57.7% | 0 | 64.6% | 0 |
| Deck & Hull electrical | OK | 66.4% | 0 | 73.6% | 4 |
| Hydraulics for Mariners | OK | 89.4% | 0 | 71.2% | 2 |
| MacGregor Crane | OK | 4.9% (LIMITED) | 0 | 39.0% | 9 |

All four tested ZIPs had zero missing referenced artifacts.

## Known scope limits

Structure keywords, technical-unit patterns, and OCR-confusion rules remain English/Latin-oriented. Coverage reports explicitly state this scope; 100% text-block scanning does not mean language-universal semantic coverage. Three-plus-column layouts, sidebars, rotated text, and other unusual page geometries may still require future layout models or human review.


## Human-readable labels

Internal status codes are intentionally stable for routing, tests, and API consumers.
Stage 2A adds `display_label` fields for dashboard/report presentation only.

| Internal status | Display label |
|---|---|
| `consistent` | Looks good |
| `anomaly` | Issue found |
| `limited` | Not enough checked to be sure |
| `not_evaluable` | Couldn't check this |
| `not_applicable` | Nothing to check |

Coverage summary labels are `Good`, `Needs more checking`, and `Needs attention`.
Archive integrity labels are `All files present` and `Some files missing`.

The display label never changes routing behavior. In particular, `limited` remains distinct from `consistent`; a low-coverage clean result must not be rendered as a green success state.
