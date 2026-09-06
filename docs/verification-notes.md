# Verification Notes

The portable app was checked with isolated temporary input, output, and SQLite paths. The existing dashboard, error log, settings flow, manual-convert endpoints, queue behavior, and direct-output path remain covered by the original tests.

The reliability update adds regression coverage for the automatic folder-watch pipeline:

- folder discovery continues while the single conversion worker is blocked on a long remote task;
- a changed document that reuses the same filename creates a second source-version job;
- the first same-name output is preserved and a later version receives a deterministic hash-suffixed filename;
- a corrupt/non-ZIP Docling result cannot replace an existing valid output and leaves no `.part` file behind;
- a saved Docling task ID is resumed after restart without submitting the source file again;
- automatic file submission does not use `Path.read_bytes()`;
- old processing rows without a remote task ID return to pending, while rows with a task ID remain resumable;
- transient poll failures still retry and permanent poll failures are still recorded.

Verification command:

```bash
python -m unittest discover -s tests -v
```

Current result: **27 tests passed**.
