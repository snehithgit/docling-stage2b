# `.40.11H` handoff

`2026.09.22.40.11H` is the current implementation baseline.

## `.40.11H` human-review authority behavior

- Human-review audit pages resolve against the current book result directory/current Stage 2C correction ledger; historical Stage 2B rows cannot reopen already-reviewed work.
- Visual human authority is physical-image scoped across duplicate normal-Vision/artifact-sweep/rerun entries. If any current duplicate has an authoritative human decision, that decision wins for the image.
- Text human authority follows stable Docling source identity across reruns, including table-cell corrections.
- Text audit exposes inline **Accept correction**, **Keep original**, and **Edit / inspect context** actions outside Technical details; these call the existing Stage 2C human-correction endpoint and advance the Human review queue.
- No second approval store or bulk approval path was added.

# `.40.11G` handoff

`2026.09.22.40.11G` is the current implementation baseline.

## `.40.11G` human-review + Telegram mobile behavior

- Text, Vision and Artifact audit pages expose `Decision = Human review`; that view contains only unresolved items that still need a human decision.
- Saving a human Vision/Artifact decision removes the item immediately from the Human review queue so the next unresolved item occupies the current slot.
- Telegram `/status`, `/books`, `/workers`, `/audit`, `/errors`, `/start` and `/help` are phone-first plain-message layouts: no large preformatted `COPY CODE` cards for routine status.
- Telegram removes redundant top-of-message timestamps/build strings; the Telegram message timestamp is the clock, and the build is shown only in start/help diagnostics.
- `/start` is a short landing message; the native Telegram Menu is the command discovery surface.

# `.40.11F` handoff

`2026.09.22.40.11F` is the current implementation baseline.

## `.40.11F` review / Telegram / queue behavior

- Text, Vision and Artifact audit pages expose a **Decision → Human review** filter that contains only unresolved entries still requiring a human decision.
- Under Human review, saving a decision removes the decided item and keeps the next unresolved item in the current slot; users no longer page through already-reviewed evidence.
- Text Human review opens the existing authoritative one-by-one correction queue; Vision/Artifact decisions still use the existing Stage 2C visual-decision endpoint.
- Telegram command output is condensed for phones: short headers, attention-first status, compact progress lines, abnormal worker diagnostics only, and side-by-side decision buttons.
- Queue rows for terminal failed/completed conversions expose a 🗑 Delete action. Managed books reuse the canonical book-delete lifecycle; queue-only stale failures (including FileMissing after a manual rename) can be cleared even when the old file no longer exists.
- Active pending/processing conversions remain undeletable to avoid racing the conversion worker.

# `.40.11E` handoff

`2026.09.22.40.11E` was the previous implementation baseline.

## `.40.11E` Telegram behavior

- Native Telegram slash-command menu is registered on bot startup; the Menu button opens it.
- Audit commands appear in that menu only when `telegram_controls` is enabled.
- Critical/routine event frames can carry filename/job/route/stage/error context; Telegram renders it but never queries pipeline databases itself.
- Conversion, Stage 2A, Stage 3 and now Stage 2B/Stage 2C job failures can name the affected manual when known at the source.
- `telegramify-markdown` is contract-probed at runtime. Formatter failure degrades to plain text with a visible warning instead of silently dropping alerts.
- Existing Stage 2C Web/Telegram human-authority paths are unchanged.

# `.40.11D` handoff

`2026.09.22.40.11D` was the previous implementation baseline.

## `.40.11D` Telegram behavior

- Telegram messages use semantic status/action icons and Markdown-derived Telegram entities instead of proportional-font pseudo-columns.
- Long command replies are safely split; photo captions keep their decision keyboard and spill excess detail into follow-up messages.
- `/status`, `/workers`, `/errors` show attention first; `/books` and `/audit` are worst-first with paging.
- Critical pipeline/verifier/quota/circuit failures are pushed as 🔴 alerts; routine/recovery events are 🟢.
- `telegram_controls: false` now blocks audit starts and decision callbacks.
- OnePlus SSH/server state is shown separately from verifier circuit/workload state.
- Web/Telegram human decisions still use the same Stage 2C authority model.

# Previous handoff — `.40.11C`

`2026.09.22.40.11C` was the previous implementation baseline.

## `.40.11C` human-review/UI behavior

- `/review` can operate as a global authoritative text-review queue across books, with book/type/reason/state filters.
- Text, Vision and Artifact audit pages present one item at a time with Previous/Next and keyboard navigation; Vision decisions auto-advance.
- Vision/Artifact decisions and summaries are shown before extracted/raw technical details, which remain collapsed by default.
- Web and Telegram still commit decisions through the same Stage 2C human-authority functions.
- Shared navigation can surface an aggregated **Needs attention** strip; dashboard queue-refresh failures are visible instead of silently ignored.
- Verifier/RAG terminology is provider-accurate and consistent.
- `.40.11A` safety behavior remains intact.

# Previous handoff — `.40.11A`

`2026.09.22.40.11A` was the previous implementation baseline.

## `.40.11A` operator/UI behavior

- Add books through `/add-book`; files/URLs are registered directly with the normal pipeline. `/convert` is one-off ZIP conversion only.
- Testing bypass is contextual inside Stage 2C, not a permanent top-of-page control.
- Web text review supports Previous/Next, progress, auto-advance, keyboard shortcuts and table-cell context; Telegram one-by-one audit remains available.
- OnePlus page separates SSH/server state from Stage 2B verifier circuit/outage health.
- RAG answer generation has elapsed progress and true backend cancellation.
- Errors & diagnostics aggregates failures and audit blockers across the pipeline.

# Previous handoff — `.40.10.1`

# `.40.10.1` handoff

`2026.09.22.40.10.1` was the implementation baseline for this previous handoff.

## `.40.10.1` OnePlus stability behavior

- Use accumulated active inference time, not image/request count, to schedule rest.
- 90 min active work -> 20 min cooldown; 20 min natural idle resets the budget.
- Two <7 tok/s results -> scheduled cooldown; one <2 tok/s, one >=10 min request, or a transport outage -> 30 min severe cooldown.
- After cooldown, restart the canonical OnePlus llama.cpp service when available and require >=8 tok/s on the next real request to clear recovery probation.
- All work stays pending/deferred; cooldown does not downgrade evidence or consume retry budget.
- State is persisted in `/data/db/oneplus_workload.json`.

## What is closed

The September 21 audit list is closed across `.40.8A.2`, `.40.8B`, and `.40.9`:

- safe Git publishing;
- local verifier outage/circuit-breaker correctness;
- correction-generation reconciliation;
- duplicate conversion submission race;
- shared correction-ledger locking;
- global Stage 2A candidate ordering with a loud 5000-route safety valve;
- config list-type validation;
- Groq in-flight quota reservation;
- Stage 3 table-cell correction provenance;
- equipment-scope hard boundary;
- identified blocking async endpoint work;
- conservative Stage 3 token accounting;
- verifier HTTP lifetime verified context-managed;
- recursion guard;
- explicit verifier schema modes;
- Telegram tests;
- stronger deterministic claim ↔ cited-source support audit.

SQLite/SMB was intentionally omitted because the inspected network share was only the review/export environment; the real application runs in its container deployment.

## `.40.9` important behavior

- `STAGE3_RULE_VERSION = stage3-canonical-integrity-v2`; older Stage 3 canonical output is stale and must rebuild.
- Table-cell corrections keep table/cell + row/column spans in final chunk provenance.
- Equipment-scoped answer generation filters text/visual results before choosing an anchor.
- Exact visual values/identifiers count as grounded only if they occur in `visible_text` or are corroborated by text evidence.
- Telegram remains monitoring-only.
- Canonical OnePlus script remains CPU `4,5,6,7`, nice `10`, `-t 4 -tb 4` with the user-specified Qwen model/mmproj.

## Still separate before final production benchmark

- immutable/transactional machine index generations;
- explicit embedding-rule fingerprint/version;
- Docling forgotten-task (404) resubmission;
- table structural-binding validation when an overlay is consumed.

After those are done: **Revalidate all + rebuild**, rebuild stale machine embeddings, then run the fresh N150 machine-hybrid benchmark and electrical holdout.
