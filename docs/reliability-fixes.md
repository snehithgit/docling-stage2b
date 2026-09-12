# Reliability fixes

This build hardens the automatic folder-watch pipeline without changing its core deployment model: one local UI/queue container talks to the configured Docling Serve instance and runs only one conversion at a time.

## Fixed behavior

1. **Continuous discovery during long conversions**
   - Folder discovery and conversion are separate asyncio tasks.
   - New stable files become `pending` while the single conversion worker remains busy.
   - Only one conversion is submitted/polled by the worker at a time.

2. **Large automatic uploads are streamed**
   - The automatic pipeline passes an open file handle to `httpx` multipart upload.
   - It no longer uses `Path.read_bytes()` for watched files.

3. **Changed files can reuse a filename**
   - New jobs store source size, nanosecond mtime and SHA-256.
   - Unchanged files use size/mtime as a cheap discovery fast path.
   - If `manual.pdf` is later replaced with different content, it becomes a new job.
   - The first result remains `manual.zip`; later versions use `manual__<sha8>.zip` so history is not silently overwritten.
   - Existing filename-only jobs are migrated/backfilled rather than duplicated on upgrade.

4. **Atomic, validated output writes**
   - Results are written to a hidden `.part` file first.
   - ZIP output must be readable, non-empty and pass `ZipFile.testzip()`.
   - The temporary file is flushed/fsynced before an atomic `os.replace()`.
   - Invalid ZIP bytes never replace a previously valid completed result.

5. **Restart recovery resumes Docling tasks**
   - `processing` + saved `docling_task_id`: keep processing and resume polling after restart.
   - `processing` without a task ID: safely return the job to `pending`.
   - The source is not re-uploaded when an existing remote task can be resumed.

6. **SQLite connections are closed cleanly**
   - Database operations now use a transaction-aware context manager that commits/rolls back and closes each connection.

## Verification

Run:

```bash
python -m unittest discover -s tests -v
```

The suite includes regression coverage for versioned same-name inputs, output preservation, corrupt ZIP rejection, remote-task resume, continuous discovery while conversion is blocked, and streaming automatic upload behavior.
