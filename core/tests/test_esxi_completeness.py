"""ESXi clusters are inventoried as completely as Nutanix allows, inside the run's scope.

Measured on a live pc.7.5.1.1 with one ESXi cluster, the v4 / v3 APIs leave an ESXi
cluster thin: v4 answers HTTP 500 for some VMs, lists only the disks on Nutanix
containers, has no boot firmware, names guest IPs under keys an older reader missed, and
no subnet API lists the cluster's port groups. Prism Element (v2.0 through the Prism
Central proxy) fills all of it. These tests pin that against the demo PC (pc01, whose
AMS-EDGE-02 cluster runs ESXi) and pin that nothing outside the scope is read.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api import PrismCredentials
from ntnxvision_core.collectors.element import _map_vm, port_group_from_v2, v2_boot_type
from ntnxvision_core.collectors.vms import _v4_nics
from ntnxvision_core.engine import collect_environment
from ntnxvision_core.models import Environment
from ntnxvision_core.synthetic.estate import Features, build_pc_estate
from ntnxvision_core.synthetic.profiles import VCENTER_ADDRESS

HOST = "pc01.demo.local"
PW = {"username": "admin", "password": "demo"}


def _estate() -> Any:
    return build_pc_estate(HOST, "medium", "pc.2024.3", features=Features(
        esxi_bad_vms=3, vcenter_address=VCENTER_ADDRESS))


def _collect(**kw: Any) -> Environment:
    return collect_environment(PrismCredentials(host=HOST, **PW), **kw)


def _esxi(est: Any) -> dict[str, Any]:
    return next(c for c in est.clusters if c["hypervisor"] == "ESXi")


# ─── mappers (payload shapes recorded from the lab PC, values synthetic) ─


def test_v4_esxi_guest_ips_are_read_from_ipv4_addresses() -> None:
    vm = {"nics": [{
        "extId": "n1", "backingInfo": {"macAddress": "00:50:56:00:00:01", "isConnected": True,
                                       "adapterType": "VMXNET3"},
        "networkInfo": {"portGroupInfo": {"name": "servers_vlan"},
                        "ipAddressInfo": {"ipv4Addresses": [{"value": "169.254.1.2"},
                                                            {"value": "192.0.2.10"}],
                                          "ipv6Addresses": [{"value": "fe80::1"},
                                                            {"value": "2001:db8::10"}]}}}]}
    nic = _v4_nics(vm)[0]
    assert nic.ip_addresses == ["192.0.2.10", "2001:db8::10"]  # link-local left out
    assert nic.subnet_name == "servers_vlan" and nic.nic_type == "VMXNET3"


def test_v2_vm_maps_every_disk_nic_and_the_boot_firmware() -> None:
    payload = {
        "uuid": "vm-1", "name": "app-01", "power_state": "on", "num_vcpus": 2,
        "num_cores_per_vcpu": 1, "memory_mb": 4096, "guest_os": "ubuntu64Guest",
        "boot": {"uefi_boot": True, "secure_boot": True, "hardware_virtualization": False},
        "vm_disk_info": [
            {"disk_address": {"device_bus": "scsi", "device_index": 0, "vmdisk_uuid": "d1"},
             "is_cdrom": False, "size": 10 * 1024**3, "storage_container_uuid": "ctr-1"},
            # On a local VMFS datastore: a size, no container.
            {"disk_address": {"device_bus": "scsi", "device_index": 1, "vmdisk_uuid": "d2"},
             "is_cdrom": False, "size": 4 * 1024**3},
        ],
        "vm_nics": [{"mac_address": "00:50:56:00:00:02", "network_uuid": "pg-1",
                     "nic_uuid": "n1", "ip_address": "192.0.2.11",
                     "ip_addresses": ["192.0.2.11"], "adapter_type": "Vmxnet3",
                     "is_connected": True}],
    }
    vm = _map_vm(payload, {"pg-1": "servers_vlan"})
    assert vm.boot_type == "SECURE_BOOT" and vm.guest_os == "ubuntu64Guest"
    assert [d.disk_size_bytes for d in vm.disks] == [10 * 1024**3, 4 * 1024**3]
    assert vm.disks[1].storage_container_uuid is None
    nic = vm.nics[0]
    assert (nic.uuid, nic.subnet_name, nic.ip_addresses, nic.nic_type) == (
        "n1", "servers_vlan", ["192.0.2.11"], "Vmxnet3")
    assert v2_boot_type({"hardware_virtualization": False}) == "LEGACY"
    assert v2_boot_type(None) is None


def test_esxi_numcpus_is_the_total_not_sockets() -> None:
    from ntnxvision_core.collectors.vms import _esxi_sockets

    assert _esxi_sockets({"numCpus": 4, "numCoresPerSocket": 2}) == 2  # 2 x 2 = 4 vCPU
    assert _esxi_sockets({"numCpus": 2, "numCoresPerSocket": 1}) == 2
    assert _esxi_sockets({"numCpus": 3, "numCoresPerSocket": 2}) == 3  # odd: keep total
    assert _esxi_sockets({}) is None


def test_a_v2_network_without_a_vlan_is_untagged() -> None:
    pg = port_group_from_v2({"uuid": "pg-1", "name": "VM Network", "network_type": "external"},
                            "c1", "edge")
    assert (pg.vlan_id, pg.cluster_uuid, pg.cluster_name) == (0, "c1", "edge")


# ─── end to end against the demo PC ────────────────────────────────────


def test_esxi_vms_are_complete(demo: Any) -> None:
    est = _estate()
    cluster = _esxi(est)
    env = _collect()
    raw = {v["uuid"]: v for v in est.vms if v["cluster_uuid"] == cluster["uuid"]}
    got = {v.uuid: v for v in env.vms if v.cluster_uuid == cluster["uuid"]}
    # Every VM, the three v4 cannot serve included, with nothing partial.
    assert set(got) == set(raw)
    assert all(v.api_source in ("v4", "v2.0") for v in got.values())
    assert sum(v.api_source == "v2.0" for v in got.values()) == 3
    for uuid, vm in got.items():
        r = raw[uuid]
        assert len([d for d in vm.disks if not d.is_cdrom]) == len(r["disks"])
        assert vm.total_disk_bytes == sum(d["size"] for d in r["disks"])
        assert [n.mac_address for n in vm.nics] == [n["mac"] for n in r["nics"]]
        assert [ip for n in vm.nics for ip in n.ip_addresses] == [
            n["ip"] for n in r["nics"] if n["ip"]]
        assert all(n.subnet_name == "servers_vlan" and n.vlan_id == 300 + cluster["index"]
                   for n in vm.nics)
        assert vm.boot_type in ("LEGACY", "UEFI", "SECURE_BOOT")
        assert vm.hypervisor == "ESXi"
    assert "skipped" not in env.collection_notes["vms"]
    # The local-datastore disks v4 leaves out are there.
    assert any(d.storage_container_uuid is None for v in got.values() for d in v.disks)


def test_esxi_port_groups_and_vcenter_are_reported(demo: Any) -> None:
    est = _estate()
    cluster = _esxi(est)
    env = _collect()
    pgs = {p.name: p for p in env.port_groups}
    assert set(pgs) == {"servers_vlan", "VM Network", "vMotion"}
    assert pgs["servers_vlan"].vlan_id == 300 + cluster["index"]
    assert pgs["VM Network"].vlan_id == 0
    assert all(p.cluster_name == cluster["name"] for p in pgs.values())
    assert pgs["servers_vlan"].vm_count == sum(
        1 for v in est.vms if v["cluster_uuid"] == cluster["uuid"])
    # No Nutanix subnet is invented for the ESXi cluster.
    assert not any(s.cluster_uuid == cluster["uuid"] for s in env.subnets)
    esxi = next(c for c in env.clusters if c.uuid == cluster["uuid"])
    assert esxi.vcenter_address == VCENTER_ADDRESS
    assert all(c.vcenter_address is None for c in env.clusters if c.uuid != cluster["uuid"])


def test_only_the_scoped_clusters_are_read(demo: Any) -> None:
    est = _estate()
    esxi = _esxi(est)
    ahv = next(c for c in est.clusters if c["hypervisor"] == "AHV")

    def proxy_hits() -> int:
        return sum(n for (host, _m, path), n in demo.api.hits.items()
                   if host == HOST and path.startswith("/PrismGateway/services/rest/v2.0/"))

    before = proxy_hits()
    env = _collect(clusters=[ahv["uuid"]])
    # No ESXi cluster in scope: Prism Element is never asked, nothing ESXi recorded.
    assert proxy_hits() == before
    assert not env.port_groups
    assert {v.cluster_uuid for v in env.vms} == {ahv["uuid"]}
    assert "skipped" not in env.collection_notes["vms"]

    env = _collect(clusters=[esxi["uuid"]])
    assert proxy_hits() > before
    assert {v.cluster_uuid for v in env.vms} == {esxi["uuid"]}
    assert len(env.vms) == sum(1 for v in est.vms if v["cluster_uuid"] == esxi["uuid"])
    # The note counts only this scope's VMs: the three v4 fails on, nothing phantom.
    assert env.collection_notes["vms"].startswith("ok: 3 ESXi VM(s) read from Prism Element")


def test_v3_detail_is_asked_for_the_scoped_clusters_only() -> None:
    from ntnxvision_core.collectors.vms import VmCollector
    from ntnxvision_core.scope import RegisteredCluster, Scope

    calls: list[dict[str, Any]] = []

    class _Conn:
        scope = Scope([RegisteredCluster("c1", "edge", hypervisors=["AHV"]),
                       RegisteredCluster("c2", "prod", hypervisors=["AHV"])], ["c1"])

        def v3_list(self, kind: str, **kw: Any) -> list[dict[str, Any]]:
            calls.append({"kind": kind, **kw})
            return []

    VmCollector(_Conn())._v3_detail()  # type: ignore[arg-type]
    assert calls == [{"kind": "vms", "filter": "cluster_name==edge"}]
