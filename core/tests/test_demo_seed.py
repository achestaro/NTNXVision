"""Demo mode opens on data: `demo_seed` fills an empty demo profile once."""

from __future__ import annotations

import pytest

from ntnxvision_core import sidecar
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.synthetic import demo


@pytest.fixture()
def demo_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("NTNXVISION_DEMO", "1")
    monkeypatch.setenv("NTNXVISION_DATA_DIR", str(tmp_path))
    demo.enable(latency_ms=0)
    demo.reset()
    yield tmp_path
    demo.reset()


def test_seed_fills_an_empty_profile_once(demo_profile):
    result = sidecar._m_demo_seed(1, {"export_dir": str(demo_profile / "exports")})
    assert result["seeded"] is True
    names = result["snapshots"]
    assert len(names) == 6
    assert any(n.startswith("pe01-") for n in names)
    assert "combined-pc02-pc03" in names

    listing = sidecar._m_list_snapshots(2, {})["snapshots"]
    assert {s["kind"] for s in listing} >= {"pc", "pe", "combined"}
    # Backdated: the history spans about two weeks, newest first.
    stamps = sorted(s["collected_at"] for s in listing)
    assert stamps[0][:10] < stamps[-1][:10]

    # The latest pc01 run wrote reports, so Results has files to show.
    assert list((demo_profile / "exports").glob("*.xlsx"))
    assert list((demo_profile / "exports").glob("*.docx"))

    # Compare has a real pc01 pair.
    pc01 = sorted((s for s in listing if s["name"].startswith("pc01-")),
                  key=lambda s: s["collected_at"])
    diff = sidecar._m_compare_snapshots(3, {"baseline": pc01[0]["path"],
                                            "current": pc01[-1]["path"]})
    assert diff

    # Idempotent: a second launch leaves the profile alone.
    assert sidecar._m_demo_seed(4, {}) == {"seeded": False, "snapshots": []}


def test_seed_refuses_outside_demo_mode(monkeypatch):
    monkeypatch.delenv("NTNXVISION_DEMO", raising=False)
    with pytest.raises(NutanixApiError):
        sidecar._m_demo_seed(1, {})
