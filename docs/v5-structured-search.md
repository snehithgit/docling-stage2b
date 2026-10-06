# V5.0.5: Verified structured search and incremental embeddings

Validated tables and visual graphs now contribute typed literal fields to lexical scoring and embedding text. Nodes retain exact labels; connections retain reviewed endpoint/branch relationships. Values, signs, units and identifiers are preserved. Ordinary text/headings/pages/Docling references are not rewritten.

The overlay is rebound from the current technical ledger each load. It requires full source identity, source signature, current validation proof and, for diagrams, the actual current converted image hash. Invalid, stale, superseded, unreviewed and changed-image records contribute no structured search facts. Ledger cache keys include file modification time and size; the raw chunk cache is independent of validation. Withdrawing validation removes facts without rebuilding raw chunks.

Explicit specification rows labelled Model, Model number or Applicable models expose a source-chunk-only applicability value. This does not assign or guess a model for the whole book, transfer applicability to neighboring procedures, or change equipment/manual assignments. Queries remain limited to the equipment's selected manuals.

Embedding fingerprints include the effective verified overlay. Books/machines with no eligible overlay keep their existing source signature and embedding text, preserving compatibility. When a validated record changes, the machine index becomes stale and the existing incremental builder embeds only changed rows while reusing unchanged vectors. The old legacy per-book builder can rebuild a whole book; normal machine builds use incremental reuse.

Prompt selection scores the rebound verified fields, and exact-value grounding checks can use their validated literal values. Structured facts enter prompts through the existing eligibility gate, never merely because a cached search result contains derived metadata. Semantic relationship/claim verification remains a later phase.

GET /api/postprocess/jobs/{job_id}/technical-evidence/search-index reports eligible indexed chunks, evidence IDs, source pages/items and explicit chunk applicability. It makes no model calls. Deployment does not certify pending extractions or rebuild unchanged embeddings. Real coverage depends on completing extraction and validation.
