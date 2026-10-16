"""Cluster capacity and oversubscription math (`ntnxvision_core.capacity`)."""

from __future__ import annotations

import pytest

from ntnxvision_core.capacity import (
    DEFAULT_VCPU_RATIO,
    coerce_ratio,
    compute_cluster,
    compute_environment,
    ratio_or_none,
)
from ntnxvision_core.models import (
    Cluster,
    Environment,
    Host,
    Nic,
    PrismCentral,
    SourceEnvironment,
    StorageContainer,
    VirtualDisk,
    VirtualMachine,
)
from ntnxvision_core.numfmt import NA, fmt_ratio

TB = 10**12
GIB = 1024**3


def _cluster(nodes: int, rf: int | None = 2, name: str = "c1", **kw: object) -> Cluster:
    return Cluster(uuid=f"{name}-uuid", name=name, num_nodes=nodes, redundancy_factor=rf,
                   **kw)  # type: ignore[arg-type]


def _hosts(specs: list[tuple[int | None, int | None]], cluster: str = "c1") -> list[Host]:
    """(cores, raw bytes) per node."""
    return [Host(uuid=f"{cluster}-h{i}", name=f"{cluster}-h{i}", cluster_uuid=f"{cluster}-uuid",
                 cluster_name=cluster, num_cpu_cores=cores, storage_capacity_bytes=raw,
                 cvm_ip=f"10.0.0.{i}")
            for i, (cores, raw) in enumerate(specs)]


def _cvms(hosts: list[Host], vcpus: int | list[int]) -> list[VirtualMachine]:
    per = vcpus if isinstance(vcpus, list) else [vcpus] * len(hosts)
    return [VirtualMachine(uuid=f"cvm-{h.uuid}", name=f"NTNX-{h.name}-CVM", vm_type="CVM",
                           num_vcpus=v, host_name=h.name, cluster_name=h.cluster_name)
            for h, v in zip(hosts, per, strict=True)]


def _vm(vcpus: int, disk_gib: int = 0, power: str = "ON", cluster: str = "c1",
        cores_per: int = 1) -> VirtualMachine:
    return VirtualMachine(
        uuid=f"vm-{vcpus}-{disk_gib}-{power}-{id(object())}", name="vm", num_vcpus=vcpus,
        num_cores_per_vcpu=cores_per, power_state=power, cluster_uuid=f"{cluster}-uuid",
        disks=[VirtualDisk(disk_size_bytes=disk_gib * GIB)] if disk_gib else [])


# ─── homogeneous ──────────────────────────────────────────────────────


def test_homogeneous_four_node_rf2_matches_the_formulas() -> None:
    hosts = _hosts([(64, 20 * TB)] * 4)
    vms = [_vm(8, 500) for _ in range(30)] + [_vm(4, 100, power="OFF") for _ in range(10)]
    cap = compute_cluster(_cluster(4), hosts, vms, _cvms(hosts, 12))

    # (64 - 12) x 4 = 208 usable cores; vCPU Total at 4:1 = 832.
    assert cap.physical_cores == 256
    assert cap.cvm_vcpus == 48
    assert cap.usable_cores == 208
    assert cap.vcpu_total == 832
    # N-1: (64 - 12) x 3.
    assert cap.usable_cores_n1 == 156
    assert cap.vcpu_total_n1 == 624
    # Provisioned counts powered-off VMs too: 30 x 8 + 10 x 4 = 280.
    assert cap.provisioned_vcpus == 280
    assert cap.provisioned_vcpus_on == 240
    assert cap.cpu_ratio == pytest.approx(280 / 208)
    assert cap.cpu_ratio_n1 == pytest.approx(280 / 156)
    assert cap.cpu_ratio_on == pytest.approx(240 / 208)
    # Storage Total = 20 TB x 4 x 0.95 / 2.
    assert cap.raw_bytes == 80 * TB
    assert cap.storage_total_bytes == pytest.approx(80 * TB * 0.95 / 2)
    assert cap.storage_total_n1_bytes == pytest.approx(60 * TB * 0.95 / 2)
    prov = (30 * 500 + 10 * 100) * GIB
    assert cap.provisioned_disk_bytes == prov
    assert cap.storage_ratio == pytest.approx(prov / (80 * TB * 0.95 / 2))
    assert cap.storage_ratio_n1 == pytest.approx(prov / (60 * TB * 0.95 / 2))


def test_rf3_divides_storage_by_three() -> None:
    hosts = _hosts([(48, 30 * TB)] * 5)
    cap = compute_cluster(_cluster(5, rf=3), hosts, [], _cvms(hosts, 16))
    assert cap.rf == 3 and cap.rf_source == "cluster"
    assert cap.storage_total_bytes == pytest.approx(150 * TB * 0.95 / 3)
    assert cap.storage_total_n1_bytes == pytest.approx(120 * TB * 0.95 / 3)
    # No VMs: ratios are 0, not N/A, because the capacity is known.
    assert cap.cpu_ratio == 0 and cap.storage_ratio == 0


def test_vcpu_counts_cores_per_socket() -> None:
    hosts = _hosts([(32, TB)] * 3)
    cap = compute_cluster(_cluster(3), hosts, [_vm(2, cores_per=4)], _cvms(hosts, 8))
    assert cap.provisioned_vcpus == 8


def test_target_ratio_scales_vcpu_total_but_not_oversubscription() -> None:
    hosts = _hosts([(40, TB)] * 3)
    vms = [_vm(10)]
    a = compute_cluster(_cluster(3), hosts, vms, _cvms(hosts, 8), target_ratio=4.0)
    b = compute_cluster(_cluster(3), hosts, vms, _cvms(hosts, 8), target_ratio=6.0)
    assert a.vcpu_total == 96 * 4 and b.vcpu_total == 96 * 6
    assert a.cpu_ratio == b.cpu_ratio


# ─── heterogeneous ────────────────────────────────────────────────────


def test_heterogeneous_sums_per_node_and_n1_drops_the_largest_node() -> None:
    hosts = _hosts([(48, 10 * TB), (48, 10 * TB), (64, 40 * TB), (96, 20 * TB)])
    cvms = _cvms(hosts, [12, 12, 12, 16])
    cap = compute_cluster(_cluster(4), hosts, [_vm(100, 1024)], cvms)
    usable = [36, 36, 52, 80]
    assert cap.usable_cores == sum(usable) == 204
    # Worst case for CPU: the 96-core node (80 usable) is lost.
    assert cap.usable_cores_n1 == 124
    # Worst case for storage: the 40 TB node, a different node.
    assert cap.raw_bytes == 80 * TB
    assert cap.storage_total_n1_bytes == pytest.approx(40 * TB * 0.95 / 2)
    assert cap.cpu_ratio_n1 == pytest.approx(100 / 124)


# ─── node counts ──────────────────────────────────────────────────────


def test_single_node_cluster_has_no_n1() -> None:
    hosts = _hosts([(32, 8 * TB)])
    cap = compute_cluster(_cluster(1), hosts, [_vm(16, 200)], _cvms(hosts, 8))
    assert cap.usable_cores == 24
    assert cap.cpu_ratio == pytest.approx(16 / 24)
    assert cap.storage_total_bytes == pytest.approx(8 * TB * 0.95 / 2)
    assert cap.usable_cores_n1 is None and cap.vcpu_total_n1 is None
    assert cap.cpu_ratio_n1 is None and cap.storage_ratio_n1 is None
    assert cap.storage_total_n1_bytes is None
    assert any("N-1 is not applicable" in n for n in cap.notes)


def test_two_node_cluster_has_no_n1() -> None:
    hosts = _hosts([(32, 8 * TB)] * 2)
    cap = compute_cluster(_cluster(2), hosts, [_vm(16, 200)], _cvms(hosts, 8))
    assert cap.usable_cores == 48
    assert cap.cpu_ratio_n1 is None and cap.storage_ratio_n1 is None
    assert fmt_ratio(cap.cpu_ratio_n1) == NA


def test_three_node_cluster_has_n1() -> None:
    hosts = _hosts([(32, 8 * TB)] * 3)
    cap = compute_cluster(_cluster(3), hosts, [_vm(48)], _cvms(hosts, 8))
    assert cap.usable_cores_n1 == 48
    assert cap.cpu_ratio_n1 == pytest.approx(1.0)


# ─── missing data ─────────────────────────────────────────────────────


def test_no_hosts_gives_no_figures() -> None:
    cap = compute_cluster(_cluster(3), [], [_vm(4)], [])
    assert cap.usable_cores is None and cap.storage_total_bytes is None
    assert cap.cpu_ratio is None and cap.storage_ratio is None
    assert cap.provisioned_vcpus == 4  # what is provisioned is still known


def test_missing_cvm_data_blanks_cpu_but_not_storage() -> None:
    hosts = _hosts([(32, 8 * TB)] * 3)
    cap = compute_cluster(_cluster(3), hosts, [_vm(4, 10)], [])
    assert cap.physical_cores == 96
    assert cap.usable_cores is None and cap.vcpu_total is None and cap.cpu_ratio is None
    assert cap.storage_total_bytes is not None and cap.storage_ratio is not None


def test_a_missing_cvm_is_estimated_from_the_largest_known() -> None:
    hosts = _hosts([(32, TB)] * 3)
    cvms = _cvms(hosts[:2], [8, 10])
    cap = compute_cluster(_cluster(3), hosts, [], cvms)
    assert cap.cvm_vcpus == 8 + 10 + 10
    assert cap.nodes[2].cvm_estimated
    assert any("estimated" in n for n in cap.notes)


def test_cvm_is_matched_by_ip_when_host_names_differ() -> None:
    hosts = _hosts([(32, TB)] * 3)
    cvms = [VirtualMachine(uuid=f"cvm{i}", name=f"NTNX-{i}-CVM", vm_type="CVM", num_vcpus=6,
                           nics=[Nic(ip_addresses=[h.cvm_ip or ""])])
            for i, h in enumerate(hosts)]
    cap = compute_cluster(_cluster(3), hosts, [], cvms)
    assert cap.cvm_vcpus == 18 and not any(n.cvm_estimated for n in cap.nodes)


def test_missing_core_count_blanks_cpu() -> None:
    hosts = _hosts([(32, TB), (None, TB), (32, TB)])
    cap = compute_cluster(_cluster(3), hosts, [], _cvms(hosts, 8))
    assert cap.physical_cores is None and cap.cpu_ratio is None


def test_missing_raw_storage_blanks_storage() -> None:
    hosts = _hosts([(32, TB), (32, None), (32, TB)])
    cap = compute_cluster(_cluster(3), hosts, [], _cvms(hosts, 8))
    assert cap.raw_bytes is None and cap.storage_total_bytes is None
    assert cap.storage_ratio is None and cap.cpu_ratio is not None


def test_missing_rf_falls_back_to_container_rf_then_na() -> None:
    hosts = _hosts([(32, TB)] * 3)
    ctrs = [StorageContainer(uuid="s1", name="ctr", cluster_uuid="c1-uuid",
                             replication_factor=3)]
    cap = compute_cluster(_cluster(3, rf=None), hosts, [], _cvms(hosts, 8), ctrs)
    assert cap.rf == 3 and cap.rf_source == "containers"
    assert cap.storage_total_bytes == pytest.approx(3 * TB * 0.95 / 3)
    none = compute_cluster(_cluster(3, rf=None), hosts, [], _cvms(hosts, 8), [])
    assert none.rf is None and none.storage_total_bytes is None
    assert none.raw_bytes == 3 * TB


def test_container_rf_that_differs_is_noted_but_cluster_rf_wins() -> None:
    hosts = _hosts([(32, TB)] * 3)
    ctrs = [StorageContainer(uuid="s1", name="ctr-a", cluster_uuid="c1-uuid",
                             replication_factor=3)]
    cap = compute_cluster(_cluster(3, rf=2), hosts, [], _cvms(hosts, 8), ctrs)
    assert cap.rf == 2
    assert any("ctr-a (RF 3)" in n for n in cap.notes)


def test_cvms_consuming_every_core_never_divide_by_zero() -> None:
    hosts = _hosts([(8, TB)] * 3)
    cap = compute_cluster(_cluster(3), hosts, [_vm(4)], _cvms(hosts, 8))
    assert cap.usable_cores == 0
    assert cap.cpu_ratio is None and cap.cpu_ratio_n1 is None


def test_ratio_or_none_guards() -> None:
    assert ratio_or_none(4, 2) == 2
    assert ratio_or_none(4, 0) is None
    assert ratio_or_none(None, 2) is None
    assert ratio_or_none(4, None) is None


# ─── environment level ────────────────────────────────────────────────


def test_environment_excludes_cvms_from_provisioned_and_includes_pc_vm() -> None:
    env = Environment()
    env.clusters = [_cluster(3)]
    env.hosts = _hosts([(32, 10 * TB)] * 3)
    env.system_vms = [*_cvms(env.hosts, 8),
                      VirtualMachine(uuid="pc", name="pc", vm_type="PRISM_CENTRAL",
                                     num_vcpus=6, cluster_uuid="c1-uuid", power_state="ON")]
    env.vms = [_vm(10)]
    cap = compute_environment(env)[id(env.clusters[0])]
    assert cap.provisioned_vcpus == 16
    assert cap.cvm_vcpus == 24
    assert cap.target_ratio == DEFAULT_VCPU_RATIO


def test_combined_environment_keeps_same_named_clusters_apart() -> None:
    env = Environment()
    a = Cluster(uuid="ua", name="Cluster01", num_nodes=3, redundancy_factor=2, source_pc="pc-a")
    b = Cluster(uuid="ub", name="Cluster01", num_nodes=3, redundancy_factor=2, source_pc="pc-b")
    env.clusters = [a, b]
    env.source_environments = [SourceEnvironment(prism_central=PrismCentral(host=h))
                               for h in ("pc-a", "pc-b")]
    for c, cores in ((a, 32), (b, 64)):
        for i in range(3):
            env.hosts.append(Host(uuid=f"{c.uuid}{i}", name=f"n{i}", cluster_name="Cluster01",
                                  source_pc=c.source_pc, num_cpu_cores=cores,
                                  storage_capacity_bytes=TB))
            env.system_vms.append(VirtualMachine(
                uuid=f"cvm{c.uuid}{i}", name="cvm", vm_type="CVM", num_vcpus=8,
                host_uuid=f"{c.uuid}{i}", cluster_name="Cluster01", source_pc=c.source_pc))
    env.vms.append(VirtualMachine(uuid="v", name="v", num_vcpus=12, cluster_name="Cluster01",
                                  source_pc="pc-b"))
    caps = compute_environment(env)
    assert caps[id(a)].usable_cores == 72 and caps[id(a)].provisioned_vcpus == 0
    assert caps[id(b)].usable_cores == 168 and caps[id(b)].provisioned_vcpus == 12


def test_coerce_ratio() -> None:
    assert coerce_ratio(None) == DEFAULT_VCPU_RATIO
    assert coerce_ratio("6") == 6.0
    assert coerce_ratio(0) == DEFAULT_VCPU_RATIO
    assert coerce_ratio(1000) == DEFAULT_VCPU_RATIO
    assert coerce_ratio("x") == DEFAULT_VCPU_RATIO


def test_fmt_ratio() -> None:
    assert fmt_ratio(3.5) == "3.5 : 1"
    assert fmt_ratio(3.46) == "3.5 : 1"
    assert fmt_ratio(4) == "4.0 : 1"
    assert fmt_ratio(1234.56) == "1,234.6 : 1"
    assert fmt_ratio(None) == NA
