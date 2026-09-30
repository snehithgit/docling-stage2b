# Release validation — 2026.09.30.40.11AH4

## Scope

AH4 adds an independent Colab **Anomaly Review** stage after the normal AI Review Assistant while preserving human authority.

## Anomaly classes

### Text
- `AI_REVIEW_NEEDS_HUMAN`
- `LOW_AI_REVIEW_CONFIDENCE` (< 0.75)
- `VERIFIER_REVIEWER_DISAGREEMENT`
- `REVIEW_EDIT_SUGGESTED`
- `VERIFIER_TRANSCRIPTION_TRUNCATED`
- `LARGE_TEXT_EXPANSION`
- `LARGE_TEXT_CONTRACTION`

### Vision
- `AI_REVIEW_NEEDS_HUMAN`
- `LOW_AI_REVIEW_CONFIDENCE`
- `VERIFIER_REVIEWER_DISAGREEMENT`
- `MISSING_TECHNICAL_EVIDENCE`

Manual re-review of a human-resolved item uses `POST_HUMAN_REVIEW_RECHECK` when no automatic anomaly class is currently present.

## Processing contract

1. Primary Text/Vision/Artifact verification finishes.
2. Normal AI Review Assistant Text/Vision queues finish.
3. Colab workers assigned to **Anomaly review** may consume `anomaly_text` and `anomaly_vision` jobs.
4. Automatic anomaly jobs are never created for already human-resolved entries.
5. A human-reviewed Text or Vision entry can still be explicitly queued with **Re-review with Colab**.
6. The original source crop/image is ground truth. Docling, primary verifier, Review Assistant and human state are supplied as context.
7. Anomaly output is persisted as `anomaly_review`; the prior result is retained in bounded `anomaly_review_history`.
8. `human_verified` and `human_visual_decision` are never set or replaced by the anomaly worker.
9. If evidence changes while Colab is running, the late result is discarded by an evidence-signature check.

## Correction behavior

- Text can return a `corrected_text` proposal. **Use anomaly correction** copies it into the existing Human Review editor; it becomes authoritative only after the human saves.
- Vision can return a corrected classification/summary/visible-text/object proposal. It is shown beside the current audit state and remains advisory.
- Existing human decisions remain authoritative during and after re-review.

## Review Worker UI

- Adds independent **Anomaly review** assignment for each configured Colab worker.
- Text/Vision headline values are now **remaining = pending + processing**, preventing normal claim/requeue transitions from appearing as backlog growth.
- Adds **Anomaly remaining**.
- Queue table exposes attempt count and last retry/error reason.

## Validation

GitHub Actions validation on commit `6859434b9026e699642fa15e31246d40c705589b`:

- Python `compileall`: **PASS**
- pytest: **667 passed**, 6 deprecation warnings
- frontend JavaScript `node --check`: **PASS** for every `app/static/*.js`

The warnings are existing dependency deprecations (PyMuPDF/SWIG and Starlette TestClient) and are unrelated to AH4.

## Compatibility

- Existing AH3 ledgers require no migration.
- Existing `ai_review_assistant`, human decisions, Stage 2A/2B/2C and Stage 3 data remain valid.
- Worker registry reads older files without `anomaly_worker_ids`; the new role defaults to an empty assignment.
- No automatic anomaly processing occurs until a Colab worker is explicitly assigned to the new Anomaly role.
