# Low-load verification

Pi5 inference from normal verification and Stage 2C backfills shares a single
request lock. Waiting for the device does not consume the Pi5 inference timeout.
The existing Pi5 job timeout now bounds each inference request, rather than the
combined triage/correction sequence. Token budgets and device launch flags are unchanged.

Successful request responses are checkpointed on the host under the book's
`verification/checkpoint_JOBID.json`. Keys include endpoint, generation, model,
prompt, image hash, and request settings. A retry or restart reuses identical
completed calls and continues unfinished work. Successful normal jobs remove
their checkpoint so an explicit subsequent rerun performs fresh inference.
Failed jobs and interrupted backfills retain checkpoints for recovery.

OnePlus stops reading at `finish_reason` or `[DONE]`; it does not wait on an idle
connection after completion. Usage events arriving after `finish_reason` may
therefore be unavailable, and `done_received` can be false for a completed response.

Vision prompts request compact summaries and label lists within the unchanged
384-token default. The model must flag omitted or unreadable important details.
Crops stop early only when the full image reports a classification uncertainty
and a resolved, high-confidence technical crop answers it. Unreadable-detail
requests continue through configured crops. Early termination is recorded as
partial coverage; summaries and uncertainty metadata flow into enrichment overlays.

Pi5 suspect text is divided into whitespace-boundary segments of up to 1,800
characters, with up to 1,000 characters of nearby context per request. Every
character remains represented in the segments. An indivisible oversized token
is flagged for review without inference. A multi-segment result is only marked
likely OK if every segment is likely OK; other outcomes remain uncertain with
per-segment evidence and coverage. Multi-segment text is not automatically rewritten.

For watcher PDFs, a Pi5 correction that passes the local gates receives a second
visual check only after every OnePlus image route for that book is completed.
The original PDF is rendered at the suspected page and sent as image evidence
with the original and proposed text. OnePlus must return `AGREES`; disagreement
or unreadable evidence changes the correction to `proposed` and keeps it out of
the overlay. Imported Docling ZIPs do not have a source PDF to render, so their
text corrections remain Pi5-only and are marked accordingly in the ledger.

Tests use simulated servers and documents. These changes reduce repeated work;
they do not establish measured accuracy, thermal behavior, or throughput on hardware.
