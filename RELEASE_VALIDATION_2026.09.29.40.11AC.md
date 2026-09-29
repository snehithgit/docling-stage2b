# Marine Pipeline Studio — Release Validation — 2026.09.29.40.11AC

## Purpose

40.11AC fixes two operator-trust issues discovered while enabling the optional Colab KoboldCpp verifier:

1. Completed Stage 2B rows could be mistaken for fresh Colab completions after merely changing the selected provider. Provider selection only applies to new/pending/rerun work; completed rows are preserved.
2. A copied Colab API key containing smart quotes, Unicode dashes/spaces, or other non-ASCII characters could reach the HTTP Authorization header and fail with an opaque `'ascii' codec can't encode characters` exception.

## Changes

- Public verification rows now expose `execution_provider` derived from their persisted request/result metadata without exposing raw prompts/responses.
- Verification Text/Vision result tables include a Provider column showing the provider that actually executed each row.
- Current provider selection is explicitly labelled as applying to NEW/PENDING work; it no longer makes historical result headings look provider-specific.
- Missing/zero legacy `processing_seconds` is rendered as `Not recorded` / `Duration not recorded`, not as a meaningful zero-second inference.
- Colab bearer keys are validated as printable ASCII before storage/use.
- Invalid AB-era persisted Unicode keys are treated as unconfigured rather than crashing status/worker loops, and the UI surfaces the validation problem.
- No automatic provider fallback was added.
- No Stage 1/2A/2B/2C/3 rule version was changed.

## Behavioral conclusion for the reported screenshot

An Offline Colab card together with Completed Text/Vision/Artifact counters is valid when those rows were completed earlier by Pi5/OnePlus (or another provider). Selecting Colab does not rerun them. A real Colab completion must have a stored execution provider of `colab` and a positive measured processing duration from the Stage 2B worker. A request that fails while constructing/sending the Authorization header cannot legitimately reach `mark_completed`.

## Validation

- Full pytest: 628 passed, 1 skipped
- Python compileall: PASS
- All frontend JavaScript `node --check`: PASS
- New regressions cover Unicode API-key rejection, invalid legacy-key compatibility, execution-provider provenance, and truthful timing UI.

## Upgrade

Upgrade AB -> AC using the same persistent database and processed/result directories. No pipeline rerun is required solely for this release.

## Packaged archive verification

The final ZIP was extracted into a clean directory and revalidated:

- Notebook JSON parse: PASS
- Full pytest from packaged ZIP: 628 passed, 1 skipped
- Python compileall from packaged ZIP: PASS
- All packaged frontend JavaScript `node --check`: PASS
