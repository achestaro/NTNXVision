"""One product version: app/package.json, the core package and new snapshots agree."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ntnxvision_core import __version__
from ntnxvision_core.models import Environment

ROOT = Path(__file__).resolve().parents[2]


def test_versions_agree() -> None:
    pkg = json.loads((ROOT / "app/package.json").read_text(encoding="utf-8"))["version"]
    lock = json.loads((ROOT / "app/package-lock.json").read_text(encoding="utf-8"))
    pyproject = (ROOT / "core/pyproject.toml").read_text(encoding="utf-8")
    py = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)  # type: ignore[union-attr]
    assert re.fullmatch(r"\d+\.\d+\.\d+", pkg)
    assert pkg == lock["version"] == lock["packages"][""]["version"] == py == __version__
    assert Environment().tool_version == __version__


def test_changelog_has_current_version() -> None:
    assert f"## {__version__} " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
