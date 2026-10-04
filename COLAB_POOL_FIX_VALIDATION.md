# Colab normal Stage2B worker-pool fix

Version: `2026.10.04.40.11AH13`

## Problem

Normal Text/Vision Stage2B work had one scheduler coroutine per logical role. When a role selected Colab, that coroutine selected one physical Colab worker and awaited the entire job before allocating another. Multiple ready Colab workers therefore could not drain the same Text or Vision backlog concurrently.

## Fix

- Add one normal-work scheduler lane per configured physical Colab worker.
- Each ready Colab independently claims normal Text/Vision work.
- Existing `mark_processing(... WHERE status='pending')` compare-and-set remains the durable duplicate-work guard.
- Claim collisions retry immediately instead of waiting for the normal scheduler poll interval.
- Text and Vision are round-robin balanced when both roles use Colab.
- Artifact/review/RAG work still shares the existing physical-provider reservation and device lock, so one Colab never receives two simultaneous inference requests.
- Logical Pi5/OnePlus loops no longer serialize normal Colab work.
- Colab Text streaming/progress is recorded on the physical Colab worker state.

## Validation

Targeted scheduler and regression gate: **156 passed**.

Includes Stage2B, Stage2B circuit breaker, Colab runner, worker registry, artifact sweep, release-version sync, and new two-Colab concurrency tests.
