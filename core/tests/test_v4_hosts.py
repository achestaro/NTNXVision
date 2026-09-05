"""The v4 host path, and the two traps real payloads set for it."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.hosts import HostCollector, _is_physical_host
from ntnxvision_core.models import Environment

# Trimmed from a live pc.7.5.1.1 response.
V4_HOST = {
    "extId": "3e022c04-d1a3-4d90-85dd-4e7e3ff131c0",
    "hostName": "host02.lab",
    "cluster": {"name": "lab-cluster-02", "uuid": "0006443c-2078-0848-0000-000000016d33"},
    "hypervisor": {
        "type": "ESX",
        "fullName": "VMware ESXi 7.0.3 build-21930508",
        "externalAddress": {"ipv4": {"value": "198.51.100.212"}},
        "numberOfVms": 153,
    },
    "controllerVm": {
        "externalAddress": {"ipv4": {"value": "198.51.100.222"}},
        "isInMaintenanceMode": False,
    },
    "ipmi": {"ip": {"ipv4": {"value": "198.51.100.22"}}, "username": "ADMIN"},
    "cpuModel": "Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz",
    "numberOfCpuSockets": 2,
    "numberOfCpuCores": 24,
    "numberOfCpuThreads": 48,
    "cpuFrequencyHz": 2500000000,
    "cpuCapacityHz": 60000000000,
    "memorySizeBytes": 274759872512,
    "blockModel": "NX-3060-G4",
    "blockSerial": "16SM60010053",
    "nodeSerial": "OM15AS005545",
    "maintenanceState": "normal",
    "gpuList": [""],
}

# The Prism Central pseudo-host v3 returns: no compute, a serial that is really an IP,
# and a block whose values are the *string* "null".
V3_PHANTOM = {
    "serial_number": "10-13-37-4",
    "block": {"block_serial_number": "null", "block_model": "null"},
    "controller_vm": {"ip": "198.51.100.4"},
    "gpu_list": [],
    "host_type": "HYPER_CONVERGED",
}

V3_REAL = {
    "cpu_model": "Intel(R) Xeon(R) CPU E5-2680 v3 @ 2.50GHz",
    "num_cpu_cores": 24,
    "serial_number": "OM15AS005116",
}


class _Conn:
    def __init__(self, v4: bool) -> None:
        self._v4 = v4

    def v4_ready(self, area: str) -> bool:
        return self._v4

    def v4_list(self, path: str) -> list[dict[str, Any]]:
        return [dict(V4_HOST)]

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        return [
            {"metadata": {"uuid": "pc"}, "status": {"resources": V3_PHANTOM}},
            {"metadata": {"uuid": "h1"}, "status": {"name": "host01", "resources": V3_REAL}},
        ]


def _collect(v4: bool) -> Environment:
    env = Environment()
    HostCollector(_Conn(v4)).collect(env)  # type: ignore[arg-type]
    return env


# ─── trap 1: the Prism Central pseudo-host ─────────────────────────────


def test_phantom_host_is_not_a_physical_node() -> None:
    assert _is_physical_host(V3_PHANTOM) is False
    assert _is_physical_host(V3_REAL) is True


def test_v3_path_drops_the_phantom_row() -> None:
    """It used to pass the filter on a truthy serial and land as a blank Hosts row."""
    env = _collect(v4=False)
    assert [h.name for h in env.hosts] == ["host01"]


def test_memory_alone_is_enough_evidence_of_a_real_node() -> None:
    assert _is_physical_host({"memory_capacity_mib": 262144}) is True
    assert _is_physical_host({"serial_number": "10-13-37-4"}) is False
    assert _is_physical_host({}) is False


# ─── trap 2: cpuCapacityHz is not cpu_capacity_hz ──────────────────────


def test_per_core_frequency_is_used_not_aggregate_capacity() -> None:
    """v4's cpuCapacityHz is freq x cores; mapping by name inflates it 24x here."""
    host = _collect(v4=True).hosts[0]
    assert host.cpu_capacity_hz == 2500000000
    assert host.cpu_capacity_hz != V4_HOST["cpuCapacityHz"]


# ─── the rest of the mapping ───────────────────────────────────────────


def test_v4_host_maps_every_field() -> None:
    h = _collect(v4=True).hosts[0]
    assert h.name == "host02.lab"
    assert h.uuid == V4_HOST["extId"]
    assert h.cluster_name == "lab-cluster-02"  # v4 gives this directly; v3 needs backfill
    assert h.hypervisor_type == "ESXi"  # normalized from v4's "ESX"
    assert h.hypervisor_version == "VMware ESXi 7.0.3 build-21930508"
    assert h.num_cpu_sockets == 2
    assert h.num_cpu_cores == 24
    assert h.num_cpu_threads == 48  # v3 leaves this None
    assert h.memory_bytes == 274759872512
    assert h.block_model == "NX-3060-G4"
    assert h.serial_number == "OM15AS005545"
    assert h.ipmi_ip == "198.51.100.22"
    assert h.cvm_ip == "198.51.100.222"
    assert h.hypervisor_ip == "198.51.100.212"
    assert h.api_source == "v4"


def test_empty_gpu_placeholder_is_dropped() -> None:
    """v4 pads gpuList with an empty string on a host with no GPU."""
    assert _collect(v4=True).hosts[0].gpu_list == []


def test_maintenance_state_uses_the_v3_vocabulary() -> None:
    assert _collect(v4=True).hosts[0].maintenance_state == "NORMAL"


def test_maintenance_state_falls_back_to_the_cvm_flag() -> None:
    entity = {k: v for k, v in V4_HOST.items() if k != "maintenanceState"}
    entity["controllerVm"] = {"isInMaintenanceMode": True}

    class _C(_Conn):
        def v4_list(self, path: str) -> list[dict[str, Any]]:
            return [entity]

    env = Environment()
    HostCollector(_C(True)).collect(env)  # type: ignore[arg-type]
    assert env.hosts[0].maintenance_state == "ENTERED_MAINTENANCE_MODE"
