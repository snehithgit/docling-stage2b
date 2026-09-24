import re
from pathlib import Path

from app.version import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def test_nav_ui_version_matches_backend_version():
    nav = (STATIC / "nav.js").read_text(encoding="utf-8")
    match = re.search(r'const version\s*=\s*["\']([^"\']+)["\']', nav)
    assert match, "nav.js UI version marker is missing"
    assert match.group(1) == APP_VERSION


def test_all_static_asset_cache_keys_match_backend_version():
    mismatches = []
    for path in STATIC.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        for value in re.findall(r'/assets/[^"\'?#]+\?v=([^"\']+)', text):
            if value != APP_VERSION:
                mismatches.append((path.name, value))
    assert not mismatches, f"stale frontend version markers: {mismatches}"
