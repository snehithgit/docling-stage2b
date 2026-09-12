# OnePlus Stage 2B streaming verification

OnePlus vision requests use the OpenAI-compatible streaming API (`stream: true`).
The phone remains strictly serial: one vision request is awaited to protocol
completion before the next OnePlus route starts. Pi5 remains an independent
worker on its own device.

## Completion and liveness

A request is complete when llama.cpp emits either a non-null `finish_reason` or
the SSE `data: [DONE]` sentinel. `finish_reason: length` is treated as a
truncated response and receives the existing one-shot compact JSON repair call.

The old fixed 240-second OnePlus read timeout is not used for streamed image
requests. Instead:

- `stage2b_oneplus_first_token_timeout_seconds`: maximum time to wait for the
  first generated content (default 1200 seconds / 20 minutes).
- `stage2b_oneplus_stream_idle_timeout_seconds`: after output begins, maximum
  time with no meaningful model output (default 300 seconds / 5 minutes).
- `stage2b_oneplus_job_timeout_seconds`: `0` disables an arbitrary total route
  duration ceiling. Network/protocol and inactivity guards remain active.

SSE comments/keep-alives do not reset the post-output idle timer.

## Audit/progress metadata

Each reconstructed response keeps `_stream` metadata including chunk counts,
first-output time, total time, completion status, `finish_reason`, `[DONE]`
status, and token/timing data when llama.cpp supplies them. The Verification
page shows the active OnePlus region, elapsed time, output chunk count,
first-output latency, last stream activity, final token count when available,
and completion markers.

SSE chunks are not assumed to be one tokenizer token each. Exact token counts
are taken from server `usage`/timing fields when available.


## Vision output budget

The OnePlus vision completion budget defaults to 384 tokens. The earlier 240-token
default caused otherwise healthy streamed responses to terminate with
`finish_reason=length`, producing avoidable `UNCERTAIN` fallbacks. Existing
configuration files that still carry exactly the historical 240-token default are
migrated to 384 in memory; custom non-240 values are preserved.
