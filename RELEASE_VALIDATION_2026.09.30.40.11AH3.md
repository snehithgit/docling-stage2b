# Marine Pipeline Studio 2026.09.30.40.11AH3 — Human Review AI-review filters

## Scope

This release connects persisted `ai_review_assistant` ledger results to first-class Human Review filters without changing the human-authority model.

## Implemented

1. Text Human Review filters:
   - AI reviewed / not AI reviewed
   - Review Assistant recommendation
   - Physical review worker
   - Needs my attention
   - AI says needs human
   - Verifier ↔ reviewer disagreement
2. Review-worker facets combine historical worker IDs stored in the ledger with workers currently assigned to Text review.
3. Filter state is preserved in Human Review URLs, so Previous/Next remains inside the selected subset.
4. Global AI-review counters report reviewed, unreviewed, NEEDS_HUMAN, disagreement, and unresolved attention counts.
5. Vision Audit/Human Review exposes equivalent AI-review/recommendation/worker/attention filters and includes currently assigned Vision review workers.
6. Search on Vision Audit now includes AI-review recommendation, reason, and worker metadata.

## Disagreement semantics

A disagreement is intentionally conservative.

- `LIKELY_CORRUPT → KEEP_ORIGINAL` is a verifier/reviewer disagreement.
- `LIKELY_OK → APPLY_PROPOSED` is a disagreement if such a route is ever exposed to this queue.
- `UNCERTAIN → KEEP_ORIGINAL/APPLY_PROPOSED` is a second-pass resolution, **not** a disagreement.
- `NEEDS_HUMAN` is tracked separately.
- Human decisions are never used to manufacture a verifier/reviewer disagreement.

For Vision:
- `TECHNICAL_USEFUL → DECORATIVE/NOT_USEFUL` is a disagreement.
- `DECORATIVE_OR_LOW_VALUE → TECHNICAL/USEFUL` is a disagreement.
- `UNCERTAIN → concrete reviewer recommendation` is a resolution, not a disagreement.

## Human-authority invariant

This release does not change decision authority:

- AI review remains stored as `ai_review_assistant`.
- Filters do not set `human_verified`.
- Filters do not set `human_visual_decision`.
- Review Assistant output remains advisory until a human explicitly saves a Text or Vision decision.

## Regression coverage

Tests were added/expanded for:

- AI-review facets and counts.
- Historical + currently assigned review-worker filter population.
- Recommendation and worker filtering.
- Needs-my-attention behavior.
- Conservative disagreement semantics.
- Text Human Review filter controls and URL persistence.
- Vision Human Review filter controls and disagreement helpers.

## Validation note

Source changes were read back from the GitHub branch after each write. The local execution sandbox in this chat was unavailable during this patch, so no claim is made here that the full pytest suite ran locally. The release branch is intended to be validated by repository/build execution before production deployment.
