# V5.0.0 — Phase 1 foundation and readiness

The V5 release line separates three axes: correction processing readiness, actual search availability, and technical-evidence candidate validation. Search availability is not a certificate that every manual page was extracted or every answer is correct. Testing bypass is explicitly not correction readiness, even when an index remains inspectable. Existing search admission and human correction policies are unchanged; Phase 2 connects the evidence contract to answer packets.

## Evidence contract

`technical-evidence-ledger/v2` defines detected, queued, parsing, extracted, needs_review, validated, rejected, superseded and error. The original source, headings, identifiers, page/item references and unknown extension fields survive migration. V1 `source_bound` maps to extracted, not validated. Human-reviewed correction decisions are a separate authority and are never touched. A legacy technical human flag is retained but cannot alone certify a new structured extraction.

Validated evidence needs an explicit human or independent source check, reviewer identity, timestamp, matching source-text hash and checked provenance. Nonempty relationships require a relationship check. Incomplete proof is represented as needs_review. Detection does not manufacture a validated state. Re-detection preserves every review field for an unchanged source/parser identity; source/parser changes create new entries and retain superseded history.

## Migration

`POST /api/postprocess/jobs/{id}/technical-evidence/migrate` runs under the book lifecycle lock. It validates the old ledger and checks each active record against its current source chunk, pages and item references before changing anything. It creates an exact content-addressed v1 backup, writes v2 atomically, and is idempotent. Unsupported, corrupt or source-mismatched ledgers return 409. Correction ledgers, PDFs, chunks, embedding vectors, worker secrets/accounts and settings are untouched.

Automatic Stage 3 detection now writes v2. Existing v1 history is backed up before automatic re-detection and preserved as source/parser records are superseded. There is no forced Docling rerun or GPU migration call.

## Readiness and coverage

`/api/documents` and `/api/retrieval/status` expose additive readiness objects. Correction readiness considers current Stage 2C, required verification, structural/verifier blocking reviews and testing bypass. Search readiness reports current lexical/hybrid indexes independently. Machine readiness retains missing assigned manuals rather than silently dropping them.

Evidence coverage reports detected candidates, validated/rejected/pending states, visual parsing requirements, migration state and source freshness. A source signature binds coverage to the current retrieval corpus. Query-time aliases/classifications do not change the signature. Missing ledgers are not_scanned with unknown counts; malformed ledgers are invalid; changed corpora are stale. An empty candidate set does not establish full extraction coverage. Counts describe detected candidates only; whole-manual page/image coverage remains unmeasured until later phases.

The Book and Ask views show these three axes separately. `/api/pipeline/roadmap` lists all ten release phases, with only Phase 1 implemented. No diagram extraction, sufficiency recovery or semantic answer validation is claimed by this release.

## Rollback

Restore the previous application source/image and each content-addressed v1 technical ledger backup if reverting the derived-ledger schema. Authoritative correction decisions and original manuals do not require restoration because migration leaves them unchanged. Keep v2 ledgers until rollback validation is complete.
