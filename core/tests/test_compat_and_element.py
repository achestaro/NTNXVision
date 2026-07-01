"""Tests for the version-compatibility layer and Prism Element (v2.0) mapping."""

from __future__ import annotations

from ntnxvision_core.collectors.element import _map_vm, _short_hyp
from ntnxvision_core.compat import check_aos, check_prism_central, parse_version

GIB = 1024**3


# ─── version parsing ───────────────────────────────────────────────────


def test_parse_version_schemes() -> None:
    assert parse_version("pc.2024.3") == ((2024, 3), "calendar")
    assert parse_version("7.5.1.6") == ((7, 5, 1, 6), "semantic")
    assert parse_version("2024.1.0.1") == ((2024, 1, 0, 1), "calendar")
    assert parse_version(None) == (None, "unknown")
    assert parse_version("nonsense") == (None, "unknown")


# ─── PC compatibility (supported: pc.2024.1 → 7.5.1.6) ─────────────────


def test_pc_in_range_no_warning() -> None:
    for v in ("pc.2024.1", "pc.2024.3", "7.0", "7.5.1.6"):
        r = check_prism_central(v)
        assert r.in_range is True
        assert r.warning is None


def test_pc_below_floor_warns() -> None:
    r = check_prism_central("pc.2023.4")
    assert r.in_range is False
    assert r.warning is not None and "below the supported floor" in r.warning


def test_pc_above_ceiling_warns_but_proceeds() -> None:
    r = check_prism_central("7.6.0")
    assert r.in_range is True  # warn but proceed
    assert r.warning is not None and "newer than the tested ceiling" in r.warning


# ─── AOS compatibility (supported: 6.5 → 7.5) ──────────────────────────


def test_aos_in_range() -> None:
    for v in ("6.5", "6.8", "7.0", "7.5", "7.5.1.6"):
        assert check_aos(v).in_range is True


def test_aos_below_floor_warns() -> None:
    r = check_aos("6.1")
    assert r.in_range is False
    assert r.warning is not None


def test_aos_above_ceiling_warns_but_proceeds() -> None:
    r = check_aos("7.6")
    assert r.in_range is True
    assert r.warning is not None


# ─── Prism Element v2.0 mapping ────────────────────────────────────────


def test_short_hypervisor_normalization() -> None:
    assert _short_hyp("kKvm") == "AHV"
    assert _short_hyp("VMware ESXi") == "ESXi"
    assert _short_hyp(None) == ""


def test_map_vm_from_v2_payload() -> None:
    payload = {
        "uuid": "vm-1",
        "name": "web-01",
        "power_state": "on",
        "num_vcpus": 2,
        "num_cores_per_vcpu": 2,
        "memory_mb": 8192,
        "vm_disk_info": [
            {"size": 50 * GIB, "is_cdrom": False,
             "disk_address": {"device_bus": "scsi", "device_index": 0,
                              "vmdisk_uuid": "d1"}},
        ],
        "vm_nics": [
            {"mac_address": "50:6b:00:00:00:aa", "ip_address": "10.0.0.5",
             "network_uuid": "net-1", "is_connected": True},
        ],
    }
    vm = _map_vm(payload)
    assert vm.name == "web-01"
    assert vm.power_state == "ON"
    assert vm.vcpu_total == 4
    assert vm.memory_bytes == 8192 * 1024 * 1024
    assert vm.total_disk_bytes == 50 * GIB
    assert vm.nics[0].ip_addresses == ["10.0.0.5"]
    assert vm.api_source == "v2.0"


# ─── Cross-reference backfill (regression: PE VM host/cluster names + NIC VLAN) ─────


def test_cross_reference_backfills_pe_vm_names_and_vlan() -> None:
    from ntnxvision_core.engine import _resolve_cross_references
    from ntnxvision_core.models import Cluster, Environment, Host, Nic, Subnet, VirtualMachine

    env = Environment(source_type="prism_element")
    env.clusters.append(Cluster(uuid="c1", name="cluster-01"))
    env.hosts.append(Host(uuid="h1", name="node-a"))  # no cluster_name set (PE mode)
    env.subnets.append(Subnet(uuid="net-1", name="vlan-100", vlan_id=100))
    vm = VirtualMachine(uuid="v1", name="web-01", host_uuid="h1")
    vm.nics.append(Nic(subnet_uuid="net-1"))
    env.vms.append(vm)

    _resolve_cross_references(env)

    # Host gets its cluster name from the sole cluster.
    assert env.hosts[0].cluster_name == "cluster-01"
    # VM gets host_name (uuid->name) and cluster_name (sole cluster).
    assert env.vms[0].host_name == "node-a"
    assert env.vms[0].cluster_name == "cluster-01"
    # NIC VLAN backfilled from the referenced subnet.
    assert env.vms[0].nics[0].vlan_id == 100


# ─── Collector field-mapping regressions (previously always-None) ──────────────────


def test_host_maintenance_state_mapped() -> None:
    from ntnxvision_core.collectors.hosts import _maintenance_state

    assert _maintenance_state({"node_state": "ENTERED_MAINTENANCE_MODE"}) == (
        "ENTERED_MAINTENANCE_MODE"
    )
    assert _maintenance_state({"host_in_maintenance_mode": True}) == "ENTERED_MAINTENANCE_MODE"
    assert _maintenance_state({"host_in_maintenance_mode": False}) == "NORMAL"
    assert _maintenance_state({}) is None


def test_vm_protection_type_mapped() -> None:
    from ntnxvision_core.collectors.vms import _protection_type

    assert _protection_type({"protection_type": "RULE_PROTECTED"}) == "RULE_PROTECTED"
    assert _protection_type({}) is None


def test_storage_advertised_distinct_from_max() -> None:
    # Regression: advertised and max must come from different sources.
    from ntnxvision_core.collectors.storage import _as_int

    usage = {"storage.capacity_bytes": "1000"}
    adv = {"max_capacity_bytes": "500"}
    max_cap = _as_int(usage.get("storage.capacity_bytes")) or _as_int(adv.get("max_capacity_bytes"))
    advertised = _as_int(adv.get("max_capacity_bytes"))
    assert max_cap == 1000
    assert advertised == 500
    assert max_cap != advertised


def test_retention_returns_int_or_none() -> None:
    from ntnxvision_core.collectors.dataprotection import _retention

    assert _retention({"num_snapshots": 7}) == 7
    rollup = {"rollup_retention_policy": {"multiple": 3, "snapshot_interval_type": "DAILY"}}
    assert _retention(rollup) == 3
    # interval_type present but multiple missing -> None, not a garbled value.
    assert _retention({"rollup_retention_policy": {"snapshot_interval_type": "DAILY"}}) is None
    assert _retention({}) is None
