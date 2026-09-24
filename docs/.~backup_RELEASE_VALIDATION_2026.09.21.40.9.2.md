# Release validation — 2026.09.21.40.9.2

## Scope

Safe per-book deletion from the Book workflow before a library-wide Stage 2A revalidation/rebuild.

## Behavior

- Every Book workflow page exposes a visible `Delete book` action.
- The browser asks for two explicit Yes/No confirmations before deletion.
- Deletion is refused while the book has queued/running Stage 2A, active Stage 2B verification, Stage 2C build, or Stage 3 build work.
- The active pipeline records are deleted transactionally from `verification_jobs`, `postprocess_jobs`, and the owning conversion `jobs` row.
- Any equipment assignment for the book is removed. Affected machine embedding indexes are deleted so the remaining machine corpus must be rebuilt from current manuals.
- If the deleted manual was referenced by another manual's `supersedes_postprocess_job_id`, that reference is cleared rather than left dangling.
- If deleting the manual leaves an equipment scope empty, the empty equipment entry is removed.

## Source safety / quarantine

Deletion removes the book from the active application but does not destroy its source artifacts:

- `/data/input/<source>` -> `/data/input/_deleted_books/...`
- `/data/output/<converted.zip>` -> `/data/output/_deleted_books/...`
- `/data/processed/<result_dir>` -> `/data/processed/_deleted_books/...`

The watcher and converted-folder importer only scan root-level files, so quarantined files do not immediately reappear. A deletion manifest is written under `/data/processed/_deleted_books/` for recovery/audit purposes.

If the database transaction fails, moved files are restored on a best-effort rollback path.

## Regression coverage

- Source/output/processed artifacts leave watched roots and can be restored during rollback.
- Pipeline records are removed across all three SQLite queue tables.
- Deleting a manual removes its equipment assignment and invalidates the affected machine index.
- Empty equipment scopes are removed.
- Book workflow exposes the delete action and quarantine-aware confirmation text.

## Validation

- Working-tree full pytest suite: **488/488 passed**.
- Python compileall: PASS.
- JavaScript syntax: PASS.
- Bash publisher syntax: PASS.
- YAML/Compose parse: PASS.
- Final ZIP integrity: PASS.
- Extracted final-package full pytest suite: **488/488 passed**.
