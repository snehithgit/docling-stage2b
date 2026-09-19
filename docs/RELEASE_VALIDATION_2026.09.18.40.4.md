# Release validation — 2026.09.18.40.4

## Source-tree gate

- App version: `2026.09.18.40.4`
- Full automated suite: **398/398 passed** with `PYTHONPATH=.`.
- Python `compileall`: PASS.
- Every `app/static/*.js` with `node --check`: PASS.
- Shell scripts with `bash -n`: PASS.
- `config.yaml`, `config.example.yaml`, and `docker-compose.yml` parse: PASS.
- All benchmark JSON under `docs/`: PASS.
- Static DOM audit on all HTML: viewport, skip link, and `#main-content` target present: PASS.
- Live headless Chromium screenshot rendering is not used as a release gate because local/file navigation is restricted/hangs in this validation sandbox. Functional UI behavior remains covered by the automated static/UI tests.

## Architecture assertions retained

- Strict stage sequencing: Docling -> 2A -> 2B -> 2C -> Stage 3 -> table/context reconstruction -> retrieval index -> machine embedding -> Machine RAG.
- One authoritative hybrid embedding corpus per physical machine.
- Incremental rebuild reuses unchanged vectors but atomically replaces the complete machine corpus.
- Normal RAG never searches unrelated machines or all books.
- Historical/Draft manuals remain auditable but do not enter normal Machine RAG.
- Canonical Stage 3 chunks remain immutable; reconstruction is derived evidence.

## External deployment acceptance still required

The release contains the fresh machine-hybrid benchmark endpoint/UI, but this build environment cannot reach the user's LAN N150 TEI service. After deployment, run the fresh machine-hybrid benchmark and record Top-1/3/5/10, MRR, skipped cases, and query latency. Historical replay numbers must not be presented as a fresh `.40.4` N150 run.

## Exact ZIP gate

- Candidate archive integrity: PASS.
- Candidate clean extraction: PASS.
- Full extracted candidate suite: **398/398 passed**.
- Extracted Python/JS/shell/YAML/JSON/static-DOM checks: PASS.
- Final published archive is rebuilt from the same source tree with only release-state documentation updated; it is re-extracted and revalidated before publication.
- SHA-256 is published outside the ZIP.
