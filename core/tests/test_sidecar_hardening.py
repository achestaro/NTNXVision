"""Regression tests for sidecar hardening: snapshot path confinement + save guard."""

from __future__ import annotations

import pytest

from ntnxvision_core.api.errors import NutanixApiError


def test_resolve_snapshot_path_rejects_outside_dir(tmp_path, monkeypatch) -> None:
    import ntnxvision_core.sidecar as sc

    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    monkeypatch.setattr(sc, "snapshots_dir", lambda: snaps)

    # A path outside the snapshots directory is rejected (no arbitrary file reads).
    outside = tmp_path / "secret.txt"
    outside.write_text("sensitive", encoding="utf-8")
    with pytest.raises(NutanixApiError):
        sc._resolve_snapshot_path(str(outside))


def test_resolve_snapshot_path_missing_file(tmp_path, monkeypatch) -> None:
    import ntnxvision_core.sidecar as sc

    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    monkeypatch.setattr(sc, "snapshots_dir", lambda: snaps)

    with pytest.raises(NutanixApiError, match="not found"):
        sc._resolve_snapshot_path("does-not-exist.ntnxsnap.json")


def test_resolve_snapshot_path_accepts_bare_name(tmp_path, monkeypatch) -> None:
    import ntnxvision_core.sidecar as sc

    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    monkeypatch.setattr(sc, "snapshots_dir", lambda: snaps)
    f = snaps / "env.ntnxsnap.json"
    f.write_text("{}", encoding="utf-8")

    resolved = sc._resolve_snapshot_path("env.ntnxsnap.json")
    assert resolved == f.resolve()


def test_finalize_survives_snapshot_save_failure(tmp_path, monkeypatch) -> None:
    """A save() failure must not discard an otherwise-successful collection."""
    import ntnxvision_core.sidecar as sc
    from ntnxvision_core.models import Environment
    from ntnxvision_core.store import SnapshotStore

    monkeypatch.setattr(sc, "snapshots_dir", lambda: tmp_path)
    monkeypatch.setattr(sc, "exports_dir", lambda: tmp_path)

    def boom(self, env, name):  # noqa: ANN001
        raise OSError("disk full")

    monkeypatch.setattr(SnapshotStore, "save", boom)

    env = Environment()
    result = sc._finalize(env, "env", {"formats": []})

    # Run is preserved: summary present, snapshot_path None, warning recorded.
    assert result["summary"] is not None
    assert result["snapshot_path"] is None
    assert any("could not be saved" in w for w in result["warnings"])
