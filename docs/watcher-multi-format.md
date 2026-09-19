# Watcher multi-format output

The automatic input-folder watcher now exposes `to_formats` as a true multi-select in Settings.

Supported watcher formats:

- `json` — Docling JSON
- `md` — Markdown
- `html` — HTML
- `text` — Plain text
- `doctags` — Doc Tags

Any non-empty combination may be selected when `target_type: zip`. Examples:

```yaml
to_formats: [json, md]
```

```yaml
to_formats: [json, html, text]
```

Each file is assigned the exact selected format set when the watcher discovers it. If settings change while a document is still pending, that pending job keeps its original format selection. Newly discovered files use the new selection.

The jobs database retains the legacy `output_format` column for compatibility and adds `output_formats`, which stores the complete format list. Existing databases are migrated automatically; historical single-format rows become one-item lists.

The Live Queue displays every format attached to each job as separate pills. The dashboard header displays the current watcher selection.

For `target_type: inbody`, exactly one watcher format is required because direct-file output has one output extension. Use `target_type: zip` for two or more formats.
