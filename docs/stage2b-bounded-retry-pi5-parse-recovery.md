# Stage 2B bounded retries and Pi5 parse recovery

This build closes two failure modes found in long Stage 2B verification runs.

## Bounded retryable failures

`stage2b_max_retries` defaults to `2`. Retryable HTTP/transport failures may therefore run at most three total attempts: the initial attempt plus two re-attempts. After the retry budget is exhausted the job is marked `failed` and the worker advances to the next route. Manual Retry/Rerun resets the retry counter and deliberately starts a new retry budget.

This applies to both Pi5 and OnePlus transport failures. OnePlus streaming remains unchanged: a healthy but slow phone is allowed to continue until protocol completion, subject to the first-output and stream-idle dead-stream detectors.

## Pi5 malformed JSON recovery

Pi5 OCR triage now mirrors the conservative OnePlus parse policy. A malformed or `finish_reason=length` response triggers exactly one compact JSON repair reprompt. If the repair response is still malformed, the route completes as `UNCERTAIN` with `parse_failed=true`, both raw attempts are preserved, and the job does not permanently fail merely because the model formatted JSON badly.

No source text is corrected or rewritten by this stage.
