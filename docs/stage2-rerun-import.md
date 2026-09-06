# Stage 2 rerun and converted-folder discovery

## Goal

Allow quality/routing code to be changed and re-executed without deleting the database or reconverting the original PDF.

## Documents produced by the watcher

Normal flow remains:

`PDF -> Docling -> converted/book.zip -> Stage 2A`

When Stage 2 finishes, the document exposes:

- **Quality** -> current `summary.json`
- **Routing** -> current `routes.json`
- **Rerun** -> queue the existing converted ZIP for Stage 2 again

Rerun never invokes Docling.

## Documents manually placed in converted/

A valid Docling ZIP copied directly into `converted/` is discovered by the Stage 2 worker even when there is no corresponding conversion row in the existing database.

The discovery process:

1. Looks only at `.zip` files.
2. Skips any output filename already owned by a normal watcher conversion, including a conversion still in progress with a reserved output name.
3. Validates that the archive is a valid Docling export (CRC, Docling JSON shape, referenced artifacts).
4. Infers returned formats from archive members.
5. Registers an internal completed source record with `source_kind=converted_folder`.
6. Creates the normal Stage 2 job and processes it.

Imported source records are hidden from the conversion dashboard counters/history, but their Stage 2 jobs are visible on Quality & Routing with an **Imported ZIP** badge.

## Database behavior

This change uses additive SQLite migrations only. Existing databases are preserved.

New conversion-source metadata:

- `jobs.source_kind` (`watcher` by default, `converted_folder` for manual imports)

New Stage 2 rerun metadata:

- `postprocess_jobs.rerun_count`
- `postprocess_jobs.last_rerun_at`

A rerun resets only the Stage 2 queue state for that document. Existing conversion history and other documents are untouched.

## Safety

- Raw Docling ZIP remains immutable.
- Stage 2 output files are atomically rewritten in the same result directory.
- A missing converted ZIP blocks rerun with a clear error.
- Pending/processing Stage 2 work cannot be queued again.
- Watcher-produced ZIPs cannot be accidentally duplicated by folder discovery.

## Troubleshooting structure regression

Stage 2 profiling recognizes common troubleshooting heading variants such as `Troubleshooting`, `Troubleshooting Chart`, `Fault Finding`, `Possible/Probable Cause`, `Corrective Action`, `Symptom`, and `Remedy`. The detection remains based on Docling section headings rather than arbitrary body prose, so these terms do not by themselves cause broad body-text classification noise.
