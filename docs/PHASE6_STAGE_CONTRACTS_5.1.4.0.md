# Phase 6 strict stage-result contracts — 5.1.4.0

This phase completes the remaining Priority-1 architecture item from the
post-Phase-4 audit: one strict, normalized stage-result contract.

## Design rule

The contract is a read-only projection of existing authoritative database rows
and stage artifacts. It does not create a second state store or a second state
machine.

Schema: `pipeline-stage/v1`.

Each contract always contains:

- `stage_id`
- `generation_id`
- `created_at`
- `source_signature`
- `upstream_generation`
- `status`
- `current`
- `ready_to_advance`
- `blockers`
- `data_hash`
- `data_hash_status`
- `human_review_required`
- `rule_version`
- `recorded_rule_version`
- `reason`
- `details`

Legacy artifacts that never persisted a trustworthy generation or output hash
use `null` plus an explicit diagnostic status. Timestamps, mtimes and filenames
are not promoted into fake hashes.

## Stages projected

### Stage 2A

Uses the postprocess row and immutable converted-source SHA. The result
directory is the current Stage-2A run identity. Existing Stage-2A output does
not have a dedicated persisted aggregate output hash, so the contract states
that explicitly.

### Stage 2B

Uses the current verification rows, route discovery state and the existing
deterministic verification signature. If exactly one verifier generation is
present it is exposed as `generation_id`; mixed generations remain visible in
`details.generation_ids` instead of being guessed into one value.

The clean zero-route case is represented as a current/completed Stage-2B
contract once route discovery is current.

### Stage 2C

Uses the existing verification signature, output signature and semantic output
signature. Rule-version drift, stale verification, missing outputs and
publication blockers remain visible through the existing freshness reason.

### Review gate

Combines structural source review and Verifier Audit state. A testing bypass can
make `ready_to_advance=true` while `human_review_required=true`; unresolved
human decisions are never relabeled as accepted.

### Stage 3

Uses the existing Stage-2C source signature and Stage-3 task/rule metadata. New
Stage-3 builds also persist:

- `data_hash`: SHA-256 of canonical chunk rows plus retrieval rows
- `data_hash_basis=canonical_chunks_and_retrieval_rows_sha256`

The hash is computed once while the rows are already in memory during the build.
It is not recomputed on polling. Existing Stage-3 artifacts remain valid and
report `legacy_or_not_persisted` until a normal rebuild creates the hash.

A Stage-3 artifact can be `current=true` but `ready_to_advance=false` if a
newer human-review gate is blocking downstream use.

### Machine embeddings and RAG

Machine contracts use the existing equipment-scoped semantic corpus
fingerprint. It is labeled as a semantic corpus fingerprint rather than a
byte-for-byte vector-file hash. RAG remains equipment-scoped; single-book
hybrid fallback is not introduced.

An old/current machine embedding can remain diagnostically `current=true`
while `ready_to_advance=false` if an upstream review blocker is active.

## API

Full and exact document status include `stage_contracts`.

Compact `/api/documents/summary` intentionally omits them.

Direct read-only access:

`GET /api/documents/{job_id}/contracts`

Response schema: `pipeline-stage-set/v1`.

## Regression coverage

The Phase-6 tests cover:

- strict required fields
- clean zero-route verification
- stale Stage-2C signature mismatch
- human review blocking downstream readiness despite an old current Stage 3
- Stage-3 upstream signature and persisted data hash
- equipment-scoped machine/RAG contracts
- old machine state behind an upstream review blocker
- compact summary omission
- read-only contract endpoint
- Stage-3 output hash persistence

