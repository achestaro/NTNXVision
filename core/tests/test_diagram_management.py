"""Management Plane Design (A3): facts, gating, determinism, golden scenes, overflow,
scaling and the report section."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from ntnxvision_core.diagrams import TYPES, check_scene, model_envelope, model_from_envelope
from ntnxvision_core.diagrams import management as M
from ntnxvision_core.diagrams import scene as S
from ntnxvision_core.diagrams.layout.conceptual import Page
from ntnxvision_core.diagrams.layout.management import layout
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.models import Environment

from .diagram_helpers import shuffled as _shuffled

GOLDEN = Path(__file__).parent / "golden" / "diagrams"
UPDATE = os.environ.get("NTNXVISION_UPDATE_GOLDEN") == "1"
EM_DASH = chr(0x2014)


def _scenes(env: Environment) -> list[S.Scene]:
    return layout(M.management_model(env))


def _texts(sc: S.Scene) -> list[str]:
    return [t.text for n in sc.nodes for t in n.lines] + \
        [t.text for e in sc.edges for lb in e.labels for t in lb.lines]




# ─── facts ─────────────────────────────────────────────────────────────


def test_enterprise_scale_out_pc_hosting_and_pairings(design_envs: dict[str, Any]) -> None:
    m = M.management_model(design_envs["enterprise"])
    nyc = m.sites[0]
    assert nyc.pc is not None and nyc.pc.vms == 3 and nyc.pc.size == "Large"
    assert (nyc.pc.vm_vcpus, nyc.pc.vm_mem_gib, nyc.pc.virtual_ip) == (10, 44, "10.10.0.50")
    assert nyc.pc.hosting == ["NYC-MGMT-01"] and nyc.clusters[0].hosts_pc
    assert nyc.pc.ncc == "5.1.0" and nyc.pc.lcm
    assert {r.name for r in m.remotes} == {"dr-apac", "Xi-EU-West"}
    assert all(lk.connected for lk in m.links)
    vc = [c for c in nyc.clusters if c.vcenter]
    assert [c.hypervisor for c in vc] == ["ESXi"]
    assert "DR" in nyc.pc.products


def test_pairing_not_connected_is_named() -> None:
    m = M.ManagementModel(
        sites=[M.MgmtSite(id="site:a.example.lab", name="a.example.lab",
                          pc=M.MgmtPc(id="site:a.example.lab:pc", version="pc.7.5"),
                          clusters=[M.MgmtCluster(id="cluster:1", name="CL-1", hosts_pc=True,
                                                  pe_ip="192.0.2.10")], registered=1)],
        remotes=[M.MgmtRemote(id="az:dr", name="dr")],
        links=[M.MgmtLink(id="link:pairing:a->dr", a="site:a.example.lab", b="az:dr",
                          connected=False, state="pending")])
    sc = layout(m)[0]
    assert "AZ pairing, pending" in _texts(sc)
    assert check_scene(sc) == []
    assert any("pending" in b for b in M.bullets(m))


def test_applies_only_to_prism_central_runs(design_envs: dict[str, Any]) -> None:
    t = TYPES["management"]
    assert (t.chapter, t.section, t.slug) == ("prism_central", "Management Plane Design",
                                              "management-plane")
    assert t.applies(design_envs["small"])
    assert not t.applies(Environment())
    pe = design_envs["small"].model_copy(update={"source_type": "prism_element"})
    assert not t.applies(pe)


# ─── determinism ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["small", "medium", "enterprise", "xlarge", "multi3"])
def test_twice_and_shuffled_identical(design_envs: dict[str, Any], name: str) -> None:
    base = [S.scene_json(x) for x in _scenes(design_envs[name])]
    assert [S.scene_json(x) for x in _scenes(design_envs[name])] == base
    if name in ("medium", "enterprise", "multi3"):
        for seed in (1, 2, 3):
            assert [S.scene_json(x) for x in _scenes(_shuffled(design_envs[name], seed))] \
                == base


def test_envelope_round_trip(design_envs: dict[str, Any]) -> None:
    m = M.management_model(design_envs["multi3"])
    doc = json.loads(json.dumps(model_envelope("management", m)))
    type_id, again = model_from_envelope(doc)
    assert type_id == "management" and again == m
    assert M.model_from_json(M.model_json(m)) == m
    assert M.model_hash(again) == M.model_hash(m)


# ─── golden ────────────────────────────────────────────────────────────


def _pretty(sc: S.Scene) -> bytes:
    return (json.dumps(S.to_doc(sc), sort_keys=True, indent=1, ensure_ascii=False)
            + "\n").encode()


@pytest.mark.parametrize("name", ["small", "medium"])
def test_golden_scene_and_svg(design_envs: dict[str, Any], name: str) -> None:
    scenes = _scenes(design_envs[name])
    for suffix, got in (("scene.json", b"".join(_pretty(x) for x in scenes)),
                        ("svg", b"".join(render_svg(x) for x in scenes))):
        path = GOLDEN / f"management-{name}.{suffix}"
        if UPDATE or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(got)
        assert got == path.read_bytes(), (
            f"{path.name} changed; rerun with NTNXVISION_UPDATE_GOLDEN=1 after reviewing")


# ─── overflow and scaling ──────────────────────────────────────────────


@pytest.mark.parametrize("name", ["small", "medium", "enterprise", "xlarge", "multi3",
                                  "pc01", "large1"])
def test_no_overflow_and_page_fit(design_envs: dict[str, Any], name: str) -> None:
    page = Page()
    for sc in _scenes(design_envs[name]):
        assert check_scene(sc) == [], sc.id
        assert sc.width <= page.width and sc.height <= page.max_h
        assert all(EM_DASH not in t for t in _texts(sc))


def _stress() -> M.ManagementModel:
    sites = []
    for i in range(7):
        sid = f"site:pc{i}.example.lab"
        clusters = [M.MgmtCluster(
            id=f"cluster:{i}-{k}", name=f"EMEA-FRANKFURT-DATACENTER-{i}-CLUSTER-{k:02d}-PROD",
            hypervisor="ESXi" if k % 5 == 0 else "AHV", pe_ip=f"198.51.100.{k + 1}",
            dsip=f"198.51.100.{k + 101}", aos="7.0.1", hosts_pc=k == 0,
            vcenter="vcenter.example.lab" if k % 5 == 0 else None) for k in range(40)]
        sites.append(M.MgmtSite(id=sid, name=f"pc{i}.example.lab",
                                pc=M.MgmtPc(id=f"{sid}:pc", version="pc.7.5.1", vms=3,
                                            size="X-Large", vm_vcpus=14, vm_mem_gib=60,
                                            virtual_ip="192.0.2.5",
                                            products=["DR", "Flow Security", "NCM"]),
                                clusters=clusters, registered=40))
    links = [M.MgmtLink(id=f"link:pairing:{sites[i].id}->{sites[i + 1].id}", a=sites[i].id,
                        b=sites[i + 1].id) for i in range(0, 6, 2)]
    links.append(M.MgmtLink(id="link:pairing:x->az:vault", a=sites[6].id, b="az:vault"))
    return M.ManagementModel(sites=sites, remotes=[M.MgmtRemote(id="az:vault", name="vault")],
                             links=links)


def test_stress_model_splits_and_summarises_without_overflow() -> None:
    m = _stress()
    scenes = layout(m)
    assert len(scenes) >= 3
    assert all(check_scene(sc) == [] and sc.height <= Page().max_h for sc in scenes)
    assert any(sc.meta["summarised"] for sc in scenes)
    drawn = {c for sc in scenes for c in sc.meta["drawn"] + sc.meta["summarised"]}
    assert drawn == {c.id for s in m.sites for c in s.clusters}
    # Paired zones share a part.
    part = {sid: sc.part for sc in scenes for sid in sc.meta["sites"]}
    assert part[m.sites[0].id] == part[m.sites[1].id]


# ─── report ────────────────────────────────────────────────────────────


def test_reports_contain_the_section(design_envs: dict[str, Any], tmp_path: Path) -> None:
    from docx import Document

    from ntnxvision_core.exporters import export_environment

    env = design_envs["multi3"]
    docx = export_environment(env, "word", tmp_path, "m")
    text = "\n".join(p.text for p in Document(str(docx)).paragraphs)
    assert "Management Plane Design" in text
    assert "Management plane, part 1 of" in text or "Management plane:" in text
    pdf = export_environment(env, "pdf", tmp_path, "m")
    assert pdf.stat().st_size > 0
