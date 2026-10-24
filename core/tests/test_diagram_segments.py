"""Network Segment Design (N1): facts, layout ladder, determinism, golden, editing,
report sections.

Estates come from the session fixture `design_envs` (conftest)."""

from __future__ import annotations

from typing import Any

import pytest

from ntnxvision_core.diagrams import TYPES
from ntnxvision_core.diagrams import segments as N1
from ntnxvision_core.diagrams import text as tx
from ntnxvision_core.diagrams import tokens as K
from ntnxvision_core.diagrams.layout.segments import compact_text, layout_site
from ntnxvision_core.diagrams.netcommon import short_gw, vlan_text
from ntnxvision_core.models import Cluster, Environment, PortGroup, Subnet, VirtualSwitch

from . import net_diagram_helpers as H


def test_medium_facts(design_envs: dict[str, Any]) -> None:
    m = N1.segments_model(design_envs["medium"])
    assert [s.name for s in m.sites] == ["md-pc01.demo.local", "md-pc02.demo.local"]
    s = m.sites[0]
    assert [c.name for c in s.clusters] == ["PROD-E01", "PROD-E02", "VDI-E01"]
    assert s.clusters[0].hosts_pc
    vs0 = s.clusters[0].switches[0]
    assert (vs0.name, vs0.default, vs0.mtu) == ("vs0", True, 1500)
    # Sorted by VLAN, never in API order; the external uplink subnet is flagged.
    assert [g.vlan for g in vs0.segments] == sorted(g.vlan or 0 for g in vs0.segments)
    assert any(g.external and g.name == "ext-transit" for g in vs0.segments)
    # A switch with no subnet is kept.
    assert any(not w.segments for w in s.clusters[0].switches)
    # ESXi: port groups only, no IP data, switch not reported.
    pg = s.clusters[2].switches[0]
    assert pg.kind == "portgroups" and all(g.prefix is None for g in pg.segments)
    assert N1.segment_detail(pg.segments[0]) == "VM count only (no IP data)"


def test_medium_bullets(design_envs: dict[str, Any]) -> None:
    s = N1.segments_model(design_envs["medium"]).sites[0]
    b = N1.bullets(s)
    # The external subnet is Flow virtual networking (N2) data: counted apart.
    assert b[0] == ("3 clusters: 10 VLAN subnets and 1 external on 2 AHV clusters, "
                    "3 vSphere port groups on VDI-E01.")
    assert any(x.startswith("All VLAN subnets sit on vs0 (MTU 1,500)") for x in b)
    assert any("No VLAN id carries two different prefixes" in x for x in b)
    assert b[-1].startswith("VDI-E01 (ESXi): switch and teaming are not reported")
    assert 2 <= len(b) <= 5


def test_text_helpers() -> None:
    assert vlan_text(100) == "VLAN 100" and vlan_text(4094) == "VLAN 4094"
    assert vlan_text(0) == "Untagged" and vlan_text(None) == "VLAN N/A"
    assert short_gw("10.1.2.0/24", "10.1.2.1") == "gw .1"
    assert short_gw("10.1.2.0/24", "10.9.9.1") == "gw 10.9.9.1"


@pytest.mark.parametrize("name", H.ESTATES)
def test_every_estate_lays_out_cleanly(design_envs: dict[str, Any], name: str) -> None:
    H.assert_clean(H.scenes("segments", design_envs[name]))


@pytest.mark.parametrize("name", ["medium", "enterprise", "pc02"])
def test_deterministic(design_envs: dict[str, Any], name: str) -> None:
    H.assert_deterministic("segments", design_envs[name])


@pytest.mark.parametrize("name", ["medium", "small"])
def test_golden(design_envs: dict[str, Any], name: str) -> None:
    H.assert_golden("segments", design_envs[name], name)


def test_round_trip(design_envs: dict[str, Any]) -> None:
    H.assert_round_trip("segments", design_envs["enterprise"])


def _stress(clusters: int = 13, subnets: int = 40, same_plan: bool = False) -> Environment:
    env = Environment(collected_at="2026-10-01T00:00:00Z")
    env.prism_central.host = "pc.example.lab"
    for c in range(clusters):
        env.clusters.append(Cluster(uuid=f"c{c:02d}", name=f"ROBO-{c + 1:02d}" + (
            "-" + "X" * 50 if c == 1 else ""), hypervisor_types=["AHV"], num_nodes=3))
        for k in range(6 if c == 0 else 1):
            env.virtual_switches.append(VirtualSwitch(
                uuid=f"vs{c}{k}", name=f"vs{k}", cluster_uuid=f"c{c:02d}", is_default=k == 0,
                mtu=9000 if k else 1500))
        for i in range(subnets if c == 0 else 3):
            env.subnets.append(Subnet(
                uuid=f"s{c}-{i}", name=f"seg-{c}-{i}", cluster_uuid=f"c{c:02d}",
                subnet_type="VLAN", vlan_id=[0, 4094, 100][i] if i < 3 else 100 + i,
                virtual_switch="vs0", ip_prefix=f"10.{0 if same_plan else c}.{i}.0/24",
                gateway_ip=f"10.{0 if same_plan else c}.{i}.1", dhcp_enabled=i % 2 == 0))
    return env


def test_stress_fits_with_more_and_parts() -> None:
    env = _stress()
    scs = H.scenes("segments", env)
    H.assert_clean(scs)
    assert 1 <= len(scs) <= 2
    texts = [t for sc in scs for t in H.texts(sc)]
    assert any(t.startswith("+") and "more subnets" in t for t in texts)
    # The "+N" numbers add up: drawn rows plus summarised rows equal the segments.
    s = N1.segments_model(env).sites[0]
    total = sum(len(w.segments) for c in s.clusters for w in c.switches)
    assert total == len(N1.table_rows(env, s))


def test_identical_plans_collapse_and_conflicts_are_named() -> None:
    env = _stress(clusters=4, subnets=3, same_plan=True)
    s = N1.segments_model(env).sites[0]
    collapsed = [c for c in s.clusters if c.applies_to]
    assert collapsed and len(collapsed[0].applies_to) == 3  # ROBO-01 hosts no PC either
    assert "share one segment plan" in " ".join(N1.bullets(s))
    # The table lists every cluster's segments.
    assert len(N1.table_rows(env, s)) == 12
    env2 = _stress(clusters=2, subnets=3)
    s2 = N1.segments_model(env2).sites[0]
    assert N1.vlan_conflicts(s2)
    assert any(b.startswith("Untagged has") or b.startswith("VLAN") and " has " in b
               for b in N1.bullets(s2))
    H.assert_clean(layout_site(s2))


def test_port_group_only_cluster() -> None:
    env = Environment()
    env.prism_central.host = "pc.example.lab"
    env.clusters.append(Cluster(uuid="e", name="ESX-01", hypervisor_types=["ESXi"]))
    env.port_groups.append(PortGroup(uuid="p", name="VM Network", cluster_uuid="e",
                                     vlan_id=0, vm_count=3))
    assert TYPES["segments"].applies(env)
    scs = H.scenes("segments", env)
    H.assert_clean(scs)
    assert "VM count only (no IP data)" in H.texts(scs[0])


# ─── editing ───────────────────────────────────────────────────────────


def test_edit_rename_note_move_delete_add(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    m = N1.segments_model(env)
    c = m.sites[0].clusters[0]
    seg = c.switches[0].segments[0]
    assert {c.id, seg.id, c.switches[0].id} <= H.ids("segments", env)
    ops = [{"op": "set", "id": seg.id, "field": "label", "value": "Web tier"},
           {"op": "set", "id": c.switches[0].id, "field": "sublabel", "value": "Uplinks A"},
           {"op": "move", "id": c.id, "index": 2},
           {"op": "delete", "id": c.switches[0].segments[1].id},
           {"op": "add", "parent": c.switches[0].id, "field": "segments", "index": 0,
            "element": {"id": "user:seg", "palette": "segment", "name": "Planned DMZ"}},
           {"op": "add", "parent": m.sites[0].id, "field": "clusters", "index": 0,
            "element": {"id": "user:fw", "palette": "gateway", "name": "Edge firewall"}}]
    payload, problems = H.edit("segments", env, ops)
    assert problems == []
    # A switch name is collected: it cannot be renamed.
    _, bad = H.edit("segments", env, [{"op": "set", "id": c.switches[0].id,
                                       "field": "label", "value": "x"}])
    assert bad
    scs = H.laid_out("segments", payload)
    H.assert_clean(scs)
    texts = [t for sc in scs for t in H.texts(sc)]
    # Compact rows put a segment's name and range on one line.
    assert any("Web tier" in t for t in texts) and any("Planned DMZ" in t for t in texts)
    assert "Edge firewall" in texts
    assert "USER-ADDED" not in texts
    # Replay is deterministic.
    assert H.edit("segments", env, ops)[0] == payload


def test_report_sections_and_edits_in_figures_only(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    heads, blocks = H.report_titles(env)
    assert any(h.endswith("Network Segment Design: md-pc01.demo.local") for h in heads)
    tables = [b for b in blocks if getattr(b, "caption", "").startswith("Network segments")]
    assert tables and tables[0].headers[0] == "Cluster"
    assert tables[0].headers[1] == "Prism Central"  # combined run: attributed


def test_prism_element_run_is_titled_with_the_element() -> None:
    env = Environment(source_type="prism_element")
    env.prism_central.host = "pe01.example.lab"
    env.clusters.append(Cluster(uuid="c", name="PE-01", hypervisor_types=["AHV"]))
    env.subnets.append(Subnet(uuid="s", name="vm-net", cluster_uuid="c", subnet_type="VLAN",
                              vlan_id=10, ip_prefix="192.0.2.0/24"))
    [site] = N1.segments_model(env).sites
    assert site.kind == "pe" and N1.site_line(site).startswith("Prism Element, 1 cluster")
    [sc] = H.scenes("segments", env)
    H.assert_clean([sc])
    assert "PRISM ELEMENT" in H.texts(sc) and "pe01.example.lab" in H.texts(sc)
    assert "No VLAN id carries two different prefixes in this cluster." in N1.bullets(site)


def test_compact_rows_never_clip_the_prefix(design_envs: dict[str, Any]) -> None:
    """A compact row reads ``prefix · name`` after the VLAN chip: no gateway, and only
    the name is shortened (enterprise en-pc01 clipped "nyc-db-01-mgmt · 10.22.3.0/24 ·
    gw…" mid-prefix before)."""
    env = design_envs["enterprise"]
    m = N1.segments_model(env)
    segs = [g for st in m.sites for c in st.clusters for w in c.switches for g in w.segments
            if g.kind == "subnet" and g.prefix]
    seg = next(g for g in segs if g.name == "nyc-db-01-mgmt")
    full = compact_text(seg, 1000.0)
    assert full == f"{seg.prefix} · nyc-db-01-mgmt" and "gw" not in full
    narrow = tx.width(f"{seg.prefix} · nyc-db", K.SIZE_SMALL, False)
    short = compact_text(seg, narrow)
    assert short.startswith(f"{seg.prefix} · ") and short.endswith(tx.ELLIPSIS)
    assert tx.width(short, K.SIZE_SMALL, False) <= narrow
    # In the laid-out figure every compact subnet row starts with its full prefix.
    by_id = {g.id: g for g in segs}
    rows = [(by_id[n.id], ln.text) for sc in H.scenes("segments", env)
            if sc.meta["level"] == 1 for n in sc.nodes if n.id in by_id for ln in n.lines]
    assert rows
    for g, text in rows:
        assert text.startswith(f"{g.prefix} · ") and "gw" not in text, text


def _report(env: Any, **opts: Any) -> Any:
    from ntnxvision_core.exporters import ExportOptions
    from ntnxvision_core.exporters.content import build_report

    r = build_report(env, ExportOptions(**opts), "Report")
    assert r.warnings == []
    return r


def _figures(r: Any, type_id: str) -> list[Any]:
    from ntnxvision_core.exporters.content import SceneFigure

    return [b for ch in r.chapters for s in ch.sections for b in s.blocks
            if isinstance(b, SceneFigure) and b.scene.type == type_id]


NET_TYPES = [("segments", "networking"), ("vpc", "networking"), ("flow", "security")]


@pytest.mark.parametrize(("type_id", "chapter"), NET_TYPES)
def test_left_out_or_reordered_chapters(design_envs: dict[str, Any], type_id: str,
                                        chapter: str) -> None:
    """A per-AZ figure follows its chapter: left out, it is in neither the report nor
    the diagrams bundle; moved, it is numbered in the chapter's new place."""
    from ntnxvision_core.diagrams.bundle import report_scenes
    from ntnxvision_core.exporters import sections

    env = design_envs["medium"]
    assert TYPES[type_id].applies(env)
    every = frozenset(s.id for s in sections.CATALOG)
    assert _figures(_report(env), type_id)
    out = _report(env, chapters=every - {chapter})
    assert not _figures(out, type_id)
    assert all(sc.type != type_id for sc in report_scenes(env, {"chapters": sorted(
        every - {chapter})}))
    assert any(sc.type == type_id for sc in report_scenes(env))
    order = (chapter, *(s for s in sections.DEFAULT_ORDER if s != chapter))
    moved = _report(env, section_order=order)
    figs = _figures(moved, type_id)
    num = next(ch.number for ch in moved.chapters if ch.key == chapter)
    assert figs and all(f.label.startswith(f"Figure {num}-") for f in figs)
    assert all(f.caption and "(edited)" not in f.caption for f in figs)


@pytest.mark.parametrize(("type_id", "chapter"), NET_TYPES)
def test_edit_disclosed_only_in_its_own_az(design_envs: dict[str, Any], type_id: str,
                                           chapter: str) -> None:
    """A rename in the first AZ: that AZ's section says "(edited)" and discloses it;
    the other AZs' sections are as generated."""
    from ntnxvision_core.diagrams.edits import walk

    env = design_envs["enterprise"]
    t = TYPES[type_id]
    gen = t.build_model(env)
    doc = t.to_doc(gen)
    first = gen.sites[0].id
    slots = walk(doc, t.edit_spec)

    def site_of(i: str | None) -> str | None:
        while i is not None and i != first:
            i = slots[i].parent if i in slots else None
        return i

    target = next(i for i, sl in slots.items() if site_of(i) == first and i != first
                  and (c := t.edit_spec.collection(sl.field)) is not None and c.label)
    payload, problems = H.edit(type_id, env, [{"op": "set", "id": target, "field": "label",
                                               "value": "Renamed by author"}])
    assert problems == []
    r = _report(env, diagram_models={type_id: t.from_doc(payload)})
    figs = _figures(r, type_id)
    mine = [f for f in figs if f.scene.meta["group"] == first]
    other = [f for f in figs if f.scene.meta["group"] != first]
    assert mine and other
    assert all("(edited)" in f.caption for f in mine)
    assert any(b.startswith("Figure edited") and "Renamed by author" in b
               for b in mine[0].summary)
    assert all("(edited)" not in f.caption for f in other)
    assert not any(b.startswith("Figure edited") for f in other for b in f.summary)
