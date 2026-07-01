"""Cross-platform application data paths (Windows + macOS)."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "NTNXVision"


def app_data_dir() -> Path:
    """Per-user application data directory."""
    if os.name == "nt":  # Windows
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:  # macOS / *nix
        base = str(Path.home() / "Library" / "Application Support")
    path = Path(base) / APP_NAME
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
