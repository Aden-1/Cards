"""Print a repeatable raw/compressed static asset and template reference audit."""

from __future__ import annotations

import gzip
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = ROOT / "static"
TEMPLATE_ROOT = ROOT / "templates"
STATIC_REFERENCE = re.compile(r"(?:asset_url|url_for\(['\"]static['\"])[^\n]*")
ASSET_URL = re.compile(r"asset_url\(['\"]([^'\"]+)['\"]\)")


def main() -> None:
    assets = sorted(path for path in STATIC_ROOT.rglob("*") if path.is_file())
    references = []
    referenced_assets = set()
    for template in sorted(TEMPLATE_ROOT.glob("*.html")):
        for line_number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
            if STATIC_REFERENCE.search(line):
                references.append(f"{template.relative_to(ROOT)}:{line_number}")
            referenced_assets.update(ASSET_URL.findall(line))

    missing_assets = sorted(
        filename for filename in referenced_assets if not (STATIC_ROOT / filename).is_file()
    )
    if missing_assets:
        raise SystemExit(f"Missing referenced static assets: {', '.join(missing_assets)}")

    print("Static assets (raw / gzip):")
    for asset in assets:
        data = asset.read_bytes()
        compressed = gzip.compress(data, compresslevel=9)
        print(f"  {asset.relative_to(ROOT)}: {len(data):,} / {len(compressed):,} bytes")
    print(f"Template static reference lines: {len(references)}")
    print(f"Referenced asset files: {len(referenced_assets)}")
    for reference in references:
        print(f"  {reference}")


if __name__ == "__main__":
    main()
