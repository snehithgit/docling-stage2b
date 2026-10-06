# Colab answer generation

On Ask your books, select **Colab · worker pool** under Generator. The same configured URL, model and protected API key from Workers are used; no additional credential entry is needed. An idle eligible pool member is reserved atomically against Stage 2B, anomaly/text/vision reviews and other answer requests. Paused, draining, unavailable or busy workers are skipped. If none can be reserved, the request fails clearly instead of waiting behind a busy GPU or switching to another provider.

The selected worker ID/name accompany the answer. Evidence scope, eligibility, literal/relationship/citation checks and truncation warnings apply unchanged. Cancellation, failure and success release the physical worker reservation. This feature does not start a notebook, restart a runtime or edit correction ledgers.

Groq remains selectable. Live Groq evaluation also prompted atomic cited-bullet instructions, canonical spacing for explicit source labels, operating-hours comparison and checks for spelled measurements/explicit flash counts. Missing citations are never fabricated.
