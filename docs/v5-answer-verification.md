# V5.0.7: Answer verification and evaluation

Generated answers now receive additional deterministic relationship checks before `answer_usable` can pass. Technical identifiers and values must coexist in a local cited evidence unit; numbers scattered across different sentences or sources cannot be combined into a new component specification. Causal claims using “because”, “due to” or “caused by” require a locally stated causal relation in the same direction. Near-verbatim procedure rewrites cannot silently discard a source condition.

Visual summaries and object descriptions cannot supply these literal relations; visible source text is required. Existing citation, exact-token, scope, eligibility, prohibition and truncation checks remain active. Failed claims remain visible for audit with explicit reason codes, and are marked unusable. No extra inference calls, automatic regeneration, ledger edits or runtime restarts are introduced.

The API exposes `verification_method` and `semantic_entailment_verified: false`. Passing these checks is not semantic proof. Paraphrases, multi-line tables, graph paths and conditional logic may need human review; the checker deliberately does not invent relationships by combining distant passages. The UI now describes passing literal checks without implying answer correctness.

Regression cases cover component/value swaps, reversed causes, cooling instructions mistaken for causes, dropped conditions, visual-summary reliance and faithful cause/condition statements. Live model answer evaluation remains pending a ready generator. Colab workers currently lack routable endpoint credentials; this release does not consume GPU time or start them automatically.
