"""Cross-platform application data paths (Windows + macOS).

Two environment overrides exist, both for isolation rather than configuration:

* ``NTNXVISION_DATA_DIR`` — use this directory instead of the per-user default. Tests and
  the end-to-end suite point it at a temporary folder so they never touch real snapshots.
* Demo mode (``NTNXVISION_DEMO=1`` / ``--demo``) keeps its synthetic snapshots apart from
  real ones: the default directory becomes ``NTNXVision-demo`` next to ``NTNXVision``.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

APP_NAME = "NTNXVision"


def private_dir(path: Path) -> Path:
    """Create `path` and keep it owner-only (0o700) on macOS/Linux.

    Windows needs nothing: folders under AppData inherit per-user ACLs. An existing
    folder from an older version is tightened too (on every startup that calls this).
    """
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        with contextlib.suppress(OSError):
            path.chmod(0o700)
    return path


def write_private_text(path: Path, text: str) -> None:
    """Write a text file the app owns: owner-only (0o600) on macOS/Linux, plain on Windows."""
    if os.name == "nt":
        path.write_text(text, encoding="utf-8")
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)  # a file that already existed keeps its old mode otherwise


def demo_enabled() -> bool:
    """True when the process runs against the built-in mock Nutanix estate."""
    return os.environ.get("NTNXVISION_DEMO", "").strip().lower() in ("1", "true", "yes", "on")


def app_data_dir() -> Path:
    """Per-user application data directory."""
    override = os.environ.get("NTNXVISION_DATA_DIR", "").strip()
    if override:
        path = private_dir(Path(override).expanduser())
        # Resolved, so every snapshot path the core hands out is canonical: the UI
        # matches results to list entries by path, and /var vs /private/var (macOS
        # temp dirs) or a symlinked profile would otherwise never compare equal.
        return path.resolve()
    if os.name == "nt":  # Windows
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:  # macOS / *nix
        base = str(Path.home() / "Library" / "Application Support")
    name = f"{APP_NAME}-demo" if demo_enabled() else APP_NAME
    path = private_dir(Path(base) / name)
    return path.resolve()


def snapshots_dir() -> Path:
    return private_dir(app_data_dir() / "snapshots")


def exports_dir() -> Path:
    return private_dir(app_data_dir() / "exports")
