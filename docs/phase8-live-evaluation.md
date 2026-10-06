# Phase 8 live answer evaluation — 2026-10-06

Evaluation completed with the configured Colab worker pool on the live webapp. The model used retrieved manual excerpts, not outside reference material. Results are a bounded reference comparison, not engineering sign-off or universal semantic certification.

## Coverage and results

- 36 reference questions spanning safety, procedures, troubleshooting, maintenance, specifications, part numbers and equipment knowledge.
- All 36 generated successfully; colab-3 served 17 and colab-4 served 19. No answers were truncated and no unknown citation labels were emitted.
- All 36 expected source chunks were included in the generated-answer packets.
- Two additional crane controls generated: the cooling question returned the N.B. switch/Start instruction, while the cause question abstained because the available evidence did not establish an overheating cause. The cooling note was not promoted into a cause.
- Two unknown-model controls correctly refused before inference (zero model calls).
- Initial deterministic acceptance: 24/36. Replaying the identical saved answers after the evaluation fixes: 22/36 accepted, 14/36 require review. The lower acceptance reflects new safety and scope checks, not poorer retrieval.

## Findings

Five reference answers contained additions or ambiguities that prevent operational acceptance:

| Case | Finding |
|---|---|
| R007 | Nearby diagram/fuse current was promoted into a relay-output maximum; existing checks blocked it. |
| R012 | Coolant replacement intervals from prose and a maintenance table were presented without reconciliation. |
| R018 | A sensor range was additionally described as pump delivery pressure; existing checks blocked the extrapolation. |
| R025 | An instruction from oil-level checking was added to an oil-change answer. |
| R036 | The power-isolation prerequisite appeared after the cleaning instruction in the action list. |

The other nine review flags concern faithful target answers that the checker cannot fully validate: R003 citation placement; R005, R015 and R016 model/heading associations; R023 sentence coreference; R026 decimal comma and table-header units; R029 broken OCR in a spelled number; R033 an implicit condition in the question; R034 vertically split specification fields. These remain review-required. Improving the score by accepting uncertain relationships would weaken the evidence contract.

## Changes in V5.0.7.2

Literal value/unit pairs separated by table cell boundaries now compare correctly. Wrong magnitudes remain rejected. Explicit cooling-tube power-isolation prerequisites must precede cleaning. Multiple differing coolant-change intervals require reconciliation. Oil-level-check instructions cannot be promoted into oil-change prerequisites without the appropriate operation context. No correction ledgers are rewritten and no source facts or citations are invented.

Validation includes targeted regression tests and replay of all 36 saved model outputs, without regeneration. The per-claim API still reports `semantic_entailment_verified: false`: these checks do not constitute a semantic model or universal reasoning proof.

## Remaining limits

Historical unparsed diagrams, unverified table associations, OCR repair and manual inconsistencies still need source validation. This evaluation used explicit single-book lexical retrieval. It does not certify every manual, global/equipment hybrid search, every possible engineer question, or the earlier 30,000-question dataset. Raw private manual excerpts and generated responses remain in local evaluation outputs rather than the repository.
