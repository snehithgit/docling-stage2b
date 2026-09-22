# Release validation — 2026.09.22.40.11C

## Scope

`.40.11C` completes the remaining human-review UX and UI-consistency work on top of the already validated `.40.11A` daily-use/safety baseline. It deliberately does **not** redesign Stage 2A/2B/2C authority, canonical Stage 3 chunking, retrieval ranking, or the OnePlus workload governor.

## Implemented

### Global web text-review queue

- Added `GET /api/postprocess/human-review` as a read-only aggregation of the existing authoritative per-book correction ledgers.
- Queue entries carry book/postprocess identity and can be filtered by book, source type, reason and state.
- `/review` now works across books while retaining Previous/Next, `N of M`, keyboard navigation and auto-advance.
- Table-cell review continues to show raw row/header context; the raw reason code is available under Technical details.
- Filter clearing is explicit after initial URL restoration, so stale URL filters cannot silently reassert themselves.

### Text / Vision / Artifact audit hierarchy

- All three audit pages render one human-decision item at a time.
- Previous/Next and keyboard navigation use the same interaction pattern.
- Vision decisions advance to the next remaining item automatically.
- Vision/Artifact cards present verdict/summary and decision controls before evidence internals.
- Evidence counts stay visible; extracted evidence and raw technical detail stay collapsed by default.
- No unsafe blanket human approval path was added. Existing large Artifact actions retain confirmation.

### Authority and terminology

- Web and Telegram continue to call the same existing Stage 2C human-decision functions; this release adds no parallel authority store.
- Verifier labels reflect the configured provider (`Pi5`, `OnePlus`, or `Groq`) while keeping the role explicit (`Text verifier` / `Vision verifier`).
- User-facing retrieval terminology is standardized on **Retrieval-Augmented Generation (RAG)** while compact navigation remains **RAG**.

### Shared attention feedback

- Shared navigation can show a **Needs attention** strip from `/api/errors`, covering pipeline failures, human-audit blockers and open verifier circuits.
- The Dashboard no longer silently swallows queue-refresh errors; it reports the refresh failure through the existing feedback surface.

## Preserved `.40.11A` behavior

- Managed Add Book upload/URL flow into the normal pipeline.
- Contextual Stage 2C testing bypass and warning/confirmation behavior.
- Stage 2A rerun, destructive and expensive-action confirmations.
- Separate OnePlus SSH/server state and verifier circuit/outage health.
- Grounded RAG spinner, elapsed time and true backend cancellation.
- Aggregated Errors & diagnostics.
- Simplified Queue and Quality layouts.
- Raw Docling immutability and existing human-decision precedence.

## Validation

- Baseline `.40.11A` before implementation: **526/526 tests passed** using `PYTHONPATH=. pytest -q`.
- `.40.11C` source tree after implementation: **529/529 tests passed**.
- JavaScript syntax checks are run for all application `.js` files before packaging.
- Python byte-compilation, YAML parsing and archive integrity are run before final delivery.

## Remaining work

The deeper correctness/performance tasks already tracked in `docs/PROJECT_IMPLEMENTATION_TODO.md` remain separate. This release does not mark them complete merely because the operator UI is improved.
