"""Enforce the documented GPL-3.0-compatible runtime dependency license policy."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ALLOWED_LICENSES = {
    "Apache Software License",
    "Apache-2.0",
    "BSD License",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "MIT",
    "MIT License",
    "MIT AND PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)",
    "PSF-2.0",
    "Python Software Foundation License",
}


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_packages(lockfile: Path) -> set[str]:
    package_pattern = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==")
    packages = set()
    for line in lockfile.read_text(encoding="utf-8").splitlines():
        match = package_pattern.match(line)
        if match:
            packages.add(normalized(match.group(1)))
    return packages


def approved(license_name: str) -> bool:
    # pip-licenses emits both SPDX ("Apache-2.0 OR BSD-2-Clause") and
    # human-readable ("Apache Software License; BSD License") dual licenses.
    parts = re.split(r"\s+(?:AND|OR)\s+|;", license_name)
    return all(part.strip() in ALLOWED_LICENSES for part in parts)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_dependency_licenses.py LICENSES.json requirements.txt", file=sys.stderr)
        return 2

    inventory_path, lockfile_path = map(Path, sys.argv[1:])
    locked = locked_packages(lockfile_path)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8-sig"))
    licenses = {
        normalized(item["Name"]): item.get("License", "UNKNOWN").strip() or "UNKNOWN"
        for item in inventory
    }

    missing = sorted(locked - licenses.keys())
    rejected = sorted(
        (name, licenses[name])
        for name in locked & licenses.keys()
        if not approved(licenses[name])
    )
    if missing or rejected:
        if missing:
            print("Missing license inventory entries: " + ", ".join(missing), file=sys.stderr)
        for name, license_name in rejected:
            print(f"Rejected license for {name}: {license_name}", file=sys.stderr)
        return 1

    print(f"License policy passed for {len(locked)} locked runtime packages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
