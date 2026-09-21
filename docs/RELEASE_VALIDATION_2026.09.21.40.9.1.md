# Release validation — 2026.09.21.40.9.1

## Scope

GitHub publisher hotfix. The safety checks continue to refuse real `.env` variants, runtime data directories, and local database files, while allowing the intentionally tracked `.env.example` template.

## Root cause

The publisher regex treated every `.env.*` path as sensitive. That was stricter than `.gitignore`, which intentionally tracks `!.env.example`, so a normal source checkout could never publish once `.env.example` was tracked.

## Fix

- `upload-github.ps1`: exclude `.env.example` from tracked/staged sensitive-file rejection.
- `upload-github-stage2c.ps1`: same fix.
- `upload-github.sh`: same fix.
- Real `.env`, `.env.local`, `.env.production`, runtime folders, and database files remain blocked.
- Added regression coverage for the tracked `.env.example` exception.

## Notes

PowerShell execution-policy and Git LF/CRLF warnings are host-side behavior and are not publication failures.

## Validation

- Full pytest suite: **483/483 passed**.
- Python compileall: PASS.
- JavaScript syntax: PASS.
- Bash publisher syntax: PASS.
- YAML/Compose parse: PASS.
- Final ZIP integrity: PASS.
- Extracted-package full pytest suite: **483/483 passed** (repeated verification).
