"""Topology diagram rendering + report embedding coverage."""

from __future__ import annotations

from ntnxvision_core.exporters import diagrams
from ntnxvision_core.models import (
    Cluster,
    Environment,
    Host,
    ProtectionPolicy,
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
    env.vms.append(VirtualMachine(uuid="v1", name="app-01", cluster_name="cluster-01"))
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


def test_all_builders_render_png() -> None:
    env = _env()
    cluster = env.clusters[0]
    assert _is_png(diagrams.management_topology(env))
    assert _is_png(diagrams.cluster_compute(env, cluster))
    assert _is_png(diagrams.network_topology(env, cluster))
    assert _is_png(diagrams.vpc_topology(env))
    assert _is_png(diagrams.replication_topology(env))


def test_empty_environment_renders_nothing() -> None:
    env = Environment()
    assert diagrams.management_topology(env) is None
    assert diagrams.vpc_topology(env) is None
    assert diagrams.replication_topology(env) is None
    lone = Cluster(uuid="cx", name="empty")
    assert diagrams.cluster_compute(env, lone) is None
    assert diagrams.network_topology(env, lone) is None


def test_overflow_is_capped_not_crashing() -> None:
    env = _env()
    for i in range(30):
        env.clusters.append(Cluster(uuid=f"cc{i}", name=f"cluster-{i:02d}"))
        env.hosts.append(Host(uuid=f"hh{i}", name=f"node-x{i}", cluster_uuid="c1"))
    d = diagrams.management_topology(env)
    assert _is_png(d)
    compute = diagrams.cluster_compute(env, env.clusters[0])
    assert _is_png(compute)


def test_word_export_embeds_diagrams(tmp_path) -> None:
    from docx import Document

    from ntnxvision_core.exporters.word import WordExporter

    out = WordExporter().export(_env(), tmp_path / "add.docx")
    doc = Document(str(out))
    # management + compute + network + vpc + replication figures
    assert len(doc.inline_shapes) >= 5


def test_pdf_export_with_diagrams(tmp_path) -> None:
    from ntnxvision_core.exporters.pdf import PdfExporter

    out = PdfExporter().export(_env(), tmp_path / "report.pdf")
    assert out.exists()
    assert out.stat().st_size > 20_000  # diagrams push it well past text-only size
