"""The sidecar `optimization` method on a demo snapshot: the synthetic estate carries a mix
of compute findings (every severity) and clean VMs, so the demo page has something to show."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import ntnxvision_core.sidecar as sc
from ntnxvision_core.api.errors import NutanixApiError


def test_optimization_on_a_demo_snapshot(demo: Any, data_dir: Path) -> None:
    run = sc._m_inventory(None, {
        "host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
        "insecure": False, "name": "opt", "formats": [],
        "export_dir": str(data_dir / "exports")})
    out = sc._m_optimization(None, {"snapshot": run["snapshot_path"]})
    (card,) = out["reviews"]
    assert card["id"] == "vm_compute" and card["count"] > 0
    s = out["vm_compute"]["summary"]
    assert s["high"] >= 3 and s["medium"] >= 3 and s["low"] >= 1
    assert s["clean"] > s["to_optimize"]
    assert s["reviewed"] == s["clean"] + s["to_optimize"]
    rules = {r["rule"] for f in out["vm_compute"]["findings"] for r in f["reasons"]}
    assert {"vcpus_exceed_cores", "numa_span_cpu", "numa_span_memory", "vnuma_not_needed",
            "vnuma_mismatch", "uneven_split", "guest_socket_limit", "smaller_host"} <= rules
    first = out["vm_compute"]["findings"][0]
    assert first["cluster"] and first["host"]["name"] and first["recommended"]["vcpus"]


def test_optimization_refuses_paths_outside_the_snapshots_dir(data_dir: Path) -> None:
    with pytest.raises(NutanixApiError):
        sc._m_optimization(None, {"snapshot": "/etc/passwd"})


def test_optimization_is_registered() -> None:
    assert sc._METHODS["optimization"] is sc._m_optimization
