#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""List the Python packages frozen into the NTNXVision sidecar, with their licenses.

Walks the *runtime* dependency closure of ``ntnxvision-core`` (its ``[project]
dependencies`` without any extra, so dev tools such as pytest/ruff/mypy/pyinstaller and the
optional ``ntnx-v4`` SDKs are excluded) and runs ``pip-licenses`` over exactly that set.
Prints JSON on stdout; ``scripts/generate-third-party-notices.mjs`` renders it into
THIRD_PARTY_NOTICES.md.

Run it with the core's venv Python (``core/.venv/bin/python``), where ``pip install -e
'.[dev]'`` has installed both the runtime deps and pip-licenses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib import metadata

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT_DIST = "ntnxvision-core"


def runtime_closure(root: str) -> list[str]:
    """Canonical names of every installed distribution ``root`` needs at runtime."""
    seen: set[str] = set()
    stack = [root]
    while stack:
        name = stack.pop()
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # a marker-excluded or optional requirement that isn't installed
        for spec in dist.requires or []:
            req = Requirement(spec)
            # Skip extras (dev, ntnx-v4, ...) and requirements whose markers don't apply here.
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            dep = canonicalize_name(req.name)
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    seen.discard(canonicalize_name(root))
    return sorted(seen)


def cpython_license() -> str:
    """The interpreter's own LICENSE.txt (PSF + the bundled C libraries' notices)."""
    ver = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    candidates = [
        os.path.join(sys.base_prefix, "lib", ver, "LICENSE.txt"),
        os.path.join(sys.base_prefix, "LICENSE.txt"),  # Windows python.org layout
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
    return ""


def pyinstaller_license() -> str:
    """PyInstaller's COPYING.txt (GPL-2.0 + bootloader exception), if PyInstaller is installed."""
    try:
        dist = metadata.distribution("pyinstaller")
    except metadata.PackageNotFoundError:
        return ""
    for f in dist.files or []:
        if f.name.upper().startswith("COPYING"):
            return f.read_text(encoding="utf-8") or ""
    return ""


def main() -> int:
    packages = runtime_closure(ROOT_DIST)
    if not packages:
        print(f"{ROOT_DIST} is not installed in {sys.executable}", file=sys.stderr)
        return 1
    out = subprocess.run(
        [
            sys.executable, "-m", "piplicenses",
            "--from=mixed",
            "--with-license-file",
            "--with-notice-file",
            "--no-license-path",
            "--with-urls",
            "--format=json",
            "--packages", *packages,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows = json.loads(out)
    found = {canonicalize_name(r["Name"]) for r in rows}
    json.dump(
        {
            "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
            "cpython_license": cpython_license(),
            "pyinstaller_license": pyinstaller_license(),
            "packages": sorted(rows, key=lambda r: canonicalize_name(r["Name"])),
            "missing": sorted(set(packages) - found),
        },
        sys.stdout,
        indent=1,
        sort_keys=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
