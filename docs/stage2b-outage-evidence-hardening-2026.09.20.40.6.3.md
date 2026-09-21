# Stage 2B Outage + Vision Evidence Hardening — 2026.09.20.40.6.3

## Vision overlap arbitration

Legacy `.40.6.2` overlap cleanup always preferred the normal picture route and could suppress useful applied sweep enrichment. `.40.6.3` resolves each same-generation/same-picture overlap using this deterministic rule:

1. `status=applied` beats non-applied (`pending`, `excluded`, etc.).
2. If both sides have equal applied/non-applied state, the normal route wins the tie.
3. The loser remains in the ledger as `superseded` for audit.
4. `.40.6.2` entries previously marked `SUPERSEDED_BY_NORMAL_PICTURE_ROUTE` are reconstructable from their persisted verifier verdict and structural evidence.

Dry-run against `_processed (11).zip`: 162 overlaps; 11 sweep winners and 151 normal winners. The 11 sweep winners are MacGregor 6, Instruction Manual 3, AD136TI 1, and engine-room marine-electrical 1.

## Endpoint circuit breaker

`httpx.ConnectError`, `httpx.ConnectTimeout`, and native `ConnectionError` against local Pi5/OnePlus are treated as endpoint outages rather than job failures. The in-flight job is returned to pending using `mark_deferred`, so `retry_count` is unchanged. The provider circuit opens and blocks both normal claims using that provider and shared artifact claims on that physical worker.

Backoff starts at `stage2b_endpoint_breaker_base_seconds` (default 30 s), doubles up to `stage2b_endpoint_breaker_max_seconds` (default 300 s), and requires `/health` to succeed before claims resume. One file under `processed/_verification_outages/` is created and updated for the outage. Recovery is recorded in that same file.

At startup, current jobs previously failed by older `ConnectError`/`ConnectTimeout`/`ConnectionError`/`NetworkError` retry exhaustion are returned to pending with retry_count reset.

## Artifact sweep readiness

- `stage2b_artifact_sweep_enabled: true` keeps automatic preparation.
- `stage2b_artifact_sweep_required_for_finalize: true` preserves the strict default sequence.
- Setting `required_for_finalize: false` excludes unfinished sweep rows from the Stage 2C verification signature and blocker set. A sweep row enters the signature once completed, making Stage 2C stale so its new evidence can be incorporated on rebuild.
- Books with zero normal Text/Vision routes release their prepared sweep immediately.

## Stale cleanup confirmation

The preview response carries a SHA-256 confirmation token over the exact candidate set. The clear endpoint requires that token and rescans before deletion. If candidates changed, HTTP 409 is returned and the operator must scan again.

## Upgrade requirement

Legacy duplicate-row suppression changes current Stage 2B signatures for existing books. After deployment run **Revalidate all + rebuild** and wait for affected machine embeddings to become current before using Machine RAG.
