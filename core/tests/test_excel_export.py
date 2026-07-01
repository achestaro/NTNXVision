"""Smoke tests that exercise the model → snapshot → Excel pipeline without a live PC."""

from __future__ import annotations

from openpyxl import load_workbook

from ntnxvision_core.exporters import ExcelExporter
from ntnxvision_core.models import (
    Cluster,
    Environment,
    Host,
    Nic,
    StorageContainer,
    Subnet,
    VirtualDisk,
    VirtualMachine,
)
from ntnxvision_core.store import SnapshotStore

GIB = 1024**3


def _sample_env() -> Environment:
    env = Environment(collected_at="2026-06-30T00:00:00Z")
    env.prism_central.host = "pc.lab.local"
    env.prism_central.version = "pc.2024.3"
    env.clusters.append(
        Cluster(uuid="c1", name="cluster-01", aos_version="7.0", hypervisor_types=["AHV"],
                num_nodes=3, redundancy_factor=2)
    )
    env.hosts.append(
        Host(uuid="h1", name="node-a", cluster_uuid="c1", cluster_name="cluster-01",
             hypervisor_type="AHV", num_cpu_cores=32, memory_bytes=256 * GIB)
    )
    vm = VirtualMachine(uuid="v1", name="app-01", cluster_name="cluster-01", power_state="ON",
                        num_vcpus=4, num_cores_per_vcpu=1, memory_bytes=16 * GIB)
    vm.disks.append(VirtualDisk(label="scsi.0", disk_size_bytes=100 * GIB, bus_type="SCSI"))
    vm.nics.append(Nic(mac_address="50:6b:00:00:00:01", ip_addresses=["10.0.0.10"]))
    env.vms.append(vm)
    env.storage_containers.append(
        StorageContainer(uuid="s1", name="default", cluster_name="cluster-01",
                         replication_factor=2, compression_enabled=True,
                         max_capacity_bytes=10 * 1024 * GIB)
    )
    env.subnets.append(
        Subnet(uuid="net1", name="vlan-100", cluster_name="cluster-01",
               subnet_type="VLAN", vlan_id=100)
    )
    env.collection_notes["vms"] = "ok"
    return env


def test_summary_counts() -> None:
    env = _sample_env()
    s = env.summary()
    assert s["clusters"] == 1
    assert s["hosts"] == 1
    assert s["vms"] == 1
    assert s["storage_containers"] == 1
    assert s["subnets"] == 1


def test_vm_derived_fields() -> None:
    vm = _sample_env().vms[0]
    assert vm.vcpu_total == 4
    assert vm.total_disk_bytes == 100 * GIB


def test_excel_export_creates_expected_sheets(tmp_path) -> None:
    out = tmp_path / "env.xlsx"
    ExcelExporter().export(_sample_env(), out)
    assert out.exists()
    wb = load_workbook(out)
    for sheet in ["Overview", "Clusters", "Hosts", "VMs", "Storage", "Subnets"]:
        assert sheet in wb.sheetnames
    vms = wb["VMs"]
    assert vms["A1"].value == "Name"
    assert vms.freeze_panes == "A2"


def test_snapshot_roundtrip(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    path = store.save(_sample_env(), "test-env")
    loaded = store.load(path)
    assert loaded.summary() == _sample_env().summary()
    assert loaded.vms[0].name == "app-01"
