# ZimaOS image install

The GitHub Actions workflow publishes the image to:

`ghcr.io/<github-owner>/<repository>:latest`

For this account, if the repository is named `docling-stage2b`, the image is:

`ghcr.io/snehithgit/docling-stage2b:latest`

Use the same volume mappings and environment from `docker-compose.yml`, replacing `build: .` with the GHCR image.
