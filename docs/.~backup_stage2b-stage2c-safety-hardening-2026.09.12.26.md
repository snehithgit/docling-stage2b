# Stage 2B / Stage 2C safety hardening — 2026.09.12.26

This release is a deterministic safety hardening pass. It does not modify raw Docling ZIP/JSON and does not add book/manufacturer dictionaries.

## Text source reconstruction

A verifier can accurately transcribe the pixels it receives while the target crop itself points at the wrong nearby label. `.26` therefore adds a final source-target alignment profile after target scoping.

Automatic application is rejected when:

- target token recall is zero and character sequence similarity is below 0.50 (`WRONG_REGION_LOW_OVERLAP`), or
- numeric/unit/identifier critical-token sequences change while target alignment is weak (`HIGH_RISK_TECHNICAL_TOKEN_CHANGE_LOW_ALIGNMENT`).

The existing truncation, partial-prefix/suffix and novel-adjacent-duplicate guards remain active.

## Stage 2C independent boundary

Stage 2C recomputes the deterministic alignment profile before accepting an automatic direct-transcription overlay. This protects saved/legacy Stage 2B results even if their historical status is `applied`. Human-verified corrections are never demoted.

The saved-result revalidation endpoint now handles direct target-crop reconstructions too, without rerunning Pi5/OnePlus/Groq.

## Vision exclusion conflict

A model result of `DECORATIVE_OR_LOW_VALUE` no longer causes automatic exclusion when N150 structural evidence reports `diagram_like=true`. Such conflicts become pending review unless the existing stronger technical-diagram category/confidence rule promotes them to `TECHNICAL_USEFUL`. Stage 2C independently repeats this policy for persisted results.

## Real-data replay: `_processed (8).zip`

Latest-run ledgers across seven books were replayed without model calls:

- 441 automatic direct text corrections examined.
- 35 would now be blocked/preserved as unsafe alignment.
- Hydraulics: 0 / 58 blocked.
- AD136TI: 0 / 16 blocked.
- Anemometer: 1 / 3 blocked.
- Engine Room: 4 / 87 blocked.
- Deck & Hull: 1 / 68 blocked.
- MacGregor: 24 / 123 blocked.
- Instruction Manual: 5 / 86 blocked.
- 25 Vision exclusions examined; 17 diagram-like exclusions now become review/pending.

Representative prevented replacements include `Transmilter Terminal -> +1 SW RX NIX TX`, `No.6 Operator Workstatlon -> C 220V`, and `witiout -> cannot be copied without permission...`.

These are conservative demotions: `.26` preserves Docling rather than guessing a correction.
