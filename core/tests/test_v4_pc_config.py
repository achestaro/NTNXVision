"""The v4 Prism Central config path: two endpoints, and the version strings they mix up."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.pc_config import PrismCentralConfigCollector
from ntnxvision_core.models import Environment, Nic, VirtualMachine

FULL_BUILD = (
    "el8.5-release-ganges-7.5.1.1-stable-0000000000000000000000000000000000000000"
)
PCVM_NAME = "NTNX-198-51-100-4-PCVM-1700000000"
PCVM_UUID = "000055b2-3402-4aec-a4d3-8c0da8d706ab"

# Trimmed from a live pc.7.5.1.1 `prism/v4.0/config/domain-managers` response.
DOMAIN_MANAGER: dict[str, Any] = {
    "extId": "0000b6a9-8ca6-44e0-a8d4-d8d661d90f4c",
    "isRegisteredWithHostingCluster": True,
    "nodeExtIds": [PCVM_UUID],
    "config": {
        # Not the build: the short marketing string, and "pc."-prefixed at that.
        "buildInfo": {"version": "pc.7.5.1.1"},
        "name": "lab-pc",
        "size": "EXTRALARGE",
        "resourceConfig": {
            "numVcpus": 18,
            "memorySizeBytes": 83751862272,
            "dataDiskSizeBytes": 2684354560000,
        },
    },
    "network": {
        "fqdn": "prism.example.lab",
        "externalNetworks": [{"ipAddresses": [{"ipv4": {"value": "198.51.100.4"}}]}],
    },
    "hostingClusterExtId": "0000d596-d86d-45b7-adae-2662c463d891",
}

# Trimmed from the same PC's own entry in `clustermgmt/v4.0/config/clusters`.
PC_CLUSTER: dict[str, Any] = {
    "extId": "0000b6a9-8ca6-44e0-a8d4-d8d661d90f4c",
    "name": "lab-pc",
    "nodes": {
        "numberOfNodes": 1,
        "nodeList": [
            {
                "nodeUuid": "0000e8e3-0063-479f-a57d-0df955a20771",
                "controllerVmIp": {"ipv4": {"value": "198.51.100.4", "prefixLength": 32}},
            }
        ],
    },
    "config": {
        "clusterFunction": ["PRISM_CENTRAL"],
        "clusterSoftwareMap": [
            {"softwareType": "NCC", "version": "ncc-5.3.1.1"},
            {"softwareType": "PRISM_CENTRAL", "version": FULL_BUILD},
        ],
        "buildInfo": {"buildType": "release", "version": "7.5", "fullVersion": FULL_BUILD},
        "isLts": False,
    },
    # A single-node PC has no VIP, and v4 renders the absence as an empty object.
    "network": {"externalAddress": {}, "externalSubnet": "198.51.100.0/255.255.255.0"},
}

AOS_CLUSTER: dict[str, Any] = {
    "extId": "0000d596-d86d-45b7-adae-2662c463d891",
    "name": "lab-cluster-01",
    "config": {"clusterFunction": ["AOS", "ONE_NODE"]},
}

# What the v1 gateway returns for this same PC: no IPs at all, and no sizing.
V1_GATEWAY: dict[str, Any] = {
    "numNodes": 1,
    "fullVersion": FULL_BUILD,
    "nccVersion": "ncc-5.3.1.1",
    "clusterExternalIPAddress": None,
}


class _Conn:
    """Serves the two v4 lists plus the v1 gateway; any of them can be made to 404."""

    def __init__(
        self,
        v4: bool = True,
        manager: dict[str, Any] | None = DOMAIN_MANAGER,
        clusters: tuple[dict[str, Any], ...] | None = (AOS_CLUSTER, PC_CLUSTER),
    ) -> None:
        self._v4 = v4
        # None means the endpoint 404s, as it does on a PC that does not route it.
        self._manager = manager
        self._clusters = clusters
        self.creds = type("C", (), {"base": "https://pc.example:9440"})()
        self.paths: list[str] = []

    def v4_ready(self, area: str) -> bool:
        return self._v4

    def v4_list(self, path: str) -> list[dict[str, Any]]:
        self.paths.append(path)
        if path.startswith("prism/"):
            if self._manager is None:
                raise NutanixApiError("Not found", status_code=404)
            return [dict(self._manager)]
        if self._clusters is None:
            raise NutanixApiError("Not found", status_code=404)
        return [dict(c) for c in self._clusters]

    def get_json(self, url: str) -> dict[str, Any]:
        return dict(V1_GATEWAY)


def _env(with_pcvm: bool = True) -> Environment:
    env = Environment()
    env.prism_central.version = FULL_BUILD
    if with_pcvm:
        env.system_vms.append(
            VirtualMachine(
                uuid=PCVM_UUID,
                name=PCVM_NAME,
                vm_type="PRISM_CENTRAL",
                num_vcpus=18,
                num_cores_per_vcpu=1,
                memory_bytes=83751862272,
                nics=[Nic(ip_addresses=["198.51.100.4"])],
            )
        )
    return env


def _collect(conn: _Conn, with_pcvm: bool = True) -> Environment:
    env = _env(with_pcvm)
    PrismCentralConfigCollector(conn).collect(env)  # type: ignore[arg-type]
    return env


def test_v4_path_fills_every_field_the_v1_gateway_did() -> None:
    conn = _Conn()
    cfg = _collect(conn).pc_config
    assert cfg.node_count == 1
    assert cfg.is_scale_out is False
    assert cfg.build == FULL_BUILD
    assert cfg.ncc_version == "ncc-5.3.1.1"
    assert cfg.num_vcpus == 18
    assert cfg.memory_bytes == 83751862272
    assert cfg.pc_vm_names == [PCVM_NAME]
    assert "prism/v4.0/config/domain-managers" in conn.paths
    assert "clustermgmt/v4.0/config/clusters" in conn.paths


def test_build_is_the_full_version_not_either_short_one() -> None:
    """The trap: three fields called some flavour of "version" and only one is the build.

    `buildInfo.version` is "7.5" on the cluster entry and "pc.7.5.1.1" on the domain
    manager; only `buildInfo.fullVersion` matches what v1 reported as `fullVersion`.
    """
    cfg = _collect(_Conn()).pc_config
    assert cfg.build == FULL_BUILD
    assert cfg.build != "pc.7.5.1.1"
    assert cfg.build != "7.5"


def test_ips_come_from_v4_where_the_v1_gateway_reported_none() -> None:
    """A single-node PC has no VIP, so v1 returned nothing and the PCVM NICs stood in.

    v4 carries the node's own controller address, so the field is populated from the
    management plane's own record even when no PC VM was classified.
    """
    assert _collect(_Conn(), with_pcvm=False).pc_config.ip_addresses == ["198.51.100.4"]


def test_absent_vip_is_an_empty_object_not_an_address() -> None:
    """v4 pads the missing VIP with `{}`; it must not become a blank IP entry."""
    ips = _collect(_Conn()).pc_config.ip_addresses
    assert ips == ["198.51.100.4"]
    assert all(ips)


def test_cluster_vip_leads_the_node_addresses_as_it_did_on_v1() -> None:
    scaled = {
        **PC_CLUSTER,
        "nodes": {
            "numberOfNodes": 3,
            "nodeList": [
                {"controllerVmIp": {"ipv4": {"value": "198.51.100.5"}}},
                {"controllerVmIp": {"ipv4": {"value": "198.51.100.6"}}},
                {"controllerVmIp": {"ipv4": {"value": "198.51.100.7"}}},
            ],
        },
        "network": {"externalAddress": {"ipv4": {"value": "198.51.100.9"}}},
    }
    cfg = _collect(_Conn(manager=None, clusters=(scaled,))).pc_config
    assert cfg.ip_addresses[0] == "198.51.100.9"
    assert cfg.ip_addresses == ["198.51.100.9", "198.51.100.5", "198.51.100.6", "198.51.100.7"]
    assert cfg.node_count == 3
    assert cfg.is_scale_out is True


def test_measured_pcvm_totals_win_over_the_declared_size() -> None:
    """`resourceConfig` states one domain manager's configured size.

    The report totals every PCVM, so the summed VMs stay authoritative — the declared
    figure must not silently replace an aggregate with a per-manager one.
    """
    env = _env()
    env.system_vms[0].num_vcpus = 24
    env.system_vms[0].memory_bytes = 99999999999
    PrismCentralConfigCollector(_Conn()).collect(env)  # type: ignore[arg-type]
    assert env.pc_config.num_vcpus == 24
    assert env.pc_config.memory_bytes == 99999999999


def test_declared_size_fills_in_when_no_pcvm_was_classified() -> None:
    """The gain: a PC whose PCVMs are not in its own inventory used to size as blank."""
    cfg = _collect(_Conn(), with_pcvm=False).pc_config
    assert cfg.num_vcpus == 18
    assert cfg.memory_bytes == 83751862272
    assert cfg.pc_vm_names == []


def test_missing_domain_manager_still_yields_the_cluster_half() -> None:
    """`prism` is not a negotiated namespace, so its absence must degrade, not fail."""
    cfg = _collect(_Conn(manager=None)).pc_config
    assert cfg.build == FULL_BUILD
    assert cfg.ncc_version == "ncc-5.3.1.1"
    assert cfg.node_count == 1
    assert cfg.ip_addresses == ["198.51.100.4"]
    # Sizing is back to the summed PC VMs, which is where it came from before v4.
    assert cfg.num_vcpus == 18


def test_domain_manager_alone_still_reports_nodes_and_sizing() -> None:
    cfg = _collect(_Conn(clusters=None), with_pcvm=False).pc_config
    assert cfg.node_count == 1  # from nodeExtIds
    assert cfg.ip_addresses == ["198.51.100.4"]
    assert cfg.num_vcpus == 18
    assert cfg.build is None  # only the cluster entry carries it
    assert cfg.ncc_version is None


def test_both_v4_endpoints_absent_falls_back_to_the_v1_gateway() -> None:
    cfg = _collect(_Conn(manager=None, clusters=None)).pc_config
    assert cfg.build == FULL_BUILD
    assert cfg.ncc_version == "ncc-5.3.1.1"
    assert cfg.node_count == 1
    # v1 has no IPs for a single-node PC, so the PCVM NIC fallback still runs.
    assert cfg.ip_addresses == ["198.51.100.4"]


def test_legacy_path_is_used_when_v4_is_not_served() -> None:
    conn = _Conn(v4=False)
    env = _collect(conn)
    assert conn.paths == []
    assert env.pc_config.build == FULL_BUILD
    assert env.collection_notes["pc_config"] == "ok"
