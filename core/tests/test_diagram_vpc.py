"""Flow Virtual Networking Design (N2): facts (scoped per Prism Central), layout ladder,
determinism, golden, editing, report sections, and the external-subnet collection."""

from __future__ import annotations

from typing import Any

import pytest

from ntnxvision_core.diagrams import vpc as N2
from ntnxvision_core.diagrams.layout.vpc import layout_site
from ntnxvision_core.models import Environment, FloatingIp, Subnet, Vpc

from . import net_diagram_helpers as H


def test_medium_facts_and_bullets(design_envs: dict[str, Any]) -> None:
    m = N2.vpc_model(design_envs["medium"])
    s = m.sites[0]
    assert [v.name for v in s.vpcs] == ["vpc-dev", "vpc-pci", "vpc-prod"]
    [ext] = s.externals
    assert (ext.name, ext.vlan, ext.prefix, ext.nat, ext.collected) == \
        ("ext-transit", 900, "203.0.113.0/24", False, True)
    assert ext.fips == ["203.0.113.10", "203.0.113.11", "203.0.113.12"]
    assert all(len(v.overlays) == 2 for v in s.vpcs)
    assert N2.bullets(s) == [
        "3 VPCs share external subnet ext-transit, all routed without NAT "
        "(172.16.0.0/16 to 172.18.0.0/16).",
        "6 overlay subnets, all with Nutanix IPAM.",
        "3 floating IPs, 1 per VPC (203.0.113.10 to .12).",
        "vpc-pci carries the most routing: 4 static routes, 2 routing policies.",
        "Every VPC uses DNS 10.20.1.5 and 10.20.1.6.",
    ]
    # Counts the API answered with none read 0, not N/A.
    assert next(v for v in s.vpcs if v.name == "vpc-prod").policies == 0


def _two_pcs_same_vpc_name() -> Environment:
    env = Environment(collected_at="2026-10-01T00:00:00Z", is_combined=True)
    from ntnxvision_core.models import PrismCentral

    for pc in ("pc1.example.lab", "pc2.example.lab"):
        src = Environment()
        src.prism_central = PrismCentral(host=pc)
        env.source_environments.append(src)
        env.vpcs.append(Vpc(uuid=f"v-{pc}", name="vpc-prod", source_pc=pc,
                            external_subnets=["ext"]))
        for j in range(2):
            env.subnets.append(Subnet(uuid=f"o-{pc}-{j}", name=f"ov-{pc[:3]}-{j}",
                                      subnet_type="OVERLAY", vpc_name="vpc-prod",
                                      source_pc=pc, ip_prefix=f"172.16.{j}.0/24"))
        env.floating_ips.append(FloatingIp(uuid=f"f-{pc}", floating_ip="198.51.100.5",
                                           vpc_name="vpc-prod", external_subnet="ext",
                                           source_pc=pc))
    return env


def test_vpcs_are_scoped_by_prism_central() -> None:
    """The legacy figure merged two PCs' "vpc-prod": each keeps its own overlays."""
    m = N2.vpc_model(_two_pcs_same_vpc_name())
    assert [s.name for s in m.sites] == ["pc1.example.lab", "pc2.example.lab"]
    for s in m.sites:
        [v] = s.vpcs
        assert [o.name for o in v.overlays] == [f"ov-{s.name[:3]}-0", f"ov-{s.name[:3]}-1"]
        assert v.fips == 1 and s.externals[0].fips == ["198.51.100.5"]
        # Not collected as a subnet: said so, not invented.
        assert N2.ext_detail(s.externals[0]) == "VLAN and IP range not collected for this subnet"


@pytest.mark.parametrize("name", H.ESTATES)
def test_every_estate_lays_out_cleanly(design_envs: dict[str, Any], name: str) -> None:
    H.assert_clean(H.scenes("vpc", design_envs[name]))


@pytest.mark.parametrize("name", ["medium", "enterprise"])
def test_deterministic(design_envs: dict[str, Any], name: str) -> None:
    H.assert_deterministic("vpc", design_envs[name])


def test_golden(design_envs: dict[str, Any]) -> None:
    H.assert_golden("vpc", design_envs["medium"], "medium")


def test_small_has_no_vpc_figure(design_envs: dict[str, Any]) -> None:
    assert H.scenes("vpc", design_envs["small"]) == []


def test_round_trip(design_envs: dict[str, Any]) -> None:
    H.assert_round_trip("vpc", design_envs["medium"])


def _stress() -> N2.VSite:
    exts = [N2.ExtNet(id=f"ext:pc:e{i}", name=f"ext-{i}", vlan=900 + i, collected=True,
                      prefix=f"192.0.2.{i * 16}/28", nat=i == 1) for i in range(3)]
    vpcs = [N2.VVpc(id=f"vpc:{i}", name=f"vpc-{i:02d}-" + "x" * (i * 3),
                    externals=[exts[i % 3].id] + ([exts[2].id] if i == 4 else []),
                    routed=[f"172.{16 + i}.0.0/16"], routes=i, policies=1, fips=i % 2,
                    overlays=[N2.Overlay(id=f"subnet:{i}:{j}", name=f"ov-{j}",
                                         prefix=f"172.{16 + i}.{j}.0/24", ipam=True, vms=j)
                              for j in range(12 if i == 0 else 2)])
            for i in range(20)]
    return N2.VSite(id="site:pc", name="pc.example.lab", externals=exts, vpcs=vpcs,
                    vpc_count=20)


def test_stress_parts_more_and_lanes() -> None:
    scs = layout_site(_stress())
    H.assert_clean(scs)
    assert len(scs) == 2
    summarised = scs[-1].meta["summarised"]
    drawn = [i for sc in scs for i in sc.meta["drawn"]]
    assert len(drawn) + len(summarised) == 20
    texts = [t for sc in scs for t in H.texts(sc)]
    assert any("more overlay subnets" in t for t in texts)
    assert any(t.startswith(f"+{len(summarised)} more VPCs") for t in texts)
    # Each external subnet has its own channel; NAT links are dashed.
    edges = [e for sc in scs for e in sc.edges if e.data.get("kind") in ("nat", "routed")]
    assert {e.data["kind"] for e in edges} == {"nat", "routed"}
    assert all(e.style.dash for e in edges if e.data["kind"] == "nat")


def test_edit_and_normalize(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    s = N2.vpc_model(env).sites[0]
    v, ext = s.vpcs[0], s.externals[0]
    ops = [{"op": "set", "id": v.id, "field": "label", "value": "Development"},
           {"op": "delete", "id": v.overlays[0].id},
           {"op": "add", "parent": v.id, "field": "overlays", "index": 9,
            "element": {"id": "user:ov", "palette": "overlay", "name": "k8s pods"}},
           {"op": "add", "parent": s.id, "field": "externals", "index": 9,
            "element": {"id": "user:up", "palette": "external", "name": "Core router"}}]
    payload, problems = H.edit("vpc", env, ops)
    assert problems == []
    scs = H.laid_out("vpc", payload)
    H.assert_clean(scs)
    texts = [t for sc in scs for t in H.texts(sc)]
    assert "Development" in texts and "k8s pods" in texts and "Core router" in texts
    # An external subnet cannot be deleted; its links are repaired when it goes anyway.
    _, bad = H.edit("vpc", env, [{"op": "delete", "id": ext.id}])
    assert bad


def test_report_sections(design_envs: dict[str, Any]) -> None:
    heads, blocks = H.report_titles(design_envs["medium"])
    assert any(h.endswith("Virtual Networking Design: md-pc02.demo.local") for h in heads)
    caps = [getattr(b, "caption", "") for b in blocks]
    assert any(c.startswith("Overlay subnets, md-pc01.demo.local (6)") for c in caps)
    assert not any(h.endswith("Virtual Networking (VPCs)") for h in heads)


def test_subnet_collector_reads_external_and_nat() -> None:
    from ntnxvision_core.collectors.subnets import _flag

    assert _flag({"is_external": True}, {}, "is_external") is True
    assert _flag({}, {"enable_nat": False}, "enable_nat") is False
    assert _flag({}, {}, "is_external") is None
