# ZimaOS image install

The GitHub Actions workflow publishes the image to:

`ghcr.io/<github-owner>/<repository>:latest`

For this account, if the repository is named `docling-stage2b`, the image is:

`ghcr.io/snehithgit/docling-stage2b:latest`

Use the same volume mappings and environment from `docker-compose.yml`, replacing `build: .` with the GHCR image.

## Release 2026.09.11.21

Text/OCR reconstruction and Image analysis each independently support `Pi5`, `OnePlus`, or `Groq`. There is no automatic provider fallback.

For Text/OCR reconstruction, READABLE source-image target transcriptions are applied automatically to the Stage 2C overlay. The Audit page shows the already-applied text and does not require a Save click; Save is only a manual override.

The `.21` target-scope guard trims BEFORE/AFTER context, repeated model output and reasoning residue, and rejects clearly unrelated adjacent-block captures before automatic apply. Pure rare-near-frequent edit-distance OCR-recall matches remain diagnostic only and no longer create verifier routes by themselves.

Set `GROQ_API_KEY` only if Groq will be selected. Pi5/OnePlus-only operation does not require a Groq key.
