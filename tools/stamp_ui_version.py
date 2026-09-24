#!/usr/bin/env python3
"""Stamp frontend cache keys/version badge from app.version.APP_VERSION.

This makes app/version.py the single canonical release-version source.  The
script is intentionally idempotent and is run during the Docker image build;
release packaging may run it too.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "app" / "version.py"
STATIC_DIR = ROOT / "app" / "static"


def app_version() -> str:
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']\s*$', VERSION_FILE.read_text(encoding="utf-8"), re.M)
    if not match:
        raise SystemExit(f"Could not read APP_VERSION from {VERSION_FILE}")
    return match.group(1)


def stamp(version: str) -> int:
    changed = 0
    # Every cache-busted local asset reference in HTML uses the canonical build version.
    for path in STATIC_DIR.glob("*.html"):
        old = path.read_text(encoding="utf-8")
        new = re.sub(r'(/assets/[^"\'?#]+\?v=)[^"\']+', rf'\g<1>{version}', old)
        if new != old:
            path.write_text(new, encoding="utf-8")
            changed += 1

    # nav.js retains the UI build identity so it can detect a genuinely stale UI.
    nav = STATIC_DIR / "nav.js"
    old = nav.read_text(encoding="utf-8")
    new, count = re.subn(r'(const version\s*=\s*)["\'][^"\']+["\']', rf'\g<1>"{version}"', old, count=1)
    if count != 1:
        raise SystemExit("Could not find the nav.js UI version marker")
    if new != old:
        nav.write_text(new, encoding="utf-8")
        changed += 1
    return changed


if __name__ == "__main__":
    version = app_version()
    changed = stamp(version)
    print(f"Stamped UI version {version} ({changed} file(s) changed)")
