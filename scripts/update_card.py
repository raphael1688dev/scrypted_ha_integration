"""Vendor a tagged scrypted-camera-card release into the integration.

Downloads the built card from the release's assets, writes it to
custom_components/scrypted/frontend/ and records its version and SHA-256 in
custom_components/scrypted/card.py. The card repository stays the source of
truth; nothing is built here.

Usage:
    python scripts/update_card.py v0.5.10
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys
import urllib.request

REPOSITORY = "raphael1688dev/scrypted-camera-card"
ASSET = "scrypted-camera-card.js"

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "scrypted"
CARD_FILE = INTEGRATION / "frontend" / ASSET
CARD_MODULE = INTEGRATION / "card.py"


def _set_constant(source: str, name: str, value: str) -> str:
    """Replace the string assigned to a module-level constant."""
    updated, count = re.subn(
        rf'^{name} = "[^"]*"$', f'{name} = "{value}"', source, flags=re.MULTILINE
    )
    if count != 1:
        raise SystemExit(f"expected exactly one {name} assignment in {CARD_MODULE}")
    return updated


def main(argv: list[str]) -> None:
    """Fetch the release asset for the tag given on the command line."""
    if len(argv) != 2 or not re.fullmatch(r"v\d+\.\d+\.\d+", argv[1]):
        raise SystemExit("usage: update_card.py vX.Y.Z (full releases only)")
    tag = argv[1]
    version = tag.removeprefix("v")
    url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{ASSET}"

    with urllib.request.urlopen(url, timeout=60) as response:
        bundle = response.read()
    # The card's build injects package.json's version, and its release workflow
    # refuses a tag that disagrees with it, so the bundle must name its release.
    if version.encode() not in bundle:
        raise SystemExit(f"{url} does not look like release {version} of the card")

    digest = hashlib.sha256(bundle).hexdigest()
    CARD_FILE.parent.mkdir(exist_ok=True)
    CARD_FILE.write_bytes(bundle)
    source = CARD_MODULE.read_text(encoding="utf-8")
    source = _set_constant(source, "CARD_VERSION", version)
    source = _set_constant(source, "CARD_SHA256", digest)
    CARD_MODULE.write_text(source, encoding="utf-8")
    print(f"{ASSET} {version}: {len(bundle)} bytes, sha256 {digest}")


if __name__ == "__main__":
    main(sys.argv)
