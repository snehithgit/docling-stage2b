# Text review source validation (AH9)

The live reported entry is book 20 (AIR COND.PLANT FINAL PLAN.pdf), page 122, Docling text index 7216, route R00047. Its immutable text is `te  o e   s t  e  e  tet ed t e`. The primary result remains pending with SOURCE_IMAGE_UNREADABLE_KEEP_ORIGINAL and no proposed text. An older normal review describes a coherent proposal that is not actually stored. The older anomaly review returns KEEP_ORIGINAL with confidence 0.999, describes readable PDF pixels, and supplies no literal source transcription.

The source PDF page is visibly scanned sideways. The stored model crop is 177 × 1,145 pixels with clip points [334.25, 384.31, 404.41, 841.92]. This is a confirmed input-orientation problem and an unsupported review decision. It does not prove what the exact target transcription should be. No production ledger, human decision or worker configuration was changed during diagnosis.

## Changes

- Very tall text crops retain their original view and gain 90°/270° views of the SAME cropped pixels, with explicit panel labels. Models choose the upright view and transcribe once. No surrounding source area is added. This common renderer serves primary text transcription, second-opinion review, anomaly review and crop previews.
- Normal and anomaly text prompts require a boolean source_readable and literal source_transcription. They define KEEP_ORIGINAL as a match with the immutable Docling string, not merely a readable PDF. Unsupported legacy review narratives are excluded from source evidence. Missing proposals are explicitly represented; neighboring Docling anchors are read-only boundary hints.
- Deterministic scope checks use existing source-transcription localization guards. Matching source text can recommend keeping the original; source text matching the proposal can recommend the proposal; different scoped text becomes an explicit replacement candidate. An unsupported or unreadable response becomes NEEDS_HUMAN, with no confidence or invented repair. Unscoped candidates remain available for visual comparison, not automatic acceptance.
- The original model decision/reason remains recorded for diagnosis. Published decision/reason is normalized from the scoped transcription. Changed evidence is still rejected before ledger publication. Truncated anomaly responses are rejected and text audits have a 4,096-token budget.
- Legacy reviews lacking source validation are flagged on the Anomalies page. Old anomaly audits no longer count as current, even when their older evidence signature matches. The text-review page replaces unsupported legacy high-confidence verdicts with SOURCE RE-REVIEW REQUIRED and disables their proposal action.
- Human decisions remain authoritative. Neither AI pass modifies immutable Docling text or automatically saves a human override. Use the replacement candidate and Save manual override only after comparing it with the source.

## Validation and deployment

Thirteen new Python cases cover contradictory KEEP_ORIGINAL decisions, missing/unreadable transcriptions, literal equality, matching primary proposals, neighbor-only contamination, the exact reported crop dimensions, unchanged horizontal crops and legacy re-review detection. An endpoint regression verifies legacy audits become stale. Four frontend behavior cases verify legacy confidence/proposal suppression, unscoped candidate display and explicit acceptance controls for scoped replacements. Existing review history and human-authority regressions now supply literal source transcription in their mocked model response.

Full local suite: 720 passed, 1 skipped, 2 Unix permission tests deselected on Windows. All Python compiles and JavaScript syntax/diff checks pass. Linux CI runs the unchanged Unix permission assertions and the full frontend suite.

After deploying AH9, run Re-review with Colab for this entry. This rerenders the source crop and replaces the unsupported historical audit with a new transcription-based result. If a readable scoped candidate is produced, compare it with the original PDF, copy it into the editor, and save the human override. If the target is still unreadable or cannot be localized, it stays unresolved instead of accepting a fabricated warning. Live Colab inference was not run as part of the source fix.
