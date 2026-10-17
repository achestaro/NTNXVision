"""SEC-17: the app's data folder and the files it writes are owner-only on macOS/Linux."""

from __future__ import annotations

import os
import stat

import pytest

from ntnxvision_core import paths
from ntnxvision_core.store.snapshot import SnapshotStore

posix_only = pytest.mark.skipif(os.name == "nt", reason="Windows uses per-user ACLs under AppData")


def mode(p) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


@posix_only
def test_data_dirs_are_owner_only_and_existing_ones_are_tightened(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    (root / "snapshots").mkdir()
    os.chmod(root, 0o755)
    os.chmod(root / "snapshots", 0o755)
    monkeypatch.setenv("NTNXVISION_DATA_DIR", str(root))
    assert paths.snapshots_dir() == (root / "snapshots").resolve()
    assert mode(root) == 0o700
    assert mode(root / "snapshots") == 0o700
    paths.exports_dir()
    assert mode(root / "exports") == 0o700


@posix_only
def test_snapshot_and_meta_files_are_owner_only(tmp_path):
    from ntnxvision_core.models import Environment

    store = SnapshotStore(tmp_path / "snaps")
    assert mode(tmp_path / "snaps") == 0o700
    path = store.save(Environment(), "one")
    assert mode(path) == 0o600
    metas = list((tmp_path / "snaps").glob("*.ntnxmeta.json"))
    assert metas and all(mode(m) == 0o600 for m in metas)


@posix_only
def test_write_private_text_tightens_an_existing_file(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("{}")
    os.chmod(f, 0o644)
    paths.write_private_text(f, "{}")
    assert mode(f) == 0o600
