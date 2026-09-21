# Release validation — 2026.09.21.40.9.4

## Scope

Vision truncation/crop recovery and human-authority evidence handling. Raw Docling artifacts remain immutable; this release only changes derived Stage 2B/2C visual evidence and audit behavior.

## Confirmed failure reproduced

A real electrical drawing was correctly classified by the local vision model as `TECHNICAL_USEFUL` at 0.98 confidence, but the model kept enumerating labels until `finish_reason=length`. The prior parser discarded the partial JSON, returned empty `UNCERTAIN`, and `_should_crop_vision()` explicitly suppressed crops on `parse_failed`. A later human `Useful` decision resolved the audit gate but the visual-RAG eligibility layer still rejected the entry because the original verifier verdict remained `UNCERTAIN` and there was no structured evidence.

## Fix

- Recover only syntactically closed fields/items from a length-truncated JSON response; mark the result incomplete so crop recovery still runs.
- If partial recovery is impossible, use a compact classification-only repair prompt rather than repeating the verbose request.
- Allow crops for parse failures, uncertainty, omitted details, and technical classifications with empty evidence.
- Use compact region evidence prompts and merge unique full-image/crop evidence.
- Human `Useful`/`Technical` overrides machine classification for RAG eligibility, but never fabricates evidence.
- Accepted images with empty/incomplete evidence block Stage 3 and queue a background recovery pass.
- Human recovery inspects the full image plus all configured crops, writes a separate recovery audit JSON, and merges only evidence fields into the already-human-authoritative ledger entry.
- Existing pre-release human-accepted empty entries are detected dynamically and expose a Recover evidence action.
- OnePlus visual max-token default/historical 384 setting migrates to 512.

## Quality invariants

- Human decision is never overwritten by automatic recovery.
- Original machine verdict/raw response remains available for audit.
- Raw Docling ZIP/JSON is never modified.
- An accepted image is not RAG-eligible until at least one visible-text/object/summary evidence field exists.
- Testing bypass remains the only way to intentionally continue while recovery is outstanding.
- Recovery merges evidence from every configured crop; it does not early-stop after the first crop.

## Regression coverage

1. Parse-failed full images now request crop recovery.
2. Truncated JSON salvages closed labels and keeps the result unresolved.
3. A recoverable length-truncated vision result uses one full-image call rather than repeating it.
4. Parse-failed full + resolved technical crop merges to technical evidence.
5. Human Useful with empty evidence marks recovery required and blocks Stage 3.
6. Human evidence merge preserves human authority and unblocks the gate.
7. Old human-accepted empty entries without the new flag are still detected.
8. Human Useful overrides original UNCERTAIN for visual RAG only after evidence exists.
9. Human recovery inspects the full image plus all four configured crops and merges them.
10. Historical OnePlus token ceilings migrate to 512.

## Validation

- Working-tree pytest: **501/501 passed**.
- Python compileall: **PASS**.
- JavaScript syntax (`node --check`): **PASS**.
- Bash syntax: **PASS**.
- YAML parse (`config.yaml`, `config.example.yaml`, `docker-compose.yml`): **PASS**.
- Final ZIP integrity and extracted-package pytest are re-run before distribution.
