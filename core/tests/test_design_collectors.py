"""The design-data collectors (architecture diagrams): v4 paths, fallbacks, isolation.

Payloads are trimmed from a live pc.7.5.1.1 and re-labelled with example values
(``example.lab``, 192.0.2.x / 198.51.100.x, documentation-range MACs).
"""

from __future__ import annotations

from typing import Any

import pytest

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.clusters import ClusterCollector
from ntnxvision_core.collectors.dataprotection import DataProtectionCollector
from ntnxvision_core.collectors.hostnet import HostNetworkCollector
from ntnxvision_core.collectors.hosts import HostCollector
from ntnxvision_core.collectors.nus import FilesCollector, ObjectsCollector
from ntnxvision_core.collectors.pc_config import PrismCentralConfigCollector
from ntnxvision_core.collectors.storage import StorageCollector
from ntnxvision_core.designdata import resolve_design_data
from ntnxvision_core.models import (
    Cluster,
    Environment,
    Host,
    StorageContainer,
    VirtualMachine,
    external_storage_product,
    pc_size,
    replication_label,
    replication_type_for_rpo,
)
from ntnxvision_core.scope import RegisteredCluster, Scope

CL_AHV = "0000aaaa-0000-0000-0000-000000000001"
CL_ESX = "0000aaaa-0000-0000-0000-000000000002"
HOST_AHV = "1111aaaa-0000-0000-0000-000000000001"
HOST_ESX = "1111aaaa-0000-0000-0000-000000000002"
PC = "2222aaaa-0000-0000-0000-000000000001"
PC_REMOTE = "2222aaaa-0000-0000-0000-000000000002"
VS0 = "3333aaaa-0000-0000-0000-000000000001"


class FakeConn:
    """A connection whose v4 / v3 / PE answers are given per path.

    A value that is an exception is raised; a missing path is a 404.
    """

    def __init__(self, v4: dict[str, Any] | None = None, *, ready: bool = True,
                 groups: dict[str, Any] | None = None, gets: dict[str, Any] | None = None,
                 pe: dict[str, Any] | None = None, v3: dict[str, Any] | None = None,
                 pc_version: str = "pc.7.5.1.1", negotiated: dict[str, str] | None = None,
                 ) -> None:
        self.v4 = v4 or {}
        self.ready = ready
        self.groups = groups or {}
        self.gets = gets or {}
        self.pe = pe or {}
        self.v3 = v3 or {}
        self.pc_version = pc_version
        self.negotiated = negotiated or {}
        self.scope: Any = None
        self.calls: list[str] = []
        self.creds = type("C", (), {"base": "https://pc.example.lab:9440"})()

    @staticmethod
    def _answer(table: dict[str, Any], key: str) -> Any:
        if key not in table:
            raise NutanixApiError(f"Not found: {key}", status_code=404)
        value = table[key]
        if isinstance(value, Exception):
            raise value
        return value

    def v4_ready(self, area: str) -> bool:
        return self.ready

    def v4_list(self, path: str, **_kw: Any) -> list[dict[str, Any]]:
        self.calls.append(path)
        return list(self._answer(self.v4, path))

    def v4_get(self, path: str, params: Any = None) -> dict[str, Any]:
        self.calls.append(f"probe:{path}")
        return {"data": list(self._answer(self.v4, path))[:1]}

    def v3_list(self, kind: str, **_kw: Any) -> list[dict[str, Any]]:
        return list(self._answer(self.v3, kind))

    def v3_groups(self, entity_type: str, attrs: list[str], **_kw: Any) -> list[dict[str, Any]]:
        return list(self._answer(self.groups, entity_type))

    def get_json(self, url: str, params: Any = None) -> Any:
        return self._answer(self.gets, url.split(":9440", 1)[-1])

    def pe_get(self, cluster_uuid: str, path: str, params: Any = None) -> Any:
        return self._answer(self.pe, f"{cluster_uuid}:{path}")

    def pe_v2_list(self, cluster_uuid: str, path: str, **_kw: Any) -> list[dict[str, Any]]:
        return list(self._answer(self.pe, f"{cluster_uuid}:{path}"))


def _env_with_clusters() -> Environment:
    env = Environment()
    env.clusters = [Cluster(uuid=CL_AHV, name="ahv-01", hypervisor_types=["AHV"]),
                    Cluster(uuid=CL_ESX, name="esx-01", hypervisor_types=["ESXi"])]
    env.hosts = [Host(uuid=HOST_AHV, name="ahv-01-n1", cluster_uuid=CL_AHV,
                      cluster_name="ahv-01"),
                 Host(uuid=HOST_ESX, name="esx-01-n1", cluster_uuid=CL_ESX,
                      cluster_name="esx-01")]
    return env


# ─── the replication type rule ─────────────────────────────────────────


@pytest.mark.parametrize(("rpo", "kind"), [
    (None, None), (0, "SYNC"), (1, "NEARSYNC"), (60, "NEARSYNC"), (900, "NEARSYNC"),
    (901, "ASYNC"), (3600, "ASYNC"), (86400, "ASYNC"), ("3600", "ASYNC"), ("x", None),
    (True, None),
])
def test_replication_type_follows_the_rpo(rpo: Any, kind: str | None) -> None:
    assert replication_type_for_rpo(rpo) == kind


def test_replication_labels() -> None:
    assert [replication_label(k) for k in ("SYNC", "NEARSYNC", "ASYNC", None)] == [
        "Sync", "NearSync", "Async", None]


def test_external_storage_products_and_pc_sizes() -> None:
    assert external_storage_product("EVERPURE_FLASHARRAY") == "Pure FlashArray"
    assert external_storage_product("PURE_STORAGE_FLASHARRAY") == "Pure FlashArray"
    assert external_storage_product("DELL_POWERFLEX") == "Dell PowerFlex"
    assert external_storage_product("DELL_POWERSTORE") == "Dell PowerStore"
    assert external_storage_product("NETAPP_ONTAP_NVME") == "Netapp Ontap Nvme"
    assert external_storage_product(None) is None
    assert [pc_size(v) for v in ("kXLarge", "kSmall", "LARGE", "$UNKNOWN", None)] == [
        "EXTRALARGE", "SMALL", "LARGE", None, None]


# ─── old snapshots ─────────────────────────────────────────────────────


def test_a_snapshot_without_the_design_fields_still_loads() -> None:
    old = {
        "schema_version": 1, "collected_at": "2026-09-01T00:00:00+00:00",
        "clusters": [{"uuid": CL_AHV, "name": "ahv-01", "hypervisor_types": ["AHV"]}],
        "hosts": [{"uuid": HOST_AHV, "name": "n1", "cluster_uuid": CL_AHV}],
        "storage_containers": [{"uuid": "c1", "name": "ctr"}],
        "protection_policies": [{"uuid": "p1", "name": "pp", "rpo_seconds": 3600,
                                 "replication_type": "ASYNC"}],
        "recovery_plans": [{"uuid": "r1", "name": "rp"}],
        "pc_config": {"version": "pc.2024.3", "node_count": 1},
        "source_environments": [{"prism_central": {"host": "pc.example.lab"},
                                 "pc_config": {"node_count": 3}}],
    }
    env = Environment.model_validate(old)
    assert env.blocks == [] and env.host_nics == [] and env.virtual_switches == []
    assert env.external_storages == [] and env.pc_vms == [] and env.pc_registrations == []
    assert env.clusters[0].fault_domain is None and env.clusters[0].categories == {}
    assert env.hosts[0].block_position is None
    assert env.storage_containers[0].external_storage_uuid is None
    assert env.protection_policies[0].locations == []
    assert env.pc_config.products == [] and env.pc_config.size is None
    assert env.source_environments[0].pc_config.services == []
    summary = env.summary()
    assert summary["host_nics"] == 0 and summary["pc_vms"] == 0
    # RPO 0 written as NEARSYNC by the old v4 path reads back as SYNC.
    stale = Environment.model_validate({"protection_policies": [
        {"uuid": "p0", "rpo_seconds": 0, "replication_type": "NEARSYNC"},
        {"uuid": "p1", "rpo_seconds": 60, "replication_type": "NEARSYNC"}]})
    assert [p.replication_type for p in stale.protection_policies] == ["SYNC", "NEARSYNC"]
    # And it round-trips with the new fields present.
    again = Environment.model_validate_json(env.model_dump_json())
    assert again.summary() == summary


# ─── clusters ──────────────────────────────────────────────────────────


V4_CLUSTER = {
    "extId": CL_AHV, "name": "ahv-01",
    "nodes": {"numberOfNodes": 1},
    "config": {
        "clusterFunction": ["AOS", "ONE_NODE"], "clusterType": "HYPER_CONVERGED",
        "hypervisorTypes": ["AHV"], "buildInfo": {"version": "7.5"},
        "faultToleranceState": {"currentClusterFaultTolerance": "CFT_1N_OR_1D",
                                "currentMaxFaultTolerance": 1,
                                "desiredMaxFaultTolerance": 1,
                                "domainAwarenessLevel": "DISK"},
    },
    "network": {},
}


def test_v4_cluster_design_fields_and_v3_categories() -> None:
    v3 = [{"metadata": {"uuid": CL_AHV, "categories_mapping": {"POD": ["POD-1"]}},
           "status": {"resources": {"config": {"encryption_status": "DISABLED"}}}}]
    conn = FakeConn({"clustermgmt/v4.0/config/clusters": [V4_CLUSTER]}, v3={"clusters": v3})
    env = Environment()
    ClusterCollector(conn).collect(env)  # type: ignore[arg-type]
    c = env.clusters[0]
    assert (c.cluster_type, c.fault_domain, c.fault_tolerance, c.desired_fault_tolerance) == (
        "HYPER_CONVERGED", "DISK", 1, 1)
    assert c.cluster_functions == ["AOS", "ONE_NODE"]
    assert c.categories == {"POD": ["POD-1"]}
    assert not c.is_compute_only


def test_fault_tolerance_from_the_cluster_fault_tolerance_enums() -> None:
    cluster = {**V4_CLUSTER, "config": {**V4_CLUSTER["config"], "faultToleranceState": {
        "domainAwarenessLevel": "BLOCK", "currentClusterFaultTolerance": "CFT_2N_OR_2D",
        "desiredClusterFaultTolerance": "CFT_2N_OR_2D"}}}
    conn = FakeConn({"clustermgmt/v4.0/config/clusters": [cluster]}, v3={"clusters": []})
    env = Environment()
    ClusterCollector(conn).collect(env)  # type: ignore[arg-type]
    c = env.clusters[0]
    assert (c.fault_domain, c.fault_tolerance, c.desired_fault_tolerance) == ("BLOCK", 2, 2)


def test_v3_cluster_reads_the_fault_domain_from_prism_element() -> None:
    v3 = [{"metadata": {"uuid": CL_AHV, "categories": {"POD": "POD-2"}},
           "status": {"name": "ahv-01", "resources": {"config": {}, "network": {}}}}]
    pe = {f"{CL_AHV}:cluster": {"fault_tolerance_domain_type": "RACKABLE_UNIT",
                                "cluster_redundancy_state": {
                                    "current_redundancy_factor": 3,
                                    "desired_redundancy_factor": 3}}}
    conn = FakeConn(ready=False, v3={"clusters": v3}, pe=pe)
    env = Environment()
    ClusterCollector(conn).collect(env)  # type: ignore[arg-type]
    c = env.clusters[0]
    assert (c.fault_domain, c.fault_tolerance, c.desired_fault_tolerance) == ("BLOCK", 2, 2)
    assert c.categories == {"POD": ["POD-2"]}


def test_v3_cluster_fault_domain_is_best_effort() -> None:
    v3 = [{"metadata": {"uuid": CL_AHV},
           "status": {"name": "ahv-01", "resources": {"config": {}, "network": {}}}}]
    conn = FakeConn(ready=False, v3={"clusters": v3},
                    pe={f"{CL_AHV}:cluster": NutanixApiError("x", status_code=403)})
    env = Environment()
    ClusterCollector(conn).collect(env)  # type: ignore[arg-type]
    assert env.clusters[0].fault_domain is None
    assert env.collection_notes["clusters"] == "ok"


# ─── hosts: blocks and CVMs ────────────────────────────────────────────

V4_HOST = {"extId": HOST_AHV, "hostName": "ahv-01-n1", "hostType": "HYPER_CONVERGED",
           "rackableUnitUuid": "4444aaaa-0000-0000-0000-000000000001",
           "cluster": {"uuid": CL_AHV, "name": "ahv-01"}, "hypervisor": {"type": "AHV"},
           "blockModel": "NX-3060-G4", "disk": [{"sizeInBytes": 10}]}
RACKABLE_UNIT = {"extId": "4444aaaa-0000-0000-0000-000000000001", "id": 10,
                 "model": "USELAYOUT", "modelName": "NX-3060-G4", "serial": "EXAMPLE-SN-1",
                 "nodes": [{"position": 2, "svmId": 2, "uuid": HOST_AHV}],
                 "rack": {"uuid": "5555aaaa-0000-0000-0000-000000000001", "id": 3}}
CVM = {"extId": "6666aaaa-0000-0000-0000-000000000001", "name": "NTNX-n1-CVM",
       "clusterExtId": CL_AHV, "nodeExtId": HOST_AHV, "numVcpus": 8,
       "memorySizeBytes": 64 * 1024**3, "hypervisorType": "AHV",
       "ipAddress": {"ipv4": {"value": "192.0.2.21"}}}


def _host_conn(**extra: Any) -> FakeConn:
    v4 = {"clustermgmt/v4.0/config/hosts": [V4_HOST],
          f"clustermgmt/v4.0/config/clusters/{CL_AHV}/rackable-units": [RACKABLE_UNIT],
          f"clustermgmt/v4.0/config/clusters/{CL_AHV}/cvms": [CVM]}
    v4.update(extra)
    return FakeConn(v4, groups={"host": []})


def test_blocks_and_cvms_come_per_cluster_and_land_on_the_hosts() -> None:
    env = Environment(clusters=[Cluster(uuid=CL_AHV, name="ahv-01")])
    HostCollector(_host_conn()).collect(env)  # type: ignore[arg-type]
    resolve_design_data(env)
    block, cvm, host = env.blocks[0], env.cvms[0], env.hosts[0]
    assert (block.model_name, block.rack_id, block.serial) == ("NX-3060-G4", 3, "EXAMPLE-SN-1")
    assert (cvm.num_vcpus, cvm.memory_bytes, cvm.ip_address, cvm.host_name) == (
        8, 64 * 1024**3, "192.0.2.21", "ahv-01-n1")
    assert (host.host_type, host.block_position, host.rack_id) == ("HYPER_CONVERGED", 2, 3)
    assert env.collection_notes["hosts"] == "ok"


def test_a_cluster_whose_cvms_fail_is_named_and_the_hosts_are_kept() -> None:
    env = Environment(clusters=[Cluster(uuid=CL_AHV, name="ahv-01")])
    conn = _host_conn(**{f"clustermgmt/v4.0/config/clusters/{CL_AHV}/cvms":
                         NutanixApiError("boom", status_code=500)})
    HostCollector(conn).collect(env)  # type: ignore[arg-type]
    assert len(env.hosts) == 1
    # The blocks were still read: each sub-resource fails on its own.
    assert len(env.blocks) == 1 and env.cvms == []
    assert env.collection_notes["hosts"] == "ok: CVMs of ahv-01 unavailable (HTTP 500)"


def test_a_cluster_without_v4_cvms_gets_them_from_the_system_vms() -> None:
    env = Environment(clusters=[Cluster(uuid=CL_AHV, name="ahv-01"),
                                Cluster(uuid=CL_ESX, name="esx-01")])
    HostCollector(_host_conn()).collect(env)  # type: ignore[arg-type]
    env.system_vms = [
        VirtualMachine(uuid="cvm-a", name="NTNX-a-CVM", vm_type="CVM", cluster_uuid=CL_AHV,
                       cluster_name="ahv-01"),
        VirtualMachine(uuid="cvm-e", name="NTNX-e-CVM", vm_type="CVM", cluster_uuid=CL_ESX,
                       cluster_name="esx-01", num_vcpus=8, api_source="v4")]
    resolve_design_data(env)
    # ahv-01 keeps its v4 CVM; esx-01, which v4 gave nothing for, gets its system VM.
    assert sorted((c.uuid, c.api_source) for c in env.cvms) == [
        ("6666aaaa-0000-0000-0000-000000000001", "v4"), ("cvm-e", "v4")]


def test_per_cluster_calls_honour_the_cluster_scope() -> None:
    env = Environment()
    conn = _host_conn()
    conn.scope = Scope([RegisteredCluster(CL_AHV, "ahv-01", hypervisors=["AHV"]),
                        RegisteredCluster(CL_ESX, "esx-01", hypervisors=["ESXi"])], [CL_AHV])
    HostCollector(conn).collect(env)  # type: ignore[arg-type]
    assert not any(CL_ESX in c for c in conn.calls)
    assert any(f"{CL_AHV}/cvms" in c for c in conn.calls)


def test_below_v4_blocks_come_from_prism_element_and_cvms_from_system_vms() -> None:
    v3_hosts = [{"metadata": {"uuid": HOST_AHV}, "status": {
        "name": "ahv-01-n1", "cluster_reference": {"uuid": CL_AHV, "name": "ahv-01"},
        "resources": {"cpu_model": "x", "host_type": "HYPER_CONVERGED"}}}]
    pe_hosts = [{"uuid": HOST_AHV, "block_serial": "EXAMPLE-SN-2",
                 "block_model_name": "NX-1065-G8", "position": {"ordinal": 4}}]
    conn = FakeConn(ready=False, v3={"hosts": v3_hosts}, groups={"host": []},
                    pe={f"{CL_AHV}:hosts": pe_hosts})
    env = Environment(clusters=[Cluster(uuid=CL_AHV, name="ahv-01")])
    HostCollector(conn).collect(env)  # type: ignore[arg-type]
    env.system_vms = [VirtualMachine(uuid="cvm-1", name="NTNX-x-CVM", vm_type="CVM",
                                     num_vcpus=12, memory_bytes=48 * 1024**3,
                                     host_name="ahv-01-n1", cluster_name="ahv-01",
                                     hypervisor="kKvm", api_source="v3-groups")]
    resolve_design_data(env)
    assert env.blocks[0].model_name == "NX-1065-G8" and env.blocks[0].api_source == "v2.0"
    assert env.hosts[0].block_position == 4
    cvm = env.cvms[0]
    assert (cvm.num_vcpus, cvm.host_uuid, cvm.cluster_uuid, cvm.hypervisor_type) == (
        12, HOST_AHV, CL_AHV, "AHV")


# ─── host networking ───────────────────────────────────────────────────

V4_SWITCH = {
    "extId": VS0, "name": "vs0", "isDefault": True, "mtu": 1500,
    "bondMode": "ACTIVE_BACKUP", "ownerType": "PE", "description": "Default Virtual Switch",
    "clusters": [{"extId": CL_AHV, "hosts": [
        {"extId": HOST_AHV, "hostNics": ["eth0", "eth1"], "internalBridgeName": "br0",
         "routeTable": 1000}]}],
}
V4_BOND = {"extId": "b1", "name": "br0-up", "type": "ACTIVE_BACKUP", "lacpStatus": "NIL",
           "hostReference": HOST_AHV, "clusterReference": CL_AHV,
           "virtualSwitchInfo": {"name": "vs0", "reference": VS0}, "hostNicReferences": []}
V4_NIC = {"extId": "n1", "name": "eth0", "nodeUuid": HOST_AHV, "clusterExtId": CL_AHV,
          "hostDescription": "Intel Corporation I350 Gigabit Network Connection",
          "pciModelId": "8086:1521", "driverVersion": "igb:5.19.3",
          "firmwareVersion": "1.63", "interfaceStatus": "0", "linkSpeedInKbps": 0,
          "linkCapacityInMbps": 1000, "mtuInBytes": 1500, "macAddress": "02:00:00:00:00:01",
          "virtualSwitchExtId": VS0, "discoveryProtocol": "LLDP",
          "switchVendorInfo": "Example 24-Port Switch",
          "switchMacAddress": "00:00:5e:00:53:01"}
V4_ESX_NIC = {"extId": "n2", "name": "vmnic0", "nodeUuid": HOST_ESX,
              "linkSpeedInKbps": 10_000_000, "macAddress": "02:00:00:00:00:02"}


def _net_conn(**extra: Any) -> FakeConn:
    v4 = {"networking/v4.0/config/virtual-switches": [V4_SWITCH],
          "networking/v4.0/config/uplink-bonds": [V4_BOND],
          "clustermgmt/v4.0/config/host-nics": [V4_NIC, V4_ESX_NIC]}
    v4.update(extra)
    return FakeConn(v4)


def test_virtual_switch_bond_and_nics_from_v4() -> None:
    env = _env_with_clusters()
    HostNetworkCollector(_net_conn()).collect(env)  # type: ignore[arg-type]
    resolve_design_data(env)
    sw = env.virtual_switches[0]
    assert (sw.name, sw.bond_mode, sw.mtu, sw.cluster_name, sw.uplink_count) == (
        "vs0", "ACTIVE_BACKUP", 1500, "ahv-01", 2)
    member = sw.hosts[0]
    assert (member.bridge, member.uplinks, member.bond_name, member.lacp_status,
            member.host_name) == ("br0", ["eth0", "eth1"], "br0-up", "NIL", "ahv-01-n1")
    assert not sw.lacp
    nics = {n.name: n for n in env.host_nics}
    eth0 = nics["eth0"]
    assert (eth0.link_up, eth0.speed_mbps, eth0.virtual_switch_name, eth0.switch_vendor,
            eth0.switch_mac, eth0.switch_port_id) == (
        False, 1000, "vs0", "Example 24-Port Switch", "00:00:5e:00:53:01", None)
    assert eth0.has_neighbour
    # ESXi: name and speed only, placed on its cluster through its host.
    esx = nics["vmnic0"]
    assert (esx.cluster_name, esx.speed_mbps, esx.model, esx.link_up, esx.has_neighbour) == (
        "esx-01", 10_000, None, None, False)
    assert env.collection_notes["host_networking"] == "ok"


def test_bond_failure_keeps_the_switches_and_nics() -> None:
    conn = _net_conn(**{"networking/v4.0/config/uplink-bonds":
                        NutanixApiError("x", status_code=500)})
    env = _env_with_clusters()
    HostNetworkCollector(conn).collect(env)  # type: ignore[arg-type]
    assert env.virtual_switches and env.host_nics
    assert env.virtual_switches[0].hosts[0].bond_name is None
    assert env.collection_notes["host_networking"] == "ok: uplink bonds unavailable (HTTP 500)"


def test_scoped_run_drops_nics_whose_cluster_is_unknown() -> None:
    conn = _net_conn()
    conn.scope = Scope([RegisteredCluster(CL_AHV, "ahv-01"),
                        RegisteredCluster(CL_ESX, "esx-01")], [CL_AHV])
    env = Environment()  # hosts not collected: the ESXi NIC cannot be placed
    HostNetworkCollector(conn).collect(env)  # type: ignore[arg-type]
    assert [n.name for n in env.host_nics] == ["eth0"]
    # No server-side filter on host NICs (ESXi NICs carry no cluster id).
    assert "clustermgmt/v4.0/config/host-nics" in conn.calls


def test_below_v4_nics_come_from_prism_element() -> None:
    pe_nic = {"uuid": "n9", "name": "eth0", "node_uuid": HOST_AHV,
              "mac_address": "02:00:00:00:00:09", "mtu_in_bytes": 9000,
              "link_speed_in_kbps": 25_000_000, "switch_vendor_info": None,
              "switch_port_id": None}
    conn = FakeConn(ready=False, pe={f"{CL_AHV}:hosts/{HOST_AHV}/host_nics": [pe_nic],
                                     f"{CL_ESX}:hosts/{HOST_ESX}/host_nics": []})
    env = _env_with_clusters()
    HostNetworkCollector(conn).collect(env)  # type: ignore[arg-type]
    nic = env.host_nics[0]
    assert (nic.api_source, nic.mtu, nic.speed_mbps, nic.cluster_uuid) == (
        "v2.0", 9000, 25_000, CL_AHV)
    assert env.virtual_switches == []


# ─── storage: external arrays ──────────────────────────────────────────

CTR = {"containerExtId": "c-ext", "name": "ext-fa01", "clusterExtId": CL_AHV,
       "clusterName": "ahv-01", "externalStorageExtId": "array-1", "isInternal": False}
ARRAY_V43 = {"extId": "array-1", "name": "fa-01", "providerType": "EVERPURE_FLASHARRAY",
             "clusterExtId": CL_AHV, "totalCapacityBytes": 100, "freeCapacityBytes": 58,
             "health": "HEALTHY", "endPointAddress": {"ipv4": {"value": "198.51.100.40"}},
             "config": {"$objectType": "clustermgmt.v4.config.EverpureFlashArrayConfig",
                        "realmName": "realm-a", "podName": "pod-a", "username": "u",
                        "password": "never-read"}}


def test_container_link_from_v42_and_array_from_v43() -> None:
    conn = FakeConn({"clustermgmt/v4.2/config/storage-containers": [CTR],
                     "clustermgmt/v4.3/config/external-storages": [ARRAY_V43]},
                    groups={"storage_container": []})
    env = Environment(clusters=[Cluster(uuid=CL_AHV, name="ahv-01")])
    StorageCollector(conn).collect(env)  # type: ignore[arg-type]
    resolve_design_data(env)
    array = env.external_storages[0]
    assert (array.product, array.protocol, array.endpoint, array.pod_name, array.api_source) == (
        "Pure FlashArray", "NVMe/TCP", "198.51.100.40", "pod-a", "v4")
    assert array.container_names == ["ext-fa01"]
    assert env.storage_containers[0].external_storage_name == "fa-01"
    assert "never-read" not in env.model_dump_json()


def test_a_failing_v43_list_falls_back_to_groups_and_keeps_the_containers() -> None:
    row = {"entity_id": "array-1", "name": "fa-01", "provider_type": "EVERPURE_FLASHARRAY"}
    conn = FakeConn({"clustermgmt/v4.2/config/storage-containers": [CTR],
                     "clustermgmt/v4.3/config/external-storages": [ARRAY_V43]},
                    groups={"storage_container": [], "external_storage": [row]})
    conn.v4_list = lambda path, **_kw: (  # type: ignore[method-assign]
        (_ for _ in ()).throw(NutanixApiError("boom", status_code=500))
        if "external-storages" in path else [CTR])
    env = Environment()
    StorageCollector(conn).collect(env)  # type: ignore[arg-type]
    assert len(env.storage_containers) == 1
    array = env.external_storages[0]
    # Groups gave no cluster: it comes from the container on the array.
    assert (array.api_source, array.cluster_uuid) == ("v3-groups", CL_AHV)
    assert env.collection_notes["storage_containers"] == (
        "ok: external storage unavailable (HTTP 500)")


def test_without_v43_the_array_comes_from_groups() -> None:
    row = {"entity_id": "array-1", "name": "pflex-01", "provider_type": "DELL_POWERFLEX",
           "cluster": CL_AHV, "end_point_address": "198.51.100.41",
           "total_capacity_bytes": "200", "free_capacity_bytes": "100", "health": "HEALTHY"}
    conn = FakeConn({"clustermgmt/v4.0/config/storage-containers": [CTR]},
                    groups={"storage_container": [], "external_storage": [row]})
    env = Environment()
    StorageCollector(conn).collect(env)  # type: ignore[arg-type]
    array = env.external_storages[0]
    assert (array.product, array.total_capacity_bytes, array.api_source) == (
        "Dell PowerFlex", 200, "v3-groups")
    assert "probe:clustermgmt/v4.3/config/external-storages" in conn.calls


def test_a_linked_array_nobody_describes_is_still_recorded() -> None:
    conn = FakeConn({"clustermgmt/v4.0/config/storage-containers": [CTR]},
                    groups={"storage_container": [],
                            "external_storage": NutanixApiError("x", status_code=400)})
    env = Environment()
    StorageCollector(conn).collect(env)  # type: ignore[arg-type]
    array = env.external_storages[0]
    assert (array.uuid, array.name, array.product) == ("array-1", None, "External storage")


# ─── Prism Central deployment ──────────────────────────────────────────

DOMAIN_MANAGER = {
    "extId": PC, "hostingClusterExtId": CL_AHV, "isRegisteredWithHostingCluster": True,
    "shouldEnableHighAvailability": False, "nodeExtIds": ["pcvm-1"],
    "config": {"size": "EXTRALARGE", "buildInfo": {"version": "pc.7.5.1.1"},
               "resourceConfig": {"numVcpus": 12, "memorySizeBytes": 38 * 1024**3,
                                  "dataDiskSizeBytes": 2500 * 1024**3}},
    "network": {"fqdn": "pc.example.lab",
                "externalNetworks": [{"ipAddresses": [{"ipv4": {"value": "192.0.2.4"}}]}]},
}
PRODUCTS = [{"name": "SELF_SERVICE", "enablementState": "DISABLED"},
            {"name": "NUTANIX_DISASTER_RECOVERY", "enablementState": "ENABLED",
             "serviceEnablementTime": "2026-05-21T17:24:53Z"},
            {"name": "$UNKNOWN"}]
REGISTRATIONS = [
    {"remoteClusterExtId": CL_AHV, "connectivityStatus": "$UNKNOWN",
     "remoteClusterDetails": {"name": "ahv-01", "clusterType": "AOS", "clusterVersion": "7.5",
                              "externalAddress": {"ipv4": {"value": "192.0.2.31"}}}},
    {"remoteClusterExtId": PC_REMOTE, "connectivityStatus": "PENDING",
     "remoteClusterDetails": {"name": "pc-remote", "clusterType": "DOMAIN_MANAGER",
                              "externalAddress": {},
                              "nodeIpAddresses": [{"ipv4": {"value": "198.51.100.13"}}]}},
]
PC_V3 = {"resources": {"pc_size": "kXLarge", "is_registered_to_hosting_pe": True,
                       "cmsp_config": {"pc_domain_name": "msp.cluster.local"},
                       "pc_vm_list": [{"vm_uuid": "pcvm-1", "vm_name": "NTNX-PCVM-1",
                                       "num_sockets": 12, "memory_size_bytes": 7,
                                       "cluster_reference": {"uuid": CL_AHV},
                                       "nic_list": [{"ip_list": ["192.0.2.4"]}]}]}}


def _pc_conn(**extra: Any) -> FakeConn:
    v4 = {"prism/v4.0/config/domain-managers": [DOMAIN_MANAGER],
          "clustermgmt/v4.0/config/clusters": [],
          f"prism/v4.0/management/domain-managers/{PC}/products": PRODUCTS,
          f"prism/v4.0/management/domain-managers/{PC}/registrations": REGISTRATIONS}
    v4.update(extra)
    return FakeConn(v4, gets={"/api/nutanix/v3/prism_central": PC_V3})


def test_pc_deployment_products_registrations_and_vms() -> None:
    env = Environment(clusters=[Cluster(uuid=CL_AHV, name="ahv-01")])
    PrismCentralConfigCollector(_pc_conn()).collect(env)  # type: ignore[arg-type]
    resolve_design_data(env)
    cfg = env.pc_config
    assert (cfg.size, cfg.hosting_cluster_name, cfg.fqdn, cfg.vm_vcpus, cfg.virtual_ip,
            cfg.cmsp_enabled) == ("EXTRALARGE", "ahv-01", "pc.example.lab", 12, None, True)
    assert cfg.enabled_products == ["NUTANIX_DISASTER_RECOVERY"]
    assert [p.label for p in cfg.products] == ["Disaster Recovery", "Self-Service"]
    regs = {r.remote_uuid: r for r in env.pc_registrations}
    assert regs[CL_AHV].connectivity_status is None  # $UNKNOWN
    assert regs[PC_REMOTE].is_remote_pc and regs[PC_REMOTE].node_ips == ["198.51.100.13"]
    vm = env.pc_vms[0]
    assert (vm.num_vcpus, vm.hosting_cluster_name, vm.ip_addresses) == (
        12, "ahv-01", ["192.0.2.4"])
    assert env.collection_notes["pc_config"] == "ok"


def test_products_failure_is_noted_and_v3_services_stand_in() -> None:
    conn = _pc_conn(**{f"prism/v4.0/management/domain-managers/{PC}/products":
                       NutanixApiError("x", status_code=500)})
    conn.gets["/api/nutanix/v3/services/microseg/status"] = {
        "service_enablement_status": "ENABLED"}
    env = Environment()
    PrismCentralConfigCollector(conn).collect(env)  # type: ignore[arg-type]
    assert [(p.name, p.enabled, p.api_source) for p in env.pc_config.products] == [
        ("FLOW_NETWORK_SECURITY", True, "v3")]
    assert env.collection_notes["pc_config"] == "ok: PC products unavailable (HTTP 500)"
    assert env.pc_registrations  # the rest is kept


def test_pc_below_v4_uses_v3_for_size_and_vms() -> None:
    conn = FakeConn(ready=False, pc_version="pc.2024.1",
                    gets={"/api/nutanix/v3/prism_central": PC_V3,
                          "/PrismGateway/services/rest/v1/cluster": {"numNodes": 1}})
    env = Environment()
    PrismCentralConfigCollector(conn).collect(env)  # type: ignore[arg-type]
    assert env.pc_config.size == "EXTRALARGE"
    assert env.pc_config.hosting_cluster_uuid == CL_AHV
    assert env.pc_registrations == [] and len(env.pc_vms) == 1


# ─── data protection ───────────────────────────────────────────────────

V4_POLICY = {
    "extId": "pp-1", "name": "pp-async-1h",
    "replicationLocations": [
        {"label": "1", "isPrimary": False, "domainManagerExtId": PC,
         "replicationSubLocation": {"clusterExtIds": [CL_AHV]}},
        {"label": "0", "isPrimary": True, "domainManagerExtId": PC_REMOTE,
         "replicationSubLocation": {"clusterExtIds": ["remote-cluster-1"]}},
    ],
    "replicationConfigurations": [
        {"sourceLocationLabel": "0", "remoteLocationLabel": "1", "schedule": {
            "recoveryPointObjectiveTimeSeconds": 3600, "recoveryPointType": "CRASH_CONSISTENT",
            "retention": {"local": 24, "remote": 24}}},
        {"sourceLocationLabel": "1", "remoteLocationLabel": "0", "schedule": {
            "recoveryPointObjectiveTimeSeconds": 3600, "retention": {"local": 24,
                                                                     "remote": 12}}},
    ],
}
V4_SYNC = {"extId": "pp-2", "name": "metro",
           "replicationLocations": [{"label": "a", "isPrimary": True,
                                     "domainManagerExtId": PC},
                                    {"label": "b", "domainManagerExtId": PC}],
           "replicationConfigurations": [{
               "sourceLocationLabel": "a", "remoteLocationLabel": "b",
               "schedule": {"recoveryPointObjectiveTimeSeconds": 0,
                            "syncReplicationAutoSuspendTimeoutSeconds": 10}}]}
V4_PLAN = {"extId": "rp-1", "name": "rp",
           "primaryLocation": {"domainManagerExtId": PC, "clusters": [{"extId": CL_AHV}]},
           "recoveryLocation": {"domainManagerExtId": PC_REMOTE},
           "witness": {"extId": "w-1"}}
V3_PLAN = {"metadata": {"uuid": "rp-1"}, "spec": {"name": "rp", "resources": {
    "stage_list": [{"stage_work": {"recover_entities": {"entity_info_list": [{}, {}]}}}],
    "parameters": {"network_mapping_list": [{}]}}}}


def _dp_conn(**extra: Any) -> FakeConn:
    v4 = {"datapolicies/v4.0/config/protection-policies": [V4_POLICY, V4_SYNC],
          "datapolicies/v4.0/config/recovery-plans": [V4_PLAN],
          "dataprotection/v4.0/config/cluster-capabilities": [
              {"clusterExtId": CL_AHV, "capabilities": [
                  {"capabilityName": "SUPPORTS_NEAR_SYNC", "isSupported": True}]}]}
    v4.update(extra)
    return FakeConn(v4, v3={"protection_rules": [], "recovery_plans": [V3_PLAN],
                            "availability_zones": []})


def test_v4_policy_sites_directions_and_derived_type() -> None:
    env = Environment(clusters=[Cluster(uuid=CL_AHV, name="ahv-01")])
    env.prism_central.uuid = PC
    env.prism_central.host = "pc.example.lab"
    DataProtectionCollector(_dp_conn()).collect(env)  # type: ignore[arg-type]
    resolve_design_data(env)
    pp, sync = env.protection_policies
    # The primary site first, whatever its label.
    assert [loc.label for loc in pp.locations] == ["0", "1"]
    assert pp.source_clusters == ["remote-cluster-1"] and pp.target_clusters == ["ahv-01"]
    assert (pp.replication_type, pp.rpo_seconds, pp.remote_retention) == ("ASYNC", 3600, 24)
    assert pp.is_bidirectional
    assert (sync.replication_type, sync.replication_label) == ("SYNC", "Sync")
    assert not sync.is_bidirectional
    assert env.clusters[0].nearsync_capable is True


def test_v4_recovery_plans_merge_the_v3_counts() -> None:
    env = Environment(clusters=[Cluster(uuid=CL_AHV, name="ahv-01")])
    DataProtectionCollector(_dp_conn()).collect(env)  # type: ignore[arg-type]
    resolve_design_data(env)
    rp = env.recovery_plans[0]
    assert (rp.api_source, rp.stage_count, rp.entities_count, rp.network_mappings) == (
        "v4", 1, 2, 1)
    assert (rp.primary_clusters, rp.witness_uuid) == (["ahv-01"], "w-1")


def test_recovery_plans_fall_back_to_v3_when_v4_fails() -> None:
    env = Environment()
    conn = _dp_conn(**{"datapolicies/v4.0/config/recovery-plans":
                       NutanixApiError("x", status_code=500)})
    DataProtectionCollector(conn).collect(env)  # type: ignore[arg-type]
    assert env.recovery_plans[0].api_source == "v3"


def test_v3_policy_sites_and_directions() -> None:
    rule = {"metadata": {"uuid": "pp-3"}, "spec": {"name": "nearsync", "resources": {
        "ordered_availability_zone_list": [
            {"availability_zone_url": PC, "cluster_uuid": CL_AHV},
            {"availability_zone_url": PC_REMOTE}],
        "availability_zone_connectivity_list": [{
            "source_availability_zone_index": 0, "destination_availability_zone_index": 1,
            "snapshot_schedule_list": [{"recovery_point_objective_secs": 60}]}]}}}
    conn = FakeConn(ready=False, v3={"protection_rules": [rule], "recovery_plans": [],
                                     "availability_zones": []})
    env = Environment(clusters=[Cluster(uuid=CL_AHV, name="ahv-01")])
    DataProtectionCollector(conn).collect(env)  # type: ignore[arg-type]
    resolve_design_data(env)
    pp = env.protection_policies[0]
    assert pp.replication_type == "NEARSYNC" and pp.source_clusters == ["ahv-01"]
    assert [(r.source_label, r.target_label) for r in pp.replications] == [("0", "1")]


# ─── Files / Objects status ────────────────────────────────────────────


def test_objects_503_reads_as_not_enabled_and_files_v4_as_not_deployed() -> None:
    env = Environment()
    conn = FakeConn({"files/v4.0/config/file-servers": []},
                    negotiated={"objects": "legacy: service unavailable", "files": "v4"})
    conn.probe_status = {"objects": 503}
    FilesCollector(conn).collect(env)  # type: ignore[arg-type]
    ObjectsCollector(conn).collect(env)  # type: ignore[arg-type]
    services = {s.name: (s.status, s.api_source) for s in env.pc_config.services}
    assert services == {"FILES": ("NOT_DEPLOYED", "v4"), "OBJECTS": ("NOT_ENABLED", "v4")}
    assert env.collection_notes["files"] == "skipped: Nutanix Files not deployed"
    assert env.collection_notes["objects"] == "skipped: Nutanix Objects not enabled"


def test_another_objects_error_is_unknown_not_absent() -> None:
    env = Environment()
    conn = FakeConn(negotiated={"objects": "legacy: service unavailable"})
    conn.probe_status = {"objects": 500}
    ObjectsCollector(conn).collect(env)  # type: ignore[arg-type]
    assert [(s.status, s.api_source) for s in env.pc_config.services] == [("UNKNOWN", "v4")]
    assert env.collection_notes["objects"] == (
        "skipped: Nutanix Objects status unknown (HTTP 500)")


def test_objects_without_v4_is_decided_by_v3() -> None:
    env = Environment()
    conn = FakeConn(negotiated={"objects": "legacy: namespace absent"})
    ObjectsCollector(conn).collect(env)  # type: ignore[arg-type]
    assert [(s.status, s.api_source) for s in env.pc_config.services] == [
        ("NOT_DEPLOYED", "v3")]


def test_files_listed_only_by_v4_are_recorded() -> None:
    env = Environment()
    conn = FakeConn({"files/v4.0/config/file-servers": [{"extId": "fs-1", "name": "fs"}]})
    FilesCollector(conn).collect(env)  # type: ignore[arg-type]
    assert [(f.name, f.api_source) for f in env.file_servers] == [("fs", "v4")]
    assert env.pc_config.services[0].status == "DEPLOYED"


def test_resolution_is_idempotent_and_names_remote_clusters_after_a_merge() -> None:
    env = Environment(clusters=[Cluster(uuid=CL_AHV, name="ahv-01")])
    DataProtectionCollector(_dp_conn()).collect(env)  # type: ignore[arg-type]
    resolve_design_data(env)
    first = env.model_dump_json()
    resolve_design_data(env)
    assert env.model_dump_json() == first
    # The remote PC's cluster becomes known (a combined run): it gets its name.
    env.clusters.append(Cluster(uuid="remote-cluster-1", name="dr-01"))
    resolve_design_data(env)
    assert env.protection_policies[0].source_clusters == ["dr-01"]


def test_container_without_array_link_stays_on_dsf() -> None:
    env = Environment(storage_containers=[StorageContainer(uuid="c", name="vm-ctr")])
    resolve_design_data(env)
    assert env.storage_containers[0].external_storage_name is None


def test_speed_labels() -> None:
    from ntnxvision_core.exporters.tables import speed_label

    assert [speed_label(v) for v in (None, 0, 100, 1000, 2500, 25000, 100000)] == [
        None, None, "100 Mb/s", "1 GbE", "2.5 GbE", "25 GbE", "100 GbE"]


def test_host_networking_brings_the_hosts_area_with_it() -> None:
    from ntnxvision_core.areas import select

    assert [a.id for a in select(["host_networking"])] == ["hosts", "host_networking"]
    assert [a.id for a in select(["hosts"])] == ["hosts"]


def test_scope_prunes_registered_clusters_but_keeps_paired_pcs() -> None:
    from ntnxvision_core.models import ExternalStorage, PcRegistration

    env = Environment(
        pc_registrations=[PcRegistration(remote_uuid=CL_AHV, remote_type="AOS"),
                          PcRegistration(remote_uuid=CL_ESX, remote_type="AOS"),
                          PcRegistration(remote_uuid=PC_REMOTE, remote_type="DOMAIN_MANAGER")],
        storage_containers=[StorageContainer(uuid="c", cluster_uuid=CL_ESX,
                                             external_storage_uuid="a1")],
        external_storages=[ExternalStorage(uuid="a1")])
    scope = Scope([RegisteredCluster(CL_AHV, "ahv-01"), RegisteredCluster(CL_ESX, "esx-01")],
                  [CL_AHV])
    scope.prune(env)
    assert [r.remote_uuid for r in env.pc_registrations] == [CL_AHV, PC_REMOTE]
    assert env.external_storages == [] and env.storage_containers == []
