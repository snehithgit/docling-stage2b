# Stage 2B Verification Workspace

Stage 2B now has its own `/verification` page. The Quality page remains focused on Stage 2A document analysis and routing.

## Execution modes

### Manual by book

Each book with current Pi5/OnePlus routes appears in the Books table with a **Verify book** action. Pressing it authorizes only the pending routes for that book. Pi5 and OnePlus may run concurrently, but each device still processes one request at a time.

### Auto verify all

The **Auto verify all** switch enables Auto Run for both Pi5 and OnePlus. Device-specific Auto Run switches remain available if only one verifier should run automatically.

## Full queues

The page loads all remaining current routes (pending, processing, and failed) for each verifier. The frontend does not truncate the queue to 40 or 100 rows. Each row exposes its book, route, page, attempt count, retry count, next retry time, and last error state.

## Retry starvation fix

Retryable failures no longer sleep the whole device worker and immediately reclaim the same route. Instead, each failed attempt receives a `next_attempt_at` timestamp. The worker skips that route until its backoff expires and continues with later eligible routes.

Backoff is exponential from `stage2b_retry_delay_seconds` up to `stage2b_retry_max_delay_seconds`.

## Route-level timeout

`stage2b_request_timeout_seconds` still limits one HTTP model request. A OnePlus route can contain a full-image request plus up to four crop requests, so Stage 2B also enforces a route-level ceiling:

- `stage2b_pi5_job_timeout_seconds: 300`
- `stage2b_oneplus_job_timeout_seconds: 600`

If that ceiling is reached, the job is returned to pending with backoff and the worker proceeds to the next eligible route.

## OnePlus response hardening

- Invalid JSON receives one strict JSON-only retry.
- One malformed crop does not discard successful full-image or other crop results.
- Incomplete crop evidence prevents an overconfident `DECORATIVE_OR_LOW_VALUE` conclusion.
- Network/HTTP retryable failures remain retryable and are deferred instead of blocking later jobs.

## Failure audit

Every retryable or permanent Stage 2B failure writes an attempt-level JSON artifact under the document's `verification/` folder. It records the job, attempt, active stage, elapsed time, error type/message, retry delay, HTTP response details when available, and malformed model attempts when available.

Existing `jobs.db` files are migrated in place with `retry_count` and `next_attempt_at`; no database reset is required.


## Verifier pause and result-only view

Each verifier now has a persistent Stop verifier action. It disables Auto Run and pauses that device after any in-flight request is allowed to finish and save. The worker will not take another route until a manual Verify book action or Auto Run explicitly resumes it. The main Verification page intentionally shows completed and failed result rows rather than the internal pending queue. Pending/retry state remains in SQLite for orchestration and API diagnostics.
