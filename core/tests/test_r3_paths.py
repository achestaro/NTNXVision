"""R3-03 / R3-04: path text is judged before any filesystem call; hostnames match fully."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ntnxvision_core import sidecar
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.api.transport import valid_hostname


def test_hostname_with_trailing_newline_is_rejected() -> None:
    assert valid_hostname("pc01.example.lab")
    assert not valid_hostname("pc01.example.lab\n")
    assert not valid_hostname("pc01\n")
    assert not valid_hostname("198.51.100.7\n")


@pytest.fixture()
def snaps(tmp_path: Path):
    root = tmp_path / "snapshots"
    root.mkdir()
    (root / "a.json").write_text("{}")
    with patch.object(sidecar, "snapshots_dir", lambda: root):
        yield root


def test_snapshot_path_inside_root_resolves(snaps: Path) -> None:
    assert sidecar._resolve_snapshot_path(str(snaps / "a.json")) == (snaps / "a.json").resolve()
    assert sidecar._resolve_snapshot_path("a.json") == (snaps / "a.json").resolve()


@pytest.mark.parametrize(
    "raw",
    [
        "\\\\attacker.example.lab\\share\\a.json",
        "//attacker.example.lab/share/a.json",
        "\\\\?\\C:\\x\\a.json",
        "a.json\0",
    ],
)
def test_unc_paths_are_rejected_before_resolve(snaps: Path, raw: str) -> None:
    with (
        patch.object(Path, "resolve", side_effect=AssertionError("touched the filesystem")),
        pytest.raises(NutanixApiError),
    ):
        sidecar._resolve_snapshot_path(raw)


def test_absolute_path_outside_root_is_rejected_lexically(snaps: Path, tmp_path: Path) -> None:
    real_resolve = Path.resolve

    def guarded(self: Path, *a: object, **k: object) -> Path:
        if "other" in self.name:
            raise AssertionError("resolved the untrusted path")
        return real_resolve(self, *a, **k)  # type: ignore[arg-type]

    for raw in (str(tmp_path / "other.json"), str(snaps / ".." / "other.json"), str(snaps.parent)):
        with patch.object(Path, "resolve", guarded), pytest.raises(NutanixApiError):
            sidecar._resolve_snapshot_path(raw)
