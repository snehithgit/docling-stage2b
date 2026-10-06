# V5.0.3: Structured literal extraction

Phase 4 parses explicit troubleshooting, alarm, parts and specification table headers, plus contiguous paragraphs labeled Fault/Cause/Remedy. Values remain strings; identifiers, signs and units are preserved. Each table record includes its exact row and header quotes, source line numbers and field column positions. The parent ledger supplies the unchanged source hash, manual, page and Docling item references.

Duplicate role headers, incomplete merged rows and malformed widths produce extraction issues. Blank rows are never completed using a previous fault. Non-table text ends table-header scope. Escaped pipes are supported. Unlabelled prose, implicit procedure relationships, diagrams and arrow branches remain literal evidence or pending visual parsing; the parser does not infer their meaning.

Structured records are extracted candidates, not corrections. Answer eligibility requires explicit structured-field checking with a matching fingerprint, in addition to the existing source and relationship checks. Changing a structured value invalidates that proof. Only validated records enter the generated prompt as structured facts. Ordinary literal source quotes remain subject to the existing evidence policy.

The detector version is technical-evidence-v3. Refresh backs up prior ledgers as pre-v5.0.3 with a content-hash suffix, preserves human decisions and superseded history, and changes no original PDF, Docling JSON, correction ledger or embedding. Refresh does not call Colab.
