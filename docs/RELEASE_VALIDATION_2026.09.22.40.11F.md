# Release validation — 2026.09.22.40.11F

## Scope

This release combines three operator-facing fixes on top of `.40.11E`:

1. Human-review-only Decision filters for Text, Vision and Artifact audit.
2. Mobile-first Telegram message/caption cleanup.
3. Safe Queue deletion for terminal conversion rows, including stale `FileMissing` rows caused by a manual rename.

## Human review behavior

- Text `Decision = Human review` is backed by the Stage 2C ledger's unresolved human-verification state and links into the existing one-by-one Apply / Keep Original review surface.
- Vision `Decision = Human review` requires no existing human visual decision and a current unresolved/pending/UNCERTAIN state.
- Artifact `Decision = Human review` uses the same Stage 2C visual-decision authority and the same unresolved semantics.
- While the Human review filter is active, saving a Vision/Artifact decision removes that item from the filtered collection and intentionally does not increment the page cursor, so the next unresolved item occupies the current slot.

## Queue deletion behavior

- Failed/completed conversion rows show a delete action; pending/processing rows do not.
- If a conversion already has a managed Stage 2A book, Queue deletion delegates to the existing book lifecycle endpoint.
- If a terminal conversion never became a managed book, the new queue-only endpoint quarantines any still-existing input/output artifact, writes a deletion manifest, and removes the terminal database row.
- Missing files are not an error during cleanup. This explicitly covers `FileMissing` after an operator manually renames a source file.
- A database-level guard prevents queue-only deletion from bypassing the managed-book delete lifecycle.

## Telegram UX

- Command messages use shorter titles/date stamps and move the build version to a footer.
- `/status` is reduced to attention, at-a-glance counts, progress and worker state.
- `/books` and `/audit` show compact per-book lines rather than repeated monospace mini-tables.
- `/workers` shows normal state tersely and expands circuit/outage/error detail only when abnormal.
- `/errors` suppresses zero-value sections and focuses on current blockers.
- Text/Vision/Artifact review captions are shorter and decision buttons share one row; Stop review remains separate.

## Validation

- Python regression suite: **541 passed, 1 skipped**.
- The skipped test is the optional real installed `telegramify-markdown` integration test in this network-isolated environment; runtime startup still probes the installed dependency and degrades visibly to plain text if incompatible.
- JavaScript syntax, Python byte-compilation, YAML parsing, shell syntax and release ZIP integrity are validated during packaging.
