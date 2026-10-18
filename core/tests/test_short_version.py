"""Build strings shorten to the plain version everywhere (model, index, reports)."""

from __future__ import annotations

import json

import pytest

from ntnxvision_core.models import Cluster, Environment
from ntnxvision_core.versions import short_version

LONG = "el8.5-release-ganges-7.5.1.1-stable-0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (LONG, "7.5.1.1"),
        ("el8.5-release-fraser-pc.2024.3-stable-pc-0", "pc.2024.3"),
        ("el8.5-release-fraser-6.8.1-stable-0", "6.8.1"),
        ("el7.3-release-euphrates-5.20.4.6-stable-0123456789abcdef0123", "5.20.4.6"),
        ("fraser-7.0.1-stable-0123456789abcdef0123", "7.0.1"),
        ("7.5.1.6", "7.5.1.6"),
        ("pc.2024.3", "pc.2024.3"),
        ("4.3.0", "4.3.0"),
        ("el8.nutanix", "el8.nutanix"),
        (None, None),
    ],
)
def test_short_version(raw: str | None, expected: str | None) -> None:
    assert short_version(raw) == expected


def test_model_shortens_on_load() -> None:
    env = Environment.model_validate({
        "prism_central": {"host": "pc.example.lab", "version": LONG},
        "pc_config": {"version": LONG, "build": LONG},
        "clusters": [{"uuid": "c1", "aos_version": LONG}],
    })
    assert env.prism_central.version == "7.5.1.1"
    assert env.pc_config.version == env.pc_config.build == "7.5.1.1"
    assert env.clusters[0].aos_version == "7.5.1.1"
    assert Cluster(uuid="c2", aos_version=LONG).aos_version == "7.5.1.1"


def test_old_index_shortened_on_read(tmp_path) -> None:
    from ntnxvision_core.store.snapshot import SnapshotStore, meta_path, read_meta

    env = Environment.model_validate({"prism_central": {"host": "pc.example.lab"}})
    path = SnapshotStore(tmp_path).save(env, name="old")
    meta = read_meta(path)
    meta.update(version=LONG, snapshot_mtime=path.stat().st_mtime)
    meta["stats"]["aos_min"] = LONG
    meta_path(path).write_text(json.dumps(meta), encoding="utf-8")
    again = read_meta(path)
    assert again["version"] == "7.5.1.1"
    assert again["stats"]["aos_min"] == "7.5.1.1"
