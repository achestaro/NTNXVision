"""VM compute optimization rules (`analysis.compute_optimizer`), one test per rule plus the
edge cases: missing host data, mixed hosts in one cluster, powered-off VMs, ESXi."""

from __future__ import annotations

from typing import Any

import pytest

from ntnxvision_core.analysis.compute_optimizer import (
    HostShape,
    analyze_compute,
    optimization_reviews,
    review_vm,
)
from ntnxvision_core.models.environment import Environment, Host, VirtualMachine

GIB = 1024**3

#: A typical 2-socket node: 2 x 24 cores, hyperthreading on, 512 GiB (256 GiB per node).
BIG = HostShape(uuid="h-big", name="node-a.example.lab", sockets=2, cores=48, threads=96,
                memory_bytes=512 * GIB)
#: A smaller node of the same cluster: 2 x 16 cores, 256 GiB.
SMALL = HostShape(uuid="h-small", name="node-b.example.lab", sockets=2, cores=32, threads=64,
                  memory_bytes=256 * GIB)


def vm(sockets: int, cores: int = 1, *, threads: int | None = 1, memory_gib: float = 8,
       vnuma: int | None = 0, hypervisor: str = "AHV", guest_os: str | None = None,
       power: str = "ON", host_uuid: str | None = "h-big", cluster: str = "c1",
       name: str = "vm-01") -> VirtualMachine:
    return VirtualMachine(uuid=f"u-{name}", name=name, cluster_uuid=cluster,
                          cluster_name=cluster.upper(), power_state=power, num_vcpus=sockets,
                          num_cores_per_vcpu=cores, num_threads_per_core=threads,
                          num_numa_nodes=vnuma, memory_bytes=int(memory_gib * GIB),
                          hypervisor=hypervisor, guest_os=guest_os, host_uuid=host_uuid)


def rules(result: dict[str, Any]) -> set[str]:
    return {r["rule"] for r in result["reasons"]}


def layout(result: dict[str, Any], key: str = "recommended") -> tuple[Any, ...]:
    d = result[key]
    return d["sockets"], d["cores_per_socket"], d["threads_per_core"], d["vnuma_nodes"]


# ─── clean VMs ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("v", [
    vm(2), vm(8), vm(24), vm(1, 24), vm(4, 6, memory_gib=200),
    # The socket / core split of a VM inside one node is not a finding on AHV.
    vm(12, 1), vm(3, 5),
    # Correct wide VM: 2 sockets, vNUMA 2, even.
    vm(2, 20, memory_gib=384, vnuma=2),
])
def test_layouts_that_fit_are_clean(v: VirtualMachine) -> None:
    result = review_vm(v, BIG)
    assert result["reasons"] == [] and result["severity"] is None
    assert result["recommended"] == result["current"]


# ─── rule 1: more vCPUs than physical cores ────────────────────────────


def test_more_vcpus_than_cores_is_high_and_capped() -> None:
    result = review_vm(vm(64), BIG)
    assert "vcpus_exceed_cores" in rules(result) and result["severity"] == "high"
    # Capped at the host's 48 cores, one socket per NUMA node, vNUMA on.
    assert layout(result) == (2, 24, 1, 2)
    assert result["recommended"]["vcpus"] == 48


def test_threads_count_towards_vcpus() -> None:
    result = review_vm(vm(2, 16, threads=2), BIG)  # 64 vCPUs
    assert "vcpus_exceed_cores" in rules(result)


# ─── rule 2: spans NUMA nodes while the guest sees one (AHV) ───────────


def test_wide_vm_without_vnuma_is_high() -> None:
    result = review_vm(vm(32), BIG)
    assert rules(result) == {"numa_span_cpu"} and result["severity"] == "high"
    assert layout(result) == (2, 16, 1, 2)


def test_wide_vm_with_unknown_vnuma_is_medium() -> None:
    """An older snapshot has no vNUMA field: asked to check, not called wrong."""
    result = review_vm(vm(32, vnuma=None), BIG)
    assert rules(result) == {"numa_span_cpu"} and result["severity"] == "medium"
    assert "does not record" in result["reasons"][0]["text"]


def test_vnuma_one_counts_as_off() -> None:
    assert rules(review_vm(vm(32, vnuma=1), BIG)) == {"numa_span_cpu"}


def test_memory_only_span_is_medium() -> None:
    result = review_vm(vm(4, memory_gib=300), BIG)
    assert rules(result) == {"numa_span_memory"} and result["severity"] == "medium"
    assert layout(result) == (2, 2, 1, 2)
    assert result["recommended"]["memory_bytes"] == 300 * GIB


# ─── rule 3: vNUMA set, but wrong ──────────────────────────────────────


def test_vnuma_on_a_vm_that_fits_is_low() -> None:
    result = review_vm(vm(8, vnuma=2), BIG)
    assert rules(result) == {"vnuma_not_needed"} and result["severity"] == "low"
    assert layout(result) == (8, 1, 1, 0)


def test_vnuma_count_mismatch_is_medium() -> None:
    result = review_vm(vm(3, 12, vnuma=3), BIG)  # 36 vCPUs need 2 nodes, not 3
    assert rules(result) == {"vnuma_mismatch"}
    assert layout(result) == (2, 18, 1, 2)


def test_socket_straddling_vnuma_nodes_is_medium() -> None:
    result = review_vm(vm(3, 10, vnuma=2), BIG)  # 30 vCPUs, 3 sockets over 2 nodes
    assert rules(result) == {"socket_straddles_vnuma"}
    assert layout(result) == (2, 15, 1, 2)


# ─── rule 4: uneven split ──────────────────────────────────────────────


def test_odd_vcpus_over_two_nodes_round_up_when_there_is_room() -> None:
    result = review_vm(vm(33, vnuma=2), BIG)
    # An odd count also leaves one socket split across the two vNUMA nodes.
    assert rules(result) == {"uneven_split", "socket_straddles_vnuma"}
    assert result["severity"] == "medium"
    assert result["recommended"]["vcpus"] == 34 and layout(result) == (2, 17, 1, 2)


def test_odd_vcpus_round_down_when_the_nodes_are_full() -> None:
    # 33 cores on 2 sockets: 16 per node counted, so 34 vCPUs would not fit two nodes.
    odd = HostShape(uuid="o", name="o", sockets=2, cores=33, threads=66,
                    memory_bytes=512 * GIB)
    result = review_vm(vm(33, vnuma=2), odd)
    assert "uneven_split" in rules(result)
    assert result["recommended"]["vcpus"] == 32 and layout(result) == (2, 16, 1, 2)


# ─── rule 5: threads without SMT ───────────────────────────────────────


def test_guest_threads_on_a_host_without_hyperthreading() -> None:
    no_smt = HostShape(uuid="n", name="n", sockets=2, cores=48, threads=48,
                       memory_bytes=512 * GIB)
    result = review_vm(vm(1, 4, threads=2), no_smt)
    assert rules(result) == {"threads_without_smt"}
    assert layout(result)[2] == 1 and result["recommended"]["vcpus"] == 8
    # With SMT on, or unknown, threads are fine.
    assert review_vm(vm(1, 4, threads=2), BIG)["reasons"] == []
    unknown = HostShape(uuid="u", name="u", sockets=2, cores=48, threads=None,
                        memory_bytes=512 * GIB)
    assert review_vm(vm(1, 4, threads=2), unknown)["reasons"] == []


# ─── rule 6: desktop Windows socket limit ──────────────────────────────


@pytest.mark.parametrize("guest", ["Windows 11 Enterprise", "Microsoft Windows 10 (64-bit)",
                                   "windows:64:Windows11Enterprise"])
def test_desktop_windows_with_more_than_two_sockets(guest: str) -> None:
    result = review_vm(vm(4, guest_os=guest), BIG)
    assert rules(result) == {"guest_socket_limit"} and result["severity"] == "high"
    assert layout(result) == (1, 4, 1, 0)


@pytest.mark.parametrize("guest", ["Windows Server 2022", "windows:64:WindowsServer2025Standard",
                                   "Ubuntu 22.04", None])
def test_servers_and_linux_have_no_socket_limit(guest: str | None) -> None:
    assert review_vm(vm(8, guest_os=guest), BIG)["reasons"] == []


# ─── rule 7 and analyze_compute: mixed clusters, hosts, power ──────────


def host(uuid: str, cluster: str, sockets: int | None, cores: int | None,
         memory_gib: int | None = 512, threads: int | None = None) -> Host:
    return Host(uuid=uuid, name=f"{uuid}.example.lab", cluster_uuid=cluster,
                cluster_name=cluster.upper(), num_cpu_sockets=sockets, num_cpu_cores=cores,
                num_cpu_threads=threads if threads is not None else (cores or 0) * 2 or None,
                memory_bytes=memory_gib * GIB if memory_gib else None)


def env_of(hosts: list[Host], vms: list[VirtualMachine]) -> Environment:
    return Environment(hosts=hosts, vms=vms)


def test_fits_current_host_but_not_the_smallest_in_a_mixed_cluster() -> None:
    env = env_of([host("big", "c1", 2, 48), host("small", "c1", 2, 32, 256)],
                 [vm(1, 20, host_uuid="big")])
    (f,) = analyze_compute(env)["findings"]
    assert {r["rule"] for r in f["reasons"]} == {"smaller_host"}
    assert f["severity"] == "medium" and f["mixed_cluster"] is True
    assert f["host"]["name"] == "big.example.lab" and f["host"]["basis"] == "current"
    # Sized for the smallest host: 20 vCPUs over its 2 x 16-core nodes.
    assert f["sized_for"] == "small.example.lab"
    assert (f["recommended"]["sockets"], f["recommended"]["cores_per_socket"],
            f["recommended"]["vnuma_nodes"]) == (2, 10, 2)


def test_same_hardware_cluster_has_no_smaller_host_finding() -> None:
    env = env_of([host("a", "c1", 2, 48), host("b", "c1", 2, 48)], [vm(1, 20, host_uuid="a")])
    assert analyze_compute(env)["findings"] == []


def test_powered_off_vm_is_checked_against_the_smallest_host() -> None:
    env = env_of([host("big", "c1", 2, 48), host("small", "c1", 2, 32, 256)],
                 [vm(1, 20, power="OFF", host_uuid=None)])
    out = analyze_compute(env)
    (f,) = out["findings"]
    assert f["host"]["name"] == "small.example.lab" and f["host"]["basis"] == "smallest"
    assert {r["rule"] for r in f["reasons"]} == {"numa_span_cpu"}
    assert out["summary"]["powered_off"] == 1 and out["summary"]["reviewed"] == 1


def test_powered_on_vm_on_an_unknown_host_falls_back_to_the_smallest() -> None:
    env = env_of([host("a", "c1", 2, 48)], [vm(32, host_uuid="gone")])
    (f,) = analyze_compute(env)["findings"]
    assert f["host"]["basis"] == "smallest"


def test_missing_host_data_leaves_the_vm_unchecked() -> None:
    env = env_of([host("a", "c1", None, 48), host("b", "c2", 2, None)],
                 [vm(4, cluster="c1"), vm(4, cluster="c2", name="vm-02"),
                  vm(4, cluster="c9", name="vm-03")])
    out = analyze_compute(env)
    assert out["findings"] == []
    assert out["summary"]["not_checked"] == 3 and out["summary"]["reviewed"] == 0
    assert all("No host" in u["reason"] for u in out["not_checked"])


def test_a_vm_without_a_vcpu_count_is_unchecked() -> None:
    v = vm(4)
    v.num_vcpus = None
    out = analyze_compute(env_of([host("h-big", "c1", 2, 48)], [v]))
    assert out["not_checked"][0]["reason"].startswith("The snapshot has no vCPU")


def test_host_without_memory_still_checks_cpu() -> None:
    env = env_of([host("h-big", "c1", 2, 48, memory_gib=None)], [vm(32, memory_gib=900)])
    (f,) = analyze_compute(env)["findings"]
    assert {r["rule"] for r in f["reasons"]} == {"numa_span_cpu"}


def test_missing_vm_cores_and_threads_default_to_one() -> None:
    v = vm(4)
    v.num_cores_per_vcpu = None
    v.num_threads_per_core = None
    assert review_vm(v, BIG)["current"]["vcpus"] == 4


# ─── ESXi ──────────────────────────────────────────────────────────────


def test_esxi_wide_vm_is_not_asked_for_vnuma() -> None:
    """ESXi builds the guest's NUMA layout itself and v4 has no vNUMA field for it."""
    result = review_vm(vm(32, threads=None, vnuma=None, hypervisor="ESXi"), BIG)
    assert result["reasons"] == []
    assert result["current"]["vnuma_nodes"] is None


def test_esxi_rules_that_apply() -> None:
    assert rules(review_vm(vm(64, threads=None, vnuma=None, hypervisor="ESXi"), BIG)) == {
        "vcpus_exceed_cores"}
    assert rules(review_vm(vm(33, threads=None, vnuma=None, hypervisor="ESXi"), BIG)) == {
        "uneven_split"}
    result = review_vm(vm(3, 10, threads=None, vnuma=None, hypervisor="ESXi"), BIG)
    assert rules(result) == {"socket_layout"} and result["severity"] == "low"
    assert layout(result) == (2, 15, 1, None)
    # Memory-only span: ESXi sizes vNUMA from vCPUs, nothing to do.
    assert review_vm(vm(4, memory_gib=300, threads=None, vnuma=None, hypervisor="ESXi"),
                     BIG)["reasons"] == []


# ─── summary and ordering ──────────────────────────────────────────────


def test_summary_counts_and_order() -> None:
    hosts = [host("h-big", "c1", 2, 48), host("h2", "c2", 2, 48)]
    vms = [vm(2, name="clean"), vm(64, name="high"), vm(8, vnuma=2, name="low"),
           vm(4, memory_gib=300, name="medium", cluster="c2", host_uuid="h2"),
           vm(2, name="off", power="OFF", host_uuid=None)]
    out = optimization_reviews(env_of(hosts, vms))
    s = out["vm_compute"]["summary"]
    assert s == {"reviewed": 5, "to_optimize": 3, "clean": 2, "high": 1, "medium": 1, "low": 1,
                 "not_checked": 0, "powered_off": 1}
    assert [f["vm"] for f in out["vm_compute"]["findings"]] == ["high", "medium", "low"]
    (card,) = out["reviews"]
    assert card["id"] == "vm_compute" and card["headline"] == "3 VMs to review"
    assert card["severity"] == "high"
    assert out["vm_compute"]["findings"][0]["finding"] == "More vCPUs than physical cores"


def test_empty_snapshot() -> None:
    out = optimization_reviews(Environment())
    assert out["reviews"][0]["headline"] == "Nothing to review"
    assert out["vm_compute"]["summary"]["reviewed"] == 0


def test_old_snapshot_without_topology_fields_loads() -> None:
    v = VirtualMachine.model_validate({"uuid": "u", "name": "n", "num_vcpus": 2})
    assert v.num_threads_per_core is None and v.num_numa_nodes is None
