# Release validation — 2026.09.22.40.11G

## Scope

This release finishes the unresolved-human-review workflow requested for all three audit types and revises Telegram presentation from the supplied phone screenshots.

## Web human review

- Text, Vision and Artifact expose a Human review option in the Decision selector.
- Human review means unresolved authority state, not a cosmetic verdict label.
- A saved Vision/Artifact decision disappears immediately from the filtered queue and navigation stays on the next unresolved item.

## Telegram mobile UX

- Routine command output uses normal Telegram text rather than large fenced/preformatted status blocks, avoiding the gray `COPY CODE` cards visible in the supplied screenshots.
- `/status` puts action-required state first, then one compact headline and three progress lines.
- `/books` and `/audit` use compact two-line entries.
- `/workers` expands circuit/outage/error information only when relevant.
- `/start` is a short landing surface; `/help` is a compact command index; the native Menu remains the primary command selector.
- Telegram's own message timestamp is used instead of repeating wall-clock time in every command response. Build metadata is kept out of routine status messages.

## Validation

- Python regression suite: **541 passed, 1 skipped**.
- The skipped test is the optional real installed `telegramify-markdown` integration test in this network-isolated environment; runtime still probes the installed dependency at startup.
- Python byte-compilation passes.
- All JavaScript assets pass `node --check`.
- YAML and shell syntax validation pass.
- Final ZIP is re-extracted and regression-tested before distribution.
