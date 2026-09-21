# Marine Pipeline Studio v2026.09.20.40.7.1

## Verifier Audit gate

- Uncertain/unresolved visual evidence is now a human gate before Stage 3.
- Artifact-sweep visual items can be marked **Technical** or **Decorative**.
- Normal visual-verifier items can be marked **Useful** or **Not useful**.
- Human visual decisions are persisted in `correction_ledger.json`, are authoritative, rebuild overlays, and therefore invalidate stale downstream Stage 3/retrieval/embedding signatures naturally.
- A per-book **Bypass audit for testing** state permits downstream testing without accepting unresolved evidence. The unresolved count remains visible and the bypass can be removed.
- Text audit wording no longer implies that every Stage 2C `pending` disposition is an unfinished Stage 2B job; the UI calls these **Original preserved / not applied**.

Pipeline order is now:

`Docling -> Stage 2A -> normal Text/Vision -> Artifact Sweep -> Stage 2C ledger -> Verifier Audit gate -> Stage 3 -> Retrieval -> Machine Embedding -> RAG`

Stage 2C remains the auditable ledger construction step; Stage 3 is the hard downstream gate. This avoids mutating immutable verifier results while ensuring unresolved human audit decisions cannot enter RAG unless the book is explicitly bypassed for testing.

## Telegram bot

Telegram is an optional thin control plane. It calls application service methods and never edits SQLite, ledgers, indexes, or source files directly.

Configuration:

```yaml
telegram_enabled: false
telegram_bot_token_env: TELEGRAM_BOT_TOKEN
telegram_allowed_chat_ids: []
telegram_notifications: true
telegram_controls: true
```

The bot token is read from the environment only. Unlisted chat IDs are ignored.

Read commands: `/status`, `/books`, `/workers`, `/audit`, `/errors`, `/help`.

Safe controls: `/startall`, `/retryfailed`, `/pause_pi5`, `/resume_pi5`, `/pause_oneplus`, `/resume_oneplus`.

Destructive actions (book/equipment deletion, stale-file clearing) remain web-only and retain their confirmation protections.

## Upgrade note

After upgrading from .40.6.2/.40.6.3, run **Revalidate all + rebuild** for books affected by the legacy sweep/normal overlap migration before treating machine RAG as production-current.
