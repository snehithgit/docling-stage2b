# Release validation — 2026.09.19.40.5

Release: **Source Fidelity & Retrieval Integrity**

## Source-tree validation

- `pytest`: **418 passed**
- Python compileall: PASS
- static JavaScript `node --check`: PASS
- shell syntax: PASS
- YAML / Compose parse: PASS
- benchmark JSON parse: PASS
- APP_VERSION: `2026.09.19.40.5`

## Real processed-corpus dry run

On a copied seven-manual processed corpus, deterministic `.40.5` correction-ledger replay required no model calls and preserved human-reviewed decisions.

- automatic applied text corrections before replay: **400**
- automatic applied after replay: **337**
- unsafe/pending legacy entries surfaced after replay: **64**
- changed-ledger count: **63** (one entry already carried prior safety-revalidation metadata)

Main reasons: `CRITICAL_SOURCE_TOKEN_NOT_PRESERVED`, `TROUBLESHOOTING_ACTION_DROPPED`, `SOURCE_CONTENT_CONTRACTION`, `INCOMPLETE_SENTENCE_END`, and scope inversion.

## Deliberately not claimed

A fresh `.40.5` N150 BGE/TEI hybrid benchmark was not run from the build environment because the user's LAN embedding endpoint is not reachable here and the live artifact queue is still processing. Deployment acceptance must run the fresh machine-scoped benchmark after the queue drains and downstream stages settle.

Historical lexical/candidate replay metrics remain reference data only; they are not relabeled as fresh `.40.5` production results.
