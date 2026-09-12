# Vision Verifier Audit — 2026.09.11.25

This release adds a read-only Stage 2B audit surface for image classification/enrichment work.

## Goal

Make vision processing inspectable without changing its routing or correction policy. The page answers:

1. Why did Stage 2A send this picture?
2. What exact full image and crops did the selected Vision provider inspect?
3. What did the provider return?
4. What did Stage 2C do with that result?

## UI

Open `/vision-audit`, or use **Audit classification** beside a completed Vision result on `/verification`.

Each audit entry contains:

- book, page, route ID, reason code and priority;
- selected provider and model;
- exact full source image reconstructed from the immutable converted ZIP;
- any overlapping crops actually inspected;
- full-image prompt and, for new runs, exact per-crop prompts;
- raw model responses and parse attempts;
- final merged verdict, confidence, category and summary;
- visible text and model-described visible objects kept as separate fields;
- unresolved state/reason and deterministic structural-image override;
- Stage 2C status (`applied`, `excluded`, or `pending`) and status reason.

## Safety

The audit endpoint and page are read-only. They do not authorize jobs, rerun Stage 2A/2B, edit the correction ledger, or modify the converted Docling ZIP. Crop images are regenerated deterministically from the original picture bytes using crop settings saved in the verification request.
