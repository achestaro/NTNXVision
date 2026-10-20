"""Topology diagram rendering + report embedding coverage."""

from __future__ import annotations

from ntnxvision_core.exporters import diagrams
from ntnxvision_core.models import (
    Cluster,
    Environment,
    Host,
    ProtectionPolicy,
    StorageContainer,
    Subnet,
    VirtualMachine,
    Vpc,
)

GIB = 1024**3


def _env() -> Environment:
    env = Environment(collected_at="2026-07-01T00:00:00Z")
    env.prism_central.host = "pc.lab.local"
    env.prism_central.version = "pc.2024.3"
    env.clusters.append(Cluster(
        uuid="c1", name="cluster-01", aos_version="7.0", hypervisor_types=["AHV"],
        num_nodes=3, redundancy_factor=2,
    ))
    for i in range(3):
        env.hosts.append(Host(
            uuid=f"h{i}", name=f"node-{i}", cluster_uuid="c1", cluster_name="cluster-01",
            cpu_model="Intel Xeon Gold 6338", num_cpu_cores=32,
            memory_bytes=256 * GIB, num_vms=10,
        ))
    env.vms.append(VirtualMachine(uuid="v1", name="app-01", cluster_name="cluster-01",
                                   power_state="ON"))
    env.vms.append(VirtualMachine(uuid="v2", name="app-02", cluster_name="cluster-01",
                                   power_state="OFF"))
    env.storage_containers.append(StorageContainer(
        uuid="s1", name="default-container", cluster_name="cluster-01",
        max_capacity_bytes=100 * GIB, used_bytes=60 * GIB,
    ))
    env.subnets.append(Subnet(
        uuid="n1", name="vlan-100", cluster_uuid="c1", cluster_name="cluster-01",
        subnet_type="VLAN", vlan_id=100, virtual_switch="vs0", ip_prefix="10.0.100.0/24",
    ))
    env.subnets.append(Subnet(
        uuid="n2", name="overlay-a", subnet_type="OVERLAY", vpc_name="vpc-1",
        ip_prefix="172.16.0.0/24",
    ))
    env.vpcs.append(Vpc(uuid="vpc1", name="vpc-1", external_subnets=["vlan-100"]))
    env.protection_policies.append(ProtectionPolicy(
        uuid="pp1", name="gold", rpo_seconds=3600, replication_type="ASYNC",
        source_az="Local AZ", target_az="PC_10.1.1.1",
    ))
    return env


def _is_png(diag: diagrams.Diagram | None) -> bool:
    return diag is not None and diag.png.startswith(b"\x89PNG") and diag.width > 0


def test_empty_environment_renders_nothing() -> None:
    env = Environment()
    assert diagrams.vm_power_state_chart(env) is None
    assert diagrams.storage_capacity_chart(env) is None
    assert diagrams.vms_per_cluster_chart(env) is None
    assert diagrams.alert_severity_chart(env) is None


def test_chart_builders_render_png() -> None:
    env = _env()
    assert _is_png(diagrams.vm_power_state_chart(env))
    assert _is_png(diagrams.storage_capacity_chart(env))
    assert _is_png(diagrams.vms_per_cluster_chart(env))


def test_legacy_tree_figures_and_dashboard_are_gone() -> None:
    for name in ("cluster_compute", "network_topology", "vpc_topology",
                 "executive_dashboard", "Node"):
        assert not hasattr(diagrams, name), name


def test_vms_per_cluster_skips_clusters_with_no_vms() -> None:
    env = _env()
    env.clusters.append(Cluster(uuid="c2", name="cluster-02"))
    d = diagrams.vms_per_cluster_chart(env)
    assert _is_png(d)  # cluster-01 still has VMs even though cluster-02 doesn't


def _colours(d: diagrams.Diagram) -> set[tuple[int, int, int]]:
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(d.png)).convert("RGB")
    return {c for n, c in img.getcolors(1 << 20) or [] if n > 200}


def test_vms_per_cluster_is_one_series_colour_top_8() -> None:
    from ntnxvision_core.exporters import theme as T

    env = Environment()
    for i in range(11):
        env.clusters.append(Cluster(uuid=f"c{i}", name=f"cl-{i:02d}"))
        for k in range(i + 1):
            env.vms.append(VirtualMachine(uuid=f"v{i}-{k}", name="x", cluster_uuid=f"c{i}"))
    counts = diagrams.vm_cluster_counts(env)
    assert [n for n, _ in counts[:3]] == ["cl-10", "cl-09", "cl-08"]
    d = diagrams.vms_per_cluster_chart(env)
    assert d is not None
    big = _colours(d)
    # One hue for the bars: no other chart series colour appears.
    assert tuple(T.CHART_RGB[0]) in big
    assert not any(tuple(c) in big for c in T.CHART_RGB[1:])
    assert d.width <= 552  # fits the half-page column at 1:1


def test_storage_capacity_orders_by_cluster_then_fullest_and_drops_system() -> None:
    env = Environment()
    env.clusters += [Cluster(uuid="a", name="B-CL"), Cluster(uuid="b", name="A-CL")]
    for i, (cl, used) in enumerate([("a", 10), ("a", 95), ("b", 50), ("b", 85)]):
        env.storage_containers.append(StorageContainer(
            uuid=f"s{i}", name=f"ctr-{i}", cluster_uuid=cl, max_capacity_bytes=100 * GIB,
            used_bytes=used * GIB))
    env.storage_containers.append(StorageContainer(
        uuid="sys", name="NutanixManagementShare", cluster_uuid="a",
        max_capacity_bytes=100 * GIB, used_bytes=GIB))
    rows, total = diagrams.capacity_containers(env)
    assert total == 4
    assert [label for label, _ in rows] == ["A-CL \u00b7 ctr-3", "A-CL \u00b7 ctr-2",
                                            "B-CL \u00b7 ctr-1", "B-CL \u00b7 ctr-0"]


def test_storage_capacity_caps_at_12() -> None:
    env = Environment()
    env.clusters.append(Cluster(uuid="a", name="CL"))
    for i in range(20):
        env.storage_containers.append(StorageContainer(
            uuid=f"s{i}", name=f"ctr-{i:02d}", cluster_uuid="a",
            max_capacity_bytes=100 * GIB, used_bytes=i * GIB))
    rows, total = diagrams.capacity_containers(env)
    assert (len(rows), total) == (12, 20)


def test_storage_capacity_skips_containers_without_max_capacity() -> None:
    env = Environment()
    env.storage_containers.append(StorageContainer(uuid="s1", name="no-capacity"))
    assert diagrams.storage_capacity_chart(env) is None


def test_word_export_embeds_diagrams(tmp_path) -> None:
    from docx import Document

    from ntnxvision_core.exporters.word import WordExporter

    out = WordExporter().export(_env(), tmp_path / "add.docx")
    doc = Document(str(out))
    # Architecture figures and the storage capacity, VM power state and VMs-per-cluster
    # charts. The cover and the executive figure strip are native Word content.
    assert len(doc.inline_shapes) >= 5


def test_pdf_export_with_diagrams(tmp_path) -> None:
    from ntnxvision_core.exporters.pdf import PdfExporter

    out = PdfExporter().export(_env(), tmp_path / "report.pdf")
    assert out.exists()
    assert out.stat().st_size > 20_000  # diagrams push it well past text-only size
