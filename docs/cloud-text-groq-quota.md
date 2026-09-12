# Selectable Groq verification, usage audit, and free-tier guard

## Configure one API key

Put the key in the deployment `.env` beside `docker-compose.yml`:

```env
GROQ_API_KEY=gsk_your_key_here
```

Do not put the key in `config.yaml`, commit it to Git, or expose it in browser JavaScript. `docker-compose.yml` passes the environment variable into the app container.

## Explicit provider selection

The Verification page has two independent selectors and persists the choice in `config.yaml`:

```yaml
text_verifier_provider: groq      # pi5 | oneplus | groq
vision_verifier_provider: groq    # pi5 | oneplus | groq
```

Both roles expose all three processors. There is no automatic provider fallback. Internal database route names remain `pi5` (Text role) and `oneplus` (Image role) for backward compatibility only.

When Groq is selected for Text, the live 2026.09.11.21 path uses the configured Groq vision model because Text correction is now source-image target transcription rather than text-only semantic triage. The older GPT-OSS text client is retained for legacy compatibility/audit utilities.

Manual Text → Image re-read follows the selected Image processor and sends the isolated target crop, not the complete page. Vision → Text consistency uses whichever Text provider is selected.

## Automated text flow

1. Stage 2A creates bounded OCR candidates without changing Docling.
2. Stage 2B resolves the candidate's Docling bbox and renders only that target from the original PDF/raster source.
3. BEFORE/AFTER Docling blocks are supplied only as location context and crop boundaries.
4. The selected Pi5, OnePlus, or Groq processor transcribes the target exactly.
5. READABLE text is compared deterministically with Docling: unchanged keeps original; changed is applied directly to the overlay.
6. UNREADABLE/missing-crop keeps the original Docling text and records unresolved provenance.
7. No second semantic cloud vote is required. Human Review remains optional audit/manual override.

## Groq usage audit

Every Groq HTTP response made by this app is recorded as metadata in `/data/db/groq_quota.json`. The Verification page shows calls in the last 24 hours, success/failure, input/output/total tokens, paid-equivalent estimated cost, and recent calls with model, kind, book/route, HTTP code, latency, request ID and error code.

The ledger never stores prompt text, image bytes/base64, or API-key values. It begins with calls made by this app after this release is installed; it does not import historical Groq-console usage.

## Free-tier guard

Defaults warn at 80% and stop at 90% of the configured 1,000 requests/day or 200,000 tokens/day allowances. These values are configurable because provider/account limits can change.

Before every Groq request the guard estimates the maximum token use of the next call. If accepting it could cross the configured daily safety point or the server-reported TPM reserve, the request is not sent. Every response is then recorded with actual token usage. The state is persisted at `/data/db/groq_quota.json`.

Groq response headers provide organization-wide Requests Per Day information, so the app also stops when the server-reported request reserve is reached. Token headers describe Tokens Per Minute rather than Tokens Per Day; therefore daily token safety is based on this app's actual rolling-24-hour usage. Use a dedicated organization/key for this app if you want the local token guard to account for essentially all token consumption.

At pause, queued roles that currently select Groq remain pending and do not consume retry budget. Docling and roles assigned to Pi5/OnePlus can continue. The UI shows the reason and earliest calculated resume time. The worker resumes after the applicable local/server window expires.

An HTTP 429 is treated as a quota/rate-limit pause, using `retry-after` or the server reset header when present; it is not treated as an OCR verification failure.
