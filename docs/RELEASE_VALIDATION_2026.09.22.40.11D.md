# Release validation — 2026.09.22.40.11D

## Scope

Telegram phone UX, safe formatted transport, status icons, attention-first monitoring, expanded alerting, real `telegram_controls` gating, and the four requested live-surface branding fixes. No Stage 2B/2C evidence semantics, Stage 3 chunk semantics, retrieval ranking, or machine-index semantics were changed.

## Implemented

- Added `telegramify-markdown==1.2.0` to `requirements.txt`.
- The bot keeps its raw `httpx` Bot API architecture. Markdown source is converted with `convert()` and safely split with `split_entities()` into Telegram `text + entities` payloads.
- Removed blind `send()` slicing at 3900 characters. Long replies are split at Telegram-safe UTF-16 boundaries.
- Photo captions use the same entity conversion; if a caption exceeds Telegram's hard caption size, the image retains the decision keyboard and remaining detail is sent as follow-up text.
- `/status`, `/workers`, and `/errors` show 🔴 critical / 🟡 attention / 🟢 all-clear immediately after the header.
- Aligned status data is inside real fenced/preformatted blocks rather than proportional-font pseudo-columns.
- `/books` and `/audit` are worst-first, paginated, and explicitly tell the operator when additional rows remain.
- `/workers` separates OnePlus SSH/llama.cpp reachability from verifier circuit, failure, outage, workload, cooldown, and speed state.
- Critical push notifications now include conversion, Stage 2A, Stage 2B jobs/workers, Stage 2C ledger, Stage 3, cloud-quota, pipeline-sequence, and local verifier circuit failures. Circuit recovery and existing routine events remain green.
- `telegram_controls: false` now blocks audit start commands and audit decision callbacks. Stop remains safe so an already-open UI session can always be dismissed.
- Audit card dynamic fields are rendered as protected code spans; filenames/reasons/original/proposed/summary/labels are not trusted as Markdown syntax.
- Live branding references identified by the review now use **Docling Auto-Convert**.

## Deliberate deviation from the supplied review spec

The supplied spec proposed calling async `telegramify()` and then sending the result as MarkdownV2 strings. Current `telegramify-markdown` 1.2.0 documents `telegramify()` as an async rich-content pipeline returning Text/File/Photo objects; it may extract fenced code blocks as files. For this bot's raw-HTTP transport and status tables, the documented `convert()` + `split_entities()` path is a better fit: it keeps fenced tables as Telegram preformatted text, uses UTF-16-correct entity offsets, and avoids `parse_mode`/escaping failures.

## Safety / architecture preserved

- Telegram still does not read or write SQLite or correction-ledger files directly.
- Human decisions still go through the existing application-owned Stage 2C audit handlers and shared ledger semantics.
- No bulk human approval path was added.
- No pipeline start/stop control was reintroduced to Telegram.
- The event broker remains reason-only; push alerts therefore identify the failure class and direct the operator to `/errors` instead of coupling the transport layer to pipeline databases just to fetch a filename.

## Verification

- `PYTHONPATH=. pytest -q` → **535 passed**.
- Python byte-compilation passes for the modified application/test modules.
- Telegram transport tests cover split formatted messages, caption entities, reply-keyboard placement, enforced control gating, and critical-vs-routine event styling.
- The current `telegramify-markdown` 1.2.0 API was checked against its published PyPI documentation before implementation.

## Deployment note

The Dockerfile already installs `requirements.txt`, so a normal image rebuild installs `telegramify-markdown==1.2.0`. Existing persistent `input/`, `converted/`, `processed/`, `data/`, machine registry, and audit decisions do not need to be deleted or rebuilt for this UI/transport-only release.
