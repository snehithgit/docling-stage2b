# Stage 2A structural validation

This build adds the two previously deferred structural checks without restoring the old high-false-positive heuristics.

## Heading hierarchy

The validator infers semantic numbering depth from conservative schemes such as `1.4`, `1.4.2`, `A.1`, uppercase Roman top-level headings, and Chapter/Section/Appendix labels. Each numbering scheme is modeled separately, learns its own Docling-level mapping, and flags only strong outliers. A Docling heading level above 5 is recorded but is never considered an error by itself.

Default real-manual results:

| Manual | Headings > level 5 | Numbered headings checked | Hierarchy anomalies |
|---|---:|---:|---:|
| Engine Room electrical | 460 | 626 | 0 |
| Deck & Hull electrical | 510 | 732 | 0 |
| Hydraulics for Mariners | 79 | 489 | 0 |
| MacGregor Crane | 875 | 48 evaluable of 978 total (4.9%, LIMITED) | 0 |

Decorative headings such as `R Chapter 1 R` are not used to infer numbered hierarchy when a separate semantic chapter heading exists.

## Reading order

The validator follows Docling `body` / `groups` order and compares it with plausible page geometry. It deliberately filters:

- page headers and footers;
- repeated manufacturer/title-block strings;
- compact page/reference numbers;
- OCR glyphs embedded inside tables/pictures;
- floating pictures;
- dense schematic/grid pages where paragraph reading order is not meaningful;
- near-full-page table/title-block layouts.

For two-column pages, both row-major and column-major interpretations are considered and the better fit is accepted.

Default real-manual results:

| Manual | Reading-order candidate pages |
|---|---:|
| Engine Room electrical | 0 |
| Deck & Hull electrical | 4 |
| Hydraulics for Mariners | 2 |
| MacGregor Crane | 9 |

These are review candidates only. Stage 2A does not rewrite the source Docling JSON.

## Coverage semantics

A hierarchy result is now one of `consistent`, `anomaly`, `limited`, `not_evaluable`, or `not_applicable`. In particular, MacGregor has no hierarchy anomaly but only 4.9% evaluable heading coverage, so it is reported as `limited`, not clean. Reading-order output likewise reports pages checked, total pages, eligible pages, skipped-page reasons, and use of A4 fallback geometry.

## Tests

The project regression suite passes 39 tests, including dedicated tests for:

- legitimate level-6 numbered hierarchy;
- strong numbered-heading level inconsistency;
- material single-column order corruption;
- valid two-column column-major flow.

- unnumbered-heading coverage reporting;
- alpha-decimal and Roman numbering recognition without cross-scheme contamination;
- low reading-order coverage;
- missing referenced-artifact detection;
- emitted `integrity.json` and `coverage.json` artifacts.
