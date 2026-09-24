# Release validation — 2026.09.19.40.4.1

## Scope

Vision-provider routing hotfix on top of `.40.4`.

## Fixed defect

Full technical-artifact sweep jobs created by `.40.4` could carry a legacy per-job `source.processor` value (`pi5` or `oneplus`). Stage 2B treated that value as a forced provider override, so changing the Vision selector did not control every sweep job.

## Required contract

- `vision_verifier_provider=pi5` → every Vision-role inference resolves to `pi5_url`.
- `vision_verifier_provider=oneplus` → every Vision-role inference resolves to `oneplus_url`.
- `vision_verifier_provider=groq` → every Vision-role inference resolves to Groq Vision.
- Legacy sweep `source.processor` hints are ignored.
- No automatic fallback.
- New full sweeps use the Vision-role queue instead of physical-device round robin.

## Source-tree validation

- pytest: **399/399 passed**
- Python compile: PASS
- JavaScript syntax: PASS
- shell syntax: PASS
- YAML / Compose parse: PASS
- benchmark JSON parse: PASS

## Archive validation

A candidate ZIP was extracted into a fresh directory and validated:

- pytest: **399/399 passed**
- Python compile: PASS
- JavaScript syntax: PASS
- shell syntax: PASS
- YAML / Compose parse: PASS
- benchmark JSON parse: PASS
- app version: `2026.09.19.40.4.1`

The published ZIP is rebuilt from this documented tree and receives one final clean-extraction validation before release.
