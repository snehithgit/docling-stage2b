# Release validation — 2026.09.23.40.11M

Version-synchronization hotfix based on 40.11L.

## Fix

- `app/version.py` is the canonical release version.
- Added `tools/stamp_ui_version.py` to stamp the UI badge and every local asset cache key from that canonical value.
- Docker builds now run the stamper automatically after copying `app/`, preventing backend/UI version drift such as `40.11K` backend with `40.11J` UI.
- Added regression tests that fail if `nav.js` or any HTML asset cache key differs from `APP_VERSION`.
- The UI still keeps an independent stamped build identity at runtime, so a genuinely stale browser asset can still be detected rather than silently masked.
