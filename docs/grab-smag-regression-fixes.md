# GRAB-SMAG Stage 2A regression fixes

This build adds three conservative fixes discovered while validating the real GRAB-SMAG Instruction Manual Docling export.

## 1. Generic OCR-garble detection

Stage 2A now detects fragmented OCR text such as broken single-letter/token runs and repeated short fragments. The detector is intentionally conservative:

- it does not treat accents, non-ASCII engineering symbols, or ordinary extra spaces as corruption;
- it uses Unicode-aware word matching so French/German text is not split at accented letters;
- standalone uppercase engineering identifiers such as `A`, `B`, `G`, `H`, `K` are ignored;
- normal short function words in several Latin-script languages are excluded;
- detected text is routed to `TEXT_REVIEW -> Pi5`; Stage 2A never edits it automatically.

On the GRAB-SMAG manual this finds 19 targeted suspicious text blocks instead of the previous 0, without flooding the queue with normal multilingual text.

## 2. Reading-order non-prose exclusions

The layout-aware reading-order checker now excludes additional pages that do not have a meaningful prose order:

- generic title/form pages with many short key/value fields;
- contents/index pages;
- repeated title-block metadata in the upper page region;
- large technical-visual label pages.

The synthetic true-scramble tests remain enabled, so real prose reordering is still detected.

On GRAB-SMAG the previous 7 candidate pages reduce to 0 after these non-prose exclusions.

## 3. Requested/returned format delivery tracking

`source_manifest.json` now records the exact watcher-format snapshot from the conversion job and compares it with the files actually returned in the Docling ZIP:

- `requested_formats`
- `returned_formats`
- `missing_requested_formats`
- `format_delivery_status`
- `format_delivery.display_label`

The same format-delivery summary is included in `summary.json`. This makes it possible to detect a Docling response that silently omits one of several requested output formats.
