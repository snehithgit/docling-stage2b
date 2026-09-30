# Release validation — 2026.09.30.40.11AG

## Scope

Frontend interaction and navigation hardening on top of 40.11AF.

## Fixes

- Normalizes the primary sidebar at runtime so every primary page has the same order, labels, and SVG icons, including Workers, Review workers, and Chunk Viewer.
- Makes the navigation list the scrollable sidebar region so Version and Errors & diagnostics remain visible at the bottom on desktop and mobile.
- Rebuilds the Review workers page into a responsive assignment workspace with a primary-work gate card, explicit phase enable control, worker assignment cards, unsaved-change indicator, queue summary, and responsive table.
- Prevents Workers polling from destroying unsaved Colab URL/model/API-key/checkbox edits. Worker cards are re-rendered only when server state changes and no Colab form is dirty.
- Prevents Review workers polling from destroying unsaved Text/Vision assignment changes. Live queue status refresh is separated from settings rendering.
- Adds a shared short interaction guard so periodic refreshes defer while the user is clicking or editing a control.
- Reduces Verification UI load: fast status remains at the normal cadence, while large Pi5/OnePlus result payloads and Groq audit rows refresh every 15 seconds or immediately after explicit actions. Unchanged tables are not replaced in the DOM.
- Reduces unnecessary DOM churn on Book, Quality, and My Books pages by avoiding redundant full rerenders and/or slowing passive polling.
- Debounces event-driven Queue refreshes, prevents overlapping Queue fetches, and avoids replacing unchanged Queue rows while the user is interacting.

## Validation

- JavaScript syntax checked with `node --check` for modified frontend files.
- Static UI regression suite: 71 passed.
- Focused static/version/review-worker suite before version stamp: 77 passed.
- Full project suite: **648 passed, 1 skipped**.
