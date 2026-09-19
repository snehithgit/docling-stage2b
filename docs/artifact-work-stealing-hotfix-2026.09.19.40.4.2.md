# Shared Artifact Work-Stealing Hotfix — 2026.09.19.40.4.2

## Goal

Use both local vision-capable devices for huge full technical-artifact sweeps without forcing an equal split and without changing normal Vision-provider semantics.

## Runtime behavior

- `FULL_TECHNICAL_VISUAL` rows form one authorized shared pending pool.
- Pi5 and OnePlus each have concurrency 1 and pull a new row only when their worker loop is idle.
- Claims are atomic in `Stage2BStore`; two workers cannot claim the same row.
- There is no fixed percentage. If OnePlus is slow/hot, it remains occupied and Pi5 naturally claims more rows. If Pi5 is slower, OnePlus naturally claims more.
- A manually paused worker takes no artifact jobs, and Start/Retry does not force it back on.
- Transport errors, timeouts, retryable HTTP server errors and overload responses put only the failing artifact worker into `stage2b_artifact_worker_cooldown_seconds` (default 60s). The other worker continues.
- Retryable rows return to the same shared pool, so the opposite device may complete them.
- An already-running request is never duplicated. If a device hangs after claiming one artifact, the other worker continues with other artifacts; that one row is requeued only after the existing first-output/idle/request timeout declares the attempt dead.

## Scope boundary

Normal Stage 2B Vision verification is unchanged: it uses exactly the explicitly selected Pi5, OnePlus or Groq provider and has no automatic fallback. Shared work stealing applies only to full technical-artifact sweep rows.

## Audit

Completed artifact request/result records include `artifact_worker` so the Artifact Audit can show which local device actually processed the image. Legacy `source.processor` hints remain ignored.
