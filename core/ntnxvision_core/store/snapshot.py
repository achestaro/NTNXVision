"""Snapshot store.

A collection run (`Environment`) is serialized to a single JSON file so the environment
can be re-exported to any format offline, and so two runs can be diffed later. Snapshots
live under the user profile by default and are git-ignored (they can contain sensitive
topology).
"""

from __future__ import annotations

import json
from pathlib import Path

from ntnxvision_core.models import Environment


class SnapshotStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, env: Environment, name: str) -> Path:
        path = self.directory / f"{_safe(name)}.ntnxsnap.json"
        path.write_text(
            env.model_dump_json(indent=2, exclude_none=False),
            encoding="utf-8",
        )
        return path

    def load(self, path: str | Path) -> Environment:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return Environment.model_validate(data)

    def list_snapshots(self) -> list[Path]:
        return sorted(self.directory.glob("*.ntnxsnap.json"))


def _safe(name: str) -> str:
    keep = "-_.() "
    return "".join(c if c.isalnum() or c in keep else "_" for c in name).strip()
