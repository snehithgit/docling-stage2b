# V5.0.2 — Phase 3: detection and source-local context

Technical detection now prioritizes literal body content and specific source headings. It strips duplicated heading prefixes for classification while preserving every original text, heading, page and Docling reference. Inherited chapter labels such as Spare parts no longer classify an unrelated operating note as a parts record. N.B./Note and warning/caution callouts have explicit roles; a new section below an inherited N.B. heading does not become a note automatically. Derived search headings and aliases describe the literal body without replacing source headings.

## Context candidates

Notes, warnings and figure captions can link to a nearby diagram in the same manual and on the same first source page. A link requires either the exact figure identifier or a shared topic with the nearest preceding diagram, within three source chunks. Equally plausible diagrams remain unlinked. First-page boundaries prevent a note spanning two pages from attaching to the next page's unrelated diagram.

Each bidirectional link records target chunk identity, source-text hash, source pages/items, the rule/basis and candidate status. It is not a verified branch, cause/remedy mapping or component applicability statement. No cross-manual, cross-page or arbitrary heading-only proximity is used.

Generation and exported prompts can retrieve linked literal notes from text or visual diagram hits, even when the diagram itself is withheld. Target text, hash and references must match the current corpus. Stale or malformed links are not expanded. Book identity is normalized to the selected current book even when an old index embeds a historic job ID. Prompts and UI identify these as candidate context, with diagram applicability unverified.

## Text and image boundaries

Mixed chunks retain meaningful literal paragraph/table text with text/table references, including short safety instructions. Embedded pictures remain pending visual interpretation; neither a caption nor nearby prose certifies diagram arrows or exact branch relationships. Obvious standalone decorative icon/logo placeholders do not become technical facts. Unclassified source pictures can remain extraction candidates.

## Safe detector refresh

Detection uses technical-evidence-v2 identities. On upgrading an existing derived ledger, it first saves an exact technical_evidence_ledger.pre-v5.0.2.<hash>.json backup. Prior entries remain in superseded history. Same-source human flags, decisions, comments and extension metadata carry forward; prior validation proof is retained as previous_validation rather than certifying a new detector interpretation automatically. Rejected sources remain rejected, even when a classification disappears. Authoritative correction ledgers and original manuals are untouched.

Repeated scans preserve current review fields and refresh candidate links. They update only the technical ledger. Query-time lexical annotations refresh in memory; existing text indexes and embedding vectors are not rewritten. No Docling conversion, embedding rebuild, model call or Colab restart is needed.

Book/machine readiness additionally reports technical-note and candidate-link counts. They describe detected candidates, not complete manual extraction coverage or semantic correctness.

## Validation and remaining work

Tests cover N.B. classification, inherited-heading isolation, figure identifiers, captions before drawings, ambiguity, book/page/distance boundaries, exact target provenance, rejected/human history, detector backups, stale/malformed links, mixed text/images, visual-only note recovery and current book identity. Source-based retrieval/packet replays validate expected source retention without generating answers.

Phase 4 adds deeper structured text/table parsing. Phase 5 parses and verifies diagrams/images. Linking a note to a chart does not perform either task.

Rollback restores the previous application source/image and each pre-v5.0.2 derived-ledger backup. Originals, corrections and embeddings require no restoration because refresh leaves them unchanged.
