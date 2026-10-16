"""Cross-platform application data paths (Windows + macOS).

Two environment overrides exist, both for isolation rather than configuration:

* ``NTNXVISION_DATA_DIR`` — use this directory instead of the per-user default. Tests and
  the end-to-end suite point it at a temporary folder so they never touch real snapshots.
* Demo mode (``NTNXVISION_DEMO=1`` / ``--demo``) keeps its synthetic snapshots apart from
  real ones: the default directory becomes ``NTNXVision-demo`` next to ``NTNXVision``.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "NTNXVision"


def demo_enabled() -> bool:
    """True when the process runs against the built-in mock Nutanix estate."""
    return os.environ.get("NTNXVISION_DEMO", "").strip().lower() in ("1", "true", "yes", "on")


def app_data_dir() -> Path:
    """Per-user application data directory."""
    override = os.environ.get("NTNXVISION_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    if os.name == "nt":  # Windows
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:  # macOS / *nix
        base = str(Path.home() / "Library" / "Application Support")
    name = f"{APP_NAME}-demo" if demo_enabled() else APP_NAME
    path = Path(base) / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshots_dir() -> Path:
    path = app_data_dir() / "snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def exports_dir() -> Path:
    path = app_data_dir() / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path
