# Pre-Stage-2A Docling reliability

The automatic pipeline is remote-task driven:

1. submit one stable source to `/v1/convert/file/async`;
2. persist the returned `docling_task_id` immediately;
3. poll `/v1/status/poll/{task_id}` until Docling reports success/failure;
4. retrieve `/v1/result/{task_id}`;
5. atomically validate/write the converted output;
6. only then hand the ZIP to Stage 2A.

A long-running local timer is not a conversion failure. `document_timeout_minutes` is an informational threshold used to mark a processing job as long-running. The worker continues polling the same saved task id indefinitely while Docling has not reported a terminal failure.

Retry semantics are task-aware. A failed local/poll/result-transfer attempt with a saved remote task id resumes that task. A terminal failure returned by Docling clears the task id so Retry can submit a fresh conversion. This avoids duplicate multi-hour conversions.

`docling_poll_timeout_seconds` controls one status request. `poll_max_consecutive_errors` controls transient poll retry tolerance. `docling_result_timeout_seconds` separately controls the successful result download.
