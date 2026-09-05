"""The v4 cluster path, and the vocabulary normalization it forced.

Both API versions must produce the same `Cluster` for the same cluster: a combined
multi-PC report can merge a PC served by v4 with one served by v3, and any spelling
difference between them shows up as two different values for one fact.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.clusters import ClusterCollector
from ntnxvision_core.collectors.normalize import hypervisor_name
from ntnxvision_core.models import Environment

# Trimmed from a live pc.7.5.1.1 response ($reserved/$objectType noise removed).
V4_CLUSTER = {
    "extId": "0006442f-1fc6-3ee1-0000-000000016d34",
    "name": "lab-cluster-02",
    "nodes": {"numberOfNodes": 1},
    "network": {"externalAddress": {"ipv4": {"value": "198.51.100.232", "prefixLength": 32}}},
    "config": {
        "clusterFunction": ["AOS", "ONE_NODE"],
        "hypervisorTypes": ["ESX"],
        "clusterArch": "X86_64",
        "redundancyFactor": 2,
        "timezone": "Etc/UTC",
        "operationMode": "NORMAL",
        "isLts": False,
        "buildInfo": {"version": "7.5"},
        "clusterSoftwareMap": [
            {"softwareType": "NCC", "version": "ncc-5.3.0.1"},
            {"softwareType": "NOS", "version": "el8.5-release-ganges-7.5-stable-cd8cd937"},
        ],
    },
}

V4_PC_PSEUDO_CLUSTER = {
    "extId": "pc-uuid",
    "name": "lab-pc",
    "nodes": {"numberOfNodes": 1},
    "config": {
        "clusterFunction": ["PRISM_CENTRAL"],
        "clusterSoftwareMap": [{"softwareType": "PRISM_CENTRAL", "version": "7.5.1.1"}],
    },
}


class _Conn:
    def __init__(self, v4: bool, clusters: list[dict[str, Any]] | None = None) -> None:
        self._v4 = v4
        self._clusters = clusters if clusters is not None else [V4_CLUSTER]
        self.negotiated: dict[str, str] = {}
        self.v3_calls = 0

    def v4_ready(self, area: str) -> bool:
        return self._v4

    def v4_list(self, path: str) -> list[dict[str, Any]]:
        return list(self._clusters)

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        self.v3_calls += 1
        return [
            {
                "metadata": {"uuid": V4_CLUSTER["extId"]},
                "status": {"resources": {"config": {"encryption_status": "DISABLED"}}},
            }
        ]


def _collect(conn: _Conn) -> Environment:
    env = Environment()
    ClusterCollector(conn).collect(env)  # type: ignore[arg-type]
    return env


# ─── normalization ─────────────────────────────────────────────────────


def test_v4_esx_normalizes_to_the_v3_spelling() -> None:
    assert hypervisor_name("ESX") == "ESXi"
    assert hypervisor_name("ESXi") == "ESXi"
    assert hypervisor_name("VMware ESXi") == "ESXi"


def test_ahv_spellings() -> None:
    assert hypervisor_name("AHV") == "AHV"
    assert hypervisor_name("Nutanix AHV") == "AHV"
    assert hypervisor_name("Nutanix") == "AHV"
    assert hypervisor_name("KVM") == "AHV"


def test_bare_el_build_string_is_left_alone() -> None:
    """Documents existing behaviour, carried over unchanged from hosts._short.

    The rule is `"el" in value.split()`, i.e. "el" as a whitespace-delimited token — so a
    hyphenated build string does not match and passes through. Real host payloads report
    "Nutanix"/"AHV" rather than a raw build string, so this has not bitten; it is pinned
    here so the v4 work is not blamed if it ever does.
    """
    assert hypervisor_name("el8.5-release-ganges-7.5-stable") == (
        "el8.5-release-ganges-7.5-stable"
    )
    assert hypervisor_name("el 8.5") == "AHV"


def test_unknown_hypervisor_passes_through_and_empty_is_none() -> None:
    assert hypervisor_name("Xen") == "Xen"
    assert hypervisor_name(None) is None
    assert hypervisor_name("") is None


# ─── the v4 mapper ─────────────────────────────────────────────────────


def test_v4_cluster_maps_every_field() -> None:
    env = _collect(_Conn(v4=True))
    assert len(env.clusters) == 1
    c = env.clusters[0]
    assert c.name == "lab-cluster-02"
    assert c.uuid == V4_CLUSTER["extId"]
    assert c.aos_version == "7.5"  # buildInfo, not the long NOS build string
    assert c.ncc_version == "ncc-5.3.0.1"
    assert c.hypervisor_types == ["ESXi"]  # normalized from v4's "ESX"
    assert c.cluster_arch == "X86_64"
    assert c.redundancy_factor == 2
    assert c.num_nodes == 1
    assert c.external_ip == "198.51.100.232"  # unwrapped from the nested IPAddress
    assert c.timezone == "Etc/UTC"
    assert c.operation_mode == "NORMAL"
    assert c.is_lts is False  # v3 leaves this null; v4 reports it
    assert c.api_source == "v4"


def test_encryption_is_backfilled_from_v3_because_v4_omits_it() -> None:
    conn = _Conn(v4=True)
    env = _collect(conn)
    assert env.clusters[0].encryption_enabled is False
    assert conn.v3_calls == 1


def test_encryption_degrades_to_none_when_v3_is_gone() -> None:
    """After the legacy sunset the column blanks; the run must not fail."""

    class _NoV3(_Conn):
        def v3_list(self, kind: str) -> list[dict[str, Any]]:
            from ntnxvision_core.api.errors import NutanixApiError

            raise NutanixApiError("v3 removed", status_code=404)

    env = _collect(_NoV3(v4=True))
    assert env.clusters[0].encryption_enabled is None
    assert env.collection_notes["clusters"] == "ok"


def test_prism_central_pseudo_cluster_is_not_a_physical_cluster() -> None:
    env = _collect(_Conn(v4=True, clusters=[V4_CLUSTER, V4_PC_PSEUDO_CLUSTER]))
    assert [c.name for c in env.clusters] == ["lab-cluster-02"]
    assert env.prism_central.uuid == "pc-uuid"
    assert env.prism_central.version == "7.5.1.1"


def test_v3_path_is_used_when_v4_is_not_served() -> None:
    conn = _Conn(v4=False)
    env = _collect(conn)
    assert conn.v3_calls == 1
    assert env.collection_notes["clusters"] == "ok"
