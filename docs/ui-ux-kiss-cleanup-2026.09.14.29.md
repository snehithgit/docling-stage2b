# KISS UI/UX cleanup · 2026.09.14.29

This release is UI/UX-focused. It does **not** change OCR detection, Stage 2A routing, Stage 2B safety policy, Stage 2C overlay policy, Stage 3 chunk policy, provider selection semantics, or OnePlus charging behavior from `.28`. It adds two operational conveniences only: a read-only Text Verifier Audit API/page and a one-click retry for already-failed verifier routes.

## Design rules

- **KISS:** one primary purpose per page and one obvious next action.
- **YAGNI:** raw diagnostics and one-time setup stay hidden until requested.
- **SOLID UI structure:** Books owns book progression; Verification owns verifier operation; Verifier Audit owns read-only evidence; OnePlus owns phone/server/charging control.
- **Progressive disclosure:** raw responses, model prompts, Groq usage, controller installation, SSH setup, and pipeline explanations live behind details panels rather than filling the default screen.

## Navigation

Primary navigation is kept to six items:

1. Books
2. Convert
3. Verification
4. Verifier audit
5. OnePlus
6. Settings

`Verifier audit` contains two simple tabs: **Text** and **Vision**. Errors and diagnostics remain a secondary utility link.

The current application version is always shown in the sidebar as `Version <server version>`. It also reports whether the bundled UI version matches the server so a stale browser cache is obvious.

## Books

Books is the operational home. It shows compact totals, search/filter controls, one status per book, and one next-action button. Detailed stage explanations are optional instead of repeated on every screen.

## Verification

Text and Vision provider selectors, health, Start/Stop, and Auto remain visible because they are operational controls. Cloud usage and explanatory material use progressive disclosure.

If failed verifier routes exist, one compact **Retry all failed** action appears. It requeues only current `failed` Text/Vision routes, leaves successful and merely-pending routes unchanged, authorizes the retry batch, and resumes the affected verifier worker. Individual retry remains available in result rows.

The existing **Revalidate all + rebuild** action remains separate because it performs no model calls; it revalidates saved text results and rebuilds Stage 2C/Stage 3.

## Verifier audit

### Text

The new read-only Text audit shows, for each completed/failed text route:

- book/page/route and Stage 2A reason;
- selected provider/model and processing time;
- immutable raw Docling target;
- exact saved target crop sent to the verifier;
- verifier reconstruction;
- BEFORE/AFTER locator anchors;
- scope/alignment metrics and safety reasons;
- truncation/finish details;
- raw verifier response;
- final correction disposition;
- Stage 2C downstream status;
- link to manual override when relevant.

Saved crop coordinates are used to reconstruct audit evidence so later config changes do not silently change what the audit page displays. Opening the page performs no verifier calls and never changes overlays.

### Vision

The existing Vision audit remains evidence-first and read-only, with the same unified Verifier Audit navigation. Full source images, inspected crops, parsed classification, raw responses, and Stage 2C action remain available on demand.

## OnePlus

Charging remains first, llama.cpp server lifecycle second, with one-time SSH/script setup under advanced controls. `.28` fail-open charging semantics remain unchanged.
