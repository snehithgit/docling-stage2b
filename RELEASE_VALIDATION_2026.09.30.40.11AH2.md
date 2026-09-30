# Marine Pipeline Studio 2026.09.30.40.11AH2 — Colab verifier correctness + richer GPU prompts

## Scope

This release fixes false-success accounting observed in the fresh Colab-only Stage 2B run and gives Colab a richer prompt profile without increasing Pi5/OnePlus prompt cost.

## Production issue reproduced

The uploaded `_processed(6).zip` contains 1,137 Colab text result artifacts whose `source_reconstruction.error_type` is `HTTPStatusError` (Cloudflare HTTP 530). Older code caught `HTTPStatusError` in the generic source-transcription exception branch, converted it to `UNREADABLE`, and allowed the route to be stored as `UNCERTAIN / completed`.

## Fixes

1. `HTTPStatusError` and other HTTP/protocol/Unicode transport failures now propagate out of direct source transcription. They are owned by the Stage 2B scheduler and can never manufacture an `UNREADABLE` source verdict.
2. One-time startup migration `colab_false_completed_transport_v1` scans current completed Text rows. Colab rows containing legacy infrastructure errors are reclassified to `failed` while preserving their old audit JSON/artifact. The existing **Retry all failed** action can rerun them.
3. Pending Artifact-sweep rows for affected books are re-gated to `awaiting_normal`, so Artifact work cannot run ahead of the newly failed Text routes.
4. Retry-all clears stale physical-provider claim/provenance fields before the next attempt.
5. Normal Colab pool dispatch now preserves the concrete `colab:<worker-id>` through job claim/error accounting instead of collapsing back to the logical `colab` selector.
6. Colab Text uses a dedicated high-accuracy source-transcription prompt: exact technical symbols/identifiers, look-alike-glyph discipline, stronger boundary instructions, more anchor context, and a larger 768–2048 completion ceiling. It still never receives the old Docling target text.
7. Colab Vision uses a richer GPU prompt with explicit diagram-category cues, up to 12 exact visible strings, up to 8 visible structures, a 40-word visible-facts summary, and at least 768 output tokens. Pi5/OnePlus retain their compact profile.
8. Verification now exposes separate **Retry failed Text**, **Retry failed Vision**, and **Retry all failed** actions.
9. Verification health UI distinguishes a failing `/models` control-plane probe from inference that completed within the last 180 seconds: `Working · probe degraded` replaces the misleading `Offline` badge in that case.
10. Workers page uses `Configured` rather than `Ready` for a worker whose URL/key are merely configured.
11. Docker image now copies the full `tools/` directory, so reset/restore utilities are actually present under `/app/tools`.

## Safety invariants

- Raw Docling output remains immutable.
- Existing genuine successful Colab Text results are not reclassified.
- Vision results are untouched by the migration.
- `SourceTranscriptionTruncated`, crop-unavailable, and genuine source-unreadable outcomes are not treated as infrastructure failures.
- Historical false-success result artifacts remain available for audit after the DB row is reclassified.

## Validation

- Uploaded run scan: **1,137** Colab Text HTTP 530 false-success artifacts identified, matching the previous result comparison.
- Targeted verifier / circuit / Colab / UI regression suite: **196 passed**.
- Full suite: **657 passed, 1 skipped**.
- `python -m py_compile`: passed for modified Python modules.
- `node --check`: passed for modified Verification and Workers JavaScript.
