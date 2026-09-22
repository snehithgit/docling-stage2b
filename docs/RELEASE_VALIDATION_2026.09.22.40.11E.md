# Release validation — 2026.09.22.40.11E

## Scope

Follow-up to `.40.11D`: restore actionable filename/context in Telegram push alerts, validate the real formatter contract at runtime, and add Telegram's native slash-command menu. Pipeline evidence semantics, Stage 3 chunking, retrieval ranking, and human-decision authority are unchanged.

## Implemented

- `EventBroker.notify(reason, **context)` now accepts small scalar context while keeping SQLite/files as the source of truth.
- Conversion, Stage 2A and Stage 3 producers attach filenames/errors where available; Stage 2B adds source filename, route, stage and error for permanent verifier failures, cloud-quota pauses, Stage 2C ledger errors and job-triggered endpoint circuit opens.
- Telegram parses context from the existing SSE event frame and renders only what the producer supplied. It does not read SQLite or correction ledgers.
- Legacy injected event sinks that only support `notify(reason)` remain compatible.
- Bot startup registers native commands with `setMyCommands` and sets the default chat menu button to `commands`.
- `/start`, `/status`, `/books`, `/workers`, `/audit`, `/errors`, `/help` are always in the menu. Human-audit commands are registered only when `telegram_controls` is true.
- `telegramify-markdown` is exercised by `_telegramify_contract_probe()` at startup using bold, code, fenced-preformatted text and UTF-16-aware splitting. Failure degrades safely to plain text and emits a visible warning.
- Added a dependency integration test which executes the real package whenever it is installed in the test environment.

## External API verification

- Official Telegram Bot API documents `setMyCommands` for publishing up to 100 commands and `setChatMenuButton` with a `commands` button for the private-chat menu.
- `telegramify-markdown` 1.2.0 documentation confirms synchronous `convert(markdown, *, latex_escape=True)` and `split_entities(text, entities, max_utf16_len)` returning Telegram MessageEntity objects with UTF-16 offsets.

## Verification

- `PYTHONPATH=. pytest -q` → **538 passed, 1 skipped**.
- The skipped test is only the real-package integration test because the validation sandbox cannot install packages from PyPI. The production image installs `requirements.txt`, and startup now executes the same contract against the installed package before normal bot operation.
- Python byte-compilation passes for modified modules.
- Static JavaScript syntax, YAML parsing, shell syntax, ZIP integrity, and extracted-package regression are checked before delivery.

## Deployment

Rebuild/recreate the app container so the `.40.11E` code and current `config.yaml` are loaded. Telegram bot command menus are registered automatically during bot startup. No persistent pipeline data needs to be deleted or rebuilt.
