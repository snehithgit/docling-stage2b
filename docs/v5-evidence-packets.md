# V5.0.1 — Phase 2: evidence eligibility and source selection

Answer generation and exported prompts now use the same question-evidence/v2 packet. They bind retrieval candidates and their context neighbors to the selected manuals' current technical ledgers. This requires no model call or embedding rebuild.

## Evidence policy

Literal source paragraphs and tables remain quotable; an unvalidated technical extraction does not authorize its derived fault/cause/remedy mappings. Only records with complete validation proof and a current source signature supply validated relationships. Rejected/error records, changed source records, missing indexed technical records, and incomplete diagram relationships are withheld. Visual interpretations referencing an incomplete diagram cannot bypass this policy through the visual evidence path.

Human correction decisions are preserved. This policy does not require all ordinary paragraphs to receive a new human review. It distinguishes literal manual content from derived interpretation. Whole-manual extraction coverage and semantic claim verification are still later phases.

Mixed chunks with substantial literal paragraph/table text can retain that text while a neighboring drawing awaits parsing. The visual interpretation remains withheld. A flowchart or sparse picture-only chunk cannot use this exception.

The API returns withheld candidate identities and reasons in evidence_scope. When all candidates are withheld, generation and prompt export return 409 with an explanation before any model call. With a mixed packet, eligible sources remain available and the UI reports withheld candidates.

## Source selection

All eligible text, visual and nearby-context candidates share one bounded budget. The old fixed reservation of two visual slots is removed. Ranking combines upstream retrieval order, literal question overlap, technical intent and relevant note/warning context. Neighbors from every retrieved result may compete, rather than only neighbors of the first result. Stable ties and existing machine/book boundaries are retained; labels are assigned after selection.

Prompts explicitly distinguish alarm thresholds and cooling instructions from evidence of an overheating cause. The prompt cannot recover unread chart branches. Diagram parsing and chart-to-note relationship detection remain phases 3–5.

## Validation

Regression tests cover a fourth direct text passage, later-result warning context, unvalidated relationship exclusion, incomplete diagrams through both modalities, rejected and changed records, current versus stale validation, historic job identity, malformed ledgers, and API refusal before a model call. The existing 36 source-based retrieval cases retain their expected chunk in the new five-source packets. This is source-retention validation, not a claim that all generated answers are semantically correct.

## Deployment and rollback

This release changes generation packets and additive UI messages. No ledger migration, Docling conversion, GPU inference or embedding rebuild is required. Restore the previous application image/source to roll back; book data is unchanged.
