# Release Validation — 2026.09.30.40.11AH5

## Scope

AH5 completes the anomaly-review operator workflow on top of AH4 without weakening the existing human-authority contract.

### Dedicated Anomaly Review

- New `/anomaly-review` page for current Text and Vision ledger anomalies.
- Deterministic anomaly classes include verifier/reviewer disagreement, Review Worker `NEEDS_HUMAN`, low review confidence, source-transcription truncation, large text expansion/contraction, suggested reviewer edits, and missing technical visual evidence.
- Already human-reviewed entries can be included and sent through a one-click post-human Colab re-review.
- Anomaly rows are paginated so large manuals do not render hundreds of source-page images at once.

### Explicit human Yes / No

- `Re-verify with Colab` queues the existing third-pass anomaly worker.
- A stored Colab anomaly result never changes the authoritative ledger by itself.
- `Yes · accept Colab` applies only the action represented by the returned anomaly verdict.
- `No · keep current` preserves the existing state and records rejection of the anomaly recommendation.
- A Colab `NEEDS_HUMAN` result remains unresolved even if acknowledged.
- Existing human decisions are preserved in anomaly decision provenance before an explicitly accepted replacement.

### Text review filter parity

`/text-audit` now exposes the same second-pass review controls needed for practical triage:

- AI reviewed / not reviewed
- AI recommendation
- physical Review Worker
- Needs my attention
- AI says needs human
- verifier ↔ reviewer disagreement

Those filters are preserved when opening the full Human Review editor.

## Ledger invariants

- Raw Docling remains immutable.
- `ai_review_assistant` remains advisory.
- `anomaly_review` remains advisory until a human explicitly chooses Yes.
- `anomaly_review_history` remains bounded and preserved.
- Human Yes/No is stored separately as `anomaly_human_decision` with bounded decision history.
- Human decisions are never silently replaced by Colab.
- Re-review results are freshness-checked against the evidence signature before publication.

## Validation gate

The repository now includes `.github/workflows/validate.yml`. Every non-main branch push and pull request runs:

1. Python 3.11 dependency installation.
2. Python `compileall` over `app` and `tests`.
3. The full pytest suite with `PYTHONPATH=.` and container-style `/data` paths.
4. `node --check` over every frontend JavaScript file.
5. Packaging of the exact validated revision as `marine-pipeline-studio-v2026.09.30.40.11AH5.zip`.
6. Upload of that ZIP only after all validation steps succeed.

The distributed AH5 source artifact must come from a successful validation run; failed runs do not produce a release artifact.
