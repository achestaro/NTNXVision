"""v4 capability negotiation.

Covers the routing decision every collector will depend on as the v4 migration proceeds:
v4 when the PC genuinely serves it, legacy otherwise, and never an aborted run because a
probe failed.
"""

from __future__ import annotations

from typing import Any

import pytest

from ntnxvision_core.api import v4_client
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.licenses import LicenseCollector
from ntnxvision_core.models import Environment


class _Conn:
    """Minimal PrismConnection stand-in: records probes, replays a canned outcome."""

    def __init__(self, pc_version: str | None, outcome: Any = None) -> None:
        self.pc_version = pc_version
        self.negotiated: dict[str, str] = {}
        self.outcome = outcome
        self.probes: list[str] = []
        self.lists: list[str] = []

    def v4_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.probes.append(path)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return {"data": []}

    def v4_list(self, path: str) -> list[dict[str, Any]]:
        self.lists.append(path)
        return list(self.outcome or []) if not isinstance(self.outcome, Exception) else []

    def v4_ready(self, area: str) -> bool:
        return v4_client.negotiate(self, area) == v4_client.AVAILABLE  # type: ignore[arg-type]


# ─── version gate ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "version,expected",
    [
        ("pc.2024.1", False),  # supported floor, predates v4 GA
        ("pc.2024.2", False),
        ("pc.2024.3", True),  # v4 GA
        ("pc.2024.5", True),
        ("7.0", True),
        ("pc.7.5.1.1", True),  # the lab PC
        ("7.5.1.6", True),
        (None, False),
        ("garbage", False),
    ],
)
def test_v4_ga_line(version: str | None, expected: bool) -> None:
    assert v4_client.v4_is_ga(version) is expected


def test_below_ga_is_not_probed() -> None:
    """A pc.2024.1 target must not spend a round-trip per namespace."""
    conn = _Conn("pc.2024.1")
    assert conn.v4_ready("licensing") is False
    assert conn.probes == []
    assert conn.negotiated["licensing"] == v4_client.BELOW_GA


# ─── probe outcomes ────────────────────────────────────────────────────


def test_available_namespace() -> None:
    conn = _Conn("pc.7.5.1.1")
    assert conn.v4_ready("clusters") is True
    assert conn.negotiated["clusters"] == v4_client.AVAILABLE


def test_404_is_recorded_as_absent() -> None:
    conn = _Conn("pc.7.5.1.1", NutanixApiError("Not found", status_code=404))
    assert conn.v4_ready("dataprotection") is False
    assert conn.negotiated["dataprotection"] == v4_client.ABSENT


def test_503_is_not_mistaken_for_an_absent_namespace() -> None:
    """Objects answers 503 on the lab PC — deployed but not running, not missing."""
    conn = _Conn("pc.7.5.1.1", NutanixApiError("HTTP 503", status_code=503))
    assert conn.v4_ready("objects") is False
    assert conn.negotiated["objects"] == v4_client.UNAVAILABLE


def test_unexpected_probe_error_never_escapes() -> None:
    conn = _Conn("pc.7.5.1.1", RuntimeError("socket exploded"))
    assert conn.v4_ready("vms") is False
    assert conn.negotiated["vms"] == v4_client.UNAVAILABLE


def test_verdict_is_cached_so_each_namespace_costs_one_probe() -> None:
    conn = _Conn("pc.7.5.1.1")
    for _ in range(5):
        conn.v4_ready("vms")
    assert len(conn.probes) == 1


def test_unknown_area_is_a_programming_error() -> None:
    with pytest.raises(KeyError):
        v4_client.negotiate(_Conn("7.5"), "not-a-real-area")  # type: ignore[arg-type]


# ─── namespace map: paths recorded from a live PC ──────────────────────


def test_storage_containers_are_served_by_clustermgmt() -> None:
    """Not the `storage` namespace its SDK package name implies."""
    assert NS("storage_containers").startswith("clustermgmt/")


def test_protection_policies_are_served_by_datapolicies() -> None:
    """Not `dataprotection`, which 404s."""
    assert NS("dataprotection").startswith("datapolicies/")


def NS(area: str) -> str:
    return v4_client.NAMESPACES[area].probe_path


# ─── the licensing collector, first migrated to the gated path ─────────


def test_licensing_skips_cleanly_below_ga() -> None:
    env = Environment()
    conn = _Conn("pc.2024.1")
    LicenseCollector(conn).collect(env)  # type: ignore[arg-type]
    assert env.licenses == []
    assert "not served by this Prism Central" in env.collection_notes["licenses"]
    assert conn.lists == []  # never attempted the real call


def test_empty_license_list_is_reported_as_a_real_answer() -> None:
    """A PC with no licences returns 200 + []; that is not a missing API."""
    env = Environment()
    conn = _Conn("pc.7.5.1.1", [])
    LicenseCollector(conn).collect(env)  # type: ignore[arg-type]
    note = env.collection_notes["licenses"]
    assert note.startswith("ok")
    assert "not available" not in note


def test_licenses_are_collected_when_v4_serves_them() -> None:
    env = Environment()
    conn = _Conn(
        "pc.7.5.1.1",
        [{"name": "AOS Ultimate", "category": "AOS", "type": "Ultimate", "quantity": 4}],
    )
    LicenseCollector(conn).collect(env)  # type: ignore[arg-type]
    assert len(env.licenses) == 1
    assert env.licenses[0].product == "AOS"
    assert env.collection_notes["licenses"] == "ok"
