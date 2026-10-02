# Retrieval freshness / embedding reuse fix — 2026-10-02

## Problem

A query-ranking-only retrieval upgrade changed `RETRIEVAL_RULE_VERSION`. Stage 3 freshness treated that ranking version as a canonical index requirement, so already-current books were shown as needing Stage 3 index refresh. Rewriting `retrieval_index.jsonl` then changed file size/mtime metadata and the hybrid readiness check treated existing machine vectors as stale, causing an unnecessary embedding rebuild.

## Fix

- Query-time ranking rule changes no longer invalidate canonical Stage 3 chunks or an existing retrieval index.
- The ranking-version comparison is retained as diagnostics (`retrieval_rule_match` / `ranking_only_version_drift`).
- Hybrid embedding readiness uses file size/mtime only as a fast path. If those metadata values changed, the app verifies the semantic corpus fingerprint before declaring vectors stale.
- If the embedded heading/text corpus is unchanged, existing vectors remain valid and the machine reports Hybrid ready.
- A genuine chunk text/headings/manual-scope/model/profile change still invalidates embeddings.
- RAG Maintenance wording now states that ranking-only upgrades reuse existing indexes and embeddings.

## Validation

The one-shot GitHub workflow successfully applied the change, compiled the modified modules, and passed the existing retrieval/hybrid/scope tests plus two new regression tests:

1. ranking-rule mismatch does not make Stage 3 stale;
2. metadata-only retrieval-index rewrite reuses existing equipment embeddings, while a real text change still marks them stale.

Validated source commit: `f5f33ca41f10127461646534a96f2a8698be5934` (`Reuse embeddings across ranking-only retrieval upgrades`).

This documentation commit intentionally triggers the normal Docker publish workflow so `ghcr.io/snehithgit/docling-stage2b:latest` is built from the fixed source tree.
