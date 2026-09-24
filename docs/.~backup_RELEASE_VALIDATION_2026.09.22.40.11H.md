# Release validation — 2026.09.22.40.11H

## Scope

`.40.11H` fixes the case where an item already reviewed by a human could still appear in Web Human review because audit presentation was mixing historical Stage 2B rows with the current Stage 2C authority. It also adds direct Text audit decision controls outside the full review editor.

## Root cause verified from uploaded processed snapshot

The supplied processed snapshot contains multiple MacGregor reruns. Historical `MacGregor_Crane_Instruction_Manual__job5__run6` still contains 116 unresolved visual entries, while the current `run8` correction ledger contains 57 human-reviewed visual entries and 0 unresolved visual-review entries. Audit APIs previously walked historical verifier result rows and could resolve them against the row's historical result directory, allowing old work to appear as remaining.

## Implementation

- Text and Vision audit rows resolve against the current post-process result directory and current correction ledger.
- Historical row IDs whose target is not present in the current ledger do not become active review subjects.
- Duplicate visual entries for the same Docling source image are resolved as one physical-image authority; an existing human decision wins over pending duplicate routes/reruns.
- Text authority uses stable source identity and includes both `text_correction` and `table_cell_correction` entries.
- Human-decision timestamp ordering uses the actual `human_review.saved_at_epoch` field.
- Text audit cards expose **Accept correction**, **Keep original**, and **Edit / inspect context**. The two direct decisions POST to the existing `/api/postprocess/jobs/{job_id}/corrections/{entry_id}` endpoint and reload the one-item Human review queue.

## Safety properties

- Raw Docling remains immutable.
- No new approval database/state was introduced.
- Web and Telegram remain on the existing Stage 2C human-authority model.
- No bulk approval was added.
- Full editor remains available when the verifier text itself needs manual editing.

## Automated validation

- Full test suite: **542 passed, 1 skipped**.
- The skip remains the optional real `telegramify-markdown` integration test in this isolated environment; deployed startup probing remains unchanged.
- JavaScript syntax, Python byte-compilation, YAML parsing, shell syntax, ZIP integrity and clean-package checks are run again during packaging.
