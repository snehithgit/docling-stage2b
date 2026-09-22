## `.40.11E` Telegram follow-up — closed

- [x] Add native `/start`, `/help`, `/status`, `/books`, `/workers`, `/audit`, `/errors` Telegram command menu.
- [x] Show `/textaudit`, `/visionaudit`, `/artifactaudit`, `/stopaudit` in the native menu only when `telegram_controls` is enabled.
- [x] Restore filename/job/stage/error context in actionable push alerts without Telegram querying SQLite.
- [x] Add production startup contract probe for the real `telegramify-markdown` dependency.
- [x] Preserve reason-only event-sink compatibility.
- [x] Add regression tests for menu registration and contextual alert rendering.

## `.40.11D` Telegram/mobile UX — closed

- [x] Semantic status/action icons.
- [x] Safe Markdown-to-Telegram entity conversion and UTF-16-aware splitting.
- [x] Attention-first health commands and worst-first paginated lists.
- [x] Critical pipeline/verifier/quota/circuit push alerts.
- [x] Enforced `telegram_controls` human-audit gate.
- [x] OnePlus SSH/server status separated from verifier health.
- [x] Docling Auto-Convert live-surface branding cleanup.

## After `.40.9`

### September 21 audit list — closed

- [x] 1 Git secret/manual publication risk — `.40.8B`.
- [x] 2 Local verifier outage becoming fake completed evidence — `.40.8B`.
- [-] 3 SQLite/SMB concern — intentionally omitted; review-share artifact, not real deployment.
- [x] 4 Stale/duplicate correction generations — `.40.8B`.
- [x] 5 Duplicate document submission race — `.40.8B`.
- [x] 8 Unlocked manual cross-check ledger write — `.40.8B`.
- [x] 9 Route-priority truncation — `.40.8A.2`.
- [x] 10 Config list-type validation — `.40.9`.
- [x] 11 Groq in-flight reservation race — `.40.8B`.
- [x] 13 Missing table-cell correction provenance — `.40.9`.
- [x] 15 Equipment-scope anchor/neighbor allow-list guard — `.40.9`.
- [x] 7 Identified blocking async endpoints — `.40.9`.
- [x] 14 Conservative token-budget accounting — `.40.9`.
- [x] HTTP verifier lifetime — source-verified already context-managed; regression test added in `.40.9`.
- [x] Recursion protection — `.40.9`.
- [x] Verifier schema robustness — `.40.9`.
- [x] Telegram tests — `.40.9`.
- [x] 12 Stronger deterministic claim ↔ cited-evidence support checking — `.40.9`.

### Separately tracked correctness work before final production benchmark

- [ ] Atomic immutable machine embedding/index generations with one committed current-generation pointer.
- [ ] Explicit embedding-rule version/fingerprint so embedding input/format changes invalidate machine indexes correctly.
- [ ] Docling forgotten-task/404 recovery that clears the dead task ID and resubmits conversion safely.
- [ ] Enforce persisted table structural binding when a table-cell overlay is consumed; do not infer moves from repeated values.

### Acceptance after the separate correctness work

- [ ] Deploy without deleting `processed/`, `converted/`, `input/`, `data/`, equipment registry, audit decisions or existing raw Docling artifacts.
- [ ] Run **Revalidate all + rebuild**.
- [ ] Confirm Stage 3 rebuilds stale `.40.8B` canonical outputs under `stage3-canonical-integrity-v2`.
- [ ] Rebuild stale physical-machine embeddings.
- [ ] Run the fresh N150 machine-hybrid benchmark and electrical holdout.
