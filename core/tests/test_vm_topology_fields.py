"""Per-VM CPU topology fields for the compute review: threads per core and vNUMA nodes.

Field names verified on a live pc.7.5.1.1: v4 AHV ``numThreadsPerCore`` / ``numNumaNodes``,
v3 ``num_threads_per_core`` / ``vnuma_config.num_vnuma_nodes``. The v4 ESXi schema has
neither, so they stay None there.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.vms import VmCollector
from ntnxvision_core.models import Environment
from tests.test_v4_vms import AHV_VM, ESXI_VM


class _V4Conn:
    def v4_ready(self, area: str) -> bool:
        return True

    def v4_list_tolerant(self, path: str) -> tuple[list[dict[str, Any]], int]:
        if "ahv" in path:
            return [{**AHV_VM, "numThreadsPerCore": 2, "numNumaNodes": 2}], 0
        return [dict(ESXI_VM)], 0

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        return []

    def v3_groups(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
        return []


class _V3Conn(_V4Conn):
    def v4_ready(self, area: str) -> bool:
        return False

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        if kind != "vms":
            return []
        return [{
            "metadata": {"uuid": "11111111-2222-3333-4444-555555555555"},
            "status": {"name": "app-01", "cluster_reference": {"uuid": "c1", "name": "C1"},
                       "resources": {"power_state": "ON", "num_sockets": 2,
                                     "num_vcpus_per_socket": 4, "num_threads_per_core": 1,
                                     "vnuma_config": {"num_vnuma_nodes": 0},
                                     "memory_size_mib": 8192, "hypervisor_type": "AHV"}},
        }]


def _vms(conn: Any) -> dict[str, Any]:
    env = Environment()
    VmCollector(conn).collect(env)
    return {v.name: v for v in env.vms}


def test_v4_ahv_reads_threads_and_vnuma() -> None:
    vm = _vms(_V4Conn())["move"]
    assert vm.num_threads_per_core == 2 and vm.num_numa_nodes == 2


def test_v4_esxi_has_no_threads_or_vnuma() -> None:
    vm = _vms(_V4Conn())["nmm-linux-068"]
    assert vm.num_threads_per_core is None and vm.num_numa_nodes is None


def test_v3_reads_threads_and_vnuma() -> None:
    vm = _vms(_V3Conn())["app-01"]
    assert vm.num_threads_per_core == 1 and vm.num_numa_nodes == 0
    assert vm.num_vcpus == 2 and vm.num_cores_per_vcpu == 4
