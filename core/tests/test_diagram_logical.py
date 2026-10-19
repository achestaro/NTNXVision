"""Logical Cluster Design (A2) and the estate map (A6): facts, layout, determinism,
golden scenes, overflow, registry gating and report placement.

The estates come from the session fixture `design_envs` (conftest): the scenario estates
and the demo PCs collected once through the mock API with the real collectors.
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fpdf import FPDF

from ntnxvision_core.diagrams import TYPES, check_scene, model_envelope, model_from_envelope
from ntnxvision_core.diagrams import estate as E
from ntnxvision_core.diagrams import logical as L
from ntnxvision_core.diagrams import scene as S
from ntnxvision_core.diagrams.facts import conceptual_model
from ntnxvision_core.diagrams.layout.conceptual import Page
from ntnxvision_core.diagrams.layout.conceptual import layout as conceptual_layout
from ntnxvision_core.diagrams.layout.estate import layout as estate_layout
from ntnxvision_core.diagrams.layout.logical import layout, layout_site
from ntnxvision_core.diagrams.render.pdf import draw_scene
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.models import Environment

from .diagram_helpers import shuffled as _shuffled

GOLDEN = Path(__file__).parent / "golden" / "diagrams"
UPDATE = os.environ.get("NTNXVISION_UPDATE_GOLDEN") == "1"
EM_DASH = chr(0x2014)
SCENARIOS = ("small", "medium", "enterprise", "xlarge")


def _scenes(env: Environment) -> list[S.Scene]:
    return layout(L.logical_model(env), Page())


def _texts(sc: S.Scene) -> list[str]:
    return [t.text for n in sc.nodes for t in n.lines] + \
        [t.text for e in sc.edges for lb in e.labels for t in lb.lines]




def _pdf_bytes(sc: S.Scene) -> bytes:
    pdf = FPDF(orientation="L", unit="pt", format=(sc.height + 20, sc.width + 20))
    pdf.set_creation_date(datetime(2026, 10, 1, tzinfo=timezone.utc))
    pdf.add_page()
    draw_scene(pdf, sc, 10, 10)
    return bytes(pdf.output())


def _pretty(sc: S.Scene) -> bytes:
    return (json.dumps(S.to_doc(sc), sort_keys=True, indent=1, ensure_ascii=False)
            + "\n").encode()


def _golden(name: str, scenes: list[S.Scene]) -> None:
    for suffix, got in (("scene.json", b"".join(_pretty(x) for x in scenes)),
                        ("svg", b"".join(render_svg(x) for x in scenes))):
        path = GOLDEN / f"{name}.{suffix}"
        if UPDATE or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(got)
        assert got == path.read_bytes(), (
            f"{path.name} changed; rerun with NTNXVISION_UPDATE_GOLDEN=1 after reviewing")


# ─── facts ─────────────────────────────────────────────────────────────


def test_pods_pc_host_and_flasharray(design_envs: dict[str, Any]) -> None:
    m = L.logical_model(design_envs["pc02"])
    (site,) = m.sites
    assert [p.name for p in site.pods] == ["SEA-1", "SEA-2"]
    prod1 = next(c for c in site.clusters if c.name == "SEA-PROD-01")
    assert prod1.hosts_pc and prod1.pc_vms == 3 and prod1.pc_size == "Large"
    assert L.pc_role_text(prod1) == "Runs Prism Central: 3 PCVMs, Large"
    (arr,) = site.arrays
    assert arr.product == "Pure FlashArray" and arr.protocol == "NVMe/TCP"
    assert arr.cluster_name == "SEA-PROD-02"
    ext = [ct for c in site.clusters for ct in c.containers if ct.array]
    assert [ct.array for ct in ext] == [arr.id]
    assert "external" in L.container_flags(ext[0], "SEA-PROD-02")
    robo = next(c for c in site.clusters if c.name == "SEA-ROBO-01")
    assert L.headline(robo).endswith("RF2 (disk)")
    rows = L.container_rows(site)
    assert [r[6] for r in rows if r[1] == ext[0].name] == ["Pure FlashArray fa-sea-prod-02"]


def test_blocks_racks_and_node_groups(design_envs: dict[str, Any]) -> None:
    site = L.logical_model(design_envs["large1"]).sites[0]
    prod_a = next(c for c in site.clusters if c.name == "PROD-A")
    assert (prod_a.blocks, prod_a.per_block, prod_a.racks) == (4, 4, [1, 2, 3])
    assert L.blocks_text(prod_a) == "4 blocks of 4 nodes, racks 1, 2 and 3"
    assert L.fault_text(prod_a) == "Fault domain: rack, tolerates 2 failures"
    # Nodes differing only in memory share a group; memory reads as a range.
    (g,) = prod_a.groups
    assert g.count == 16 and L.group_lines(g)[1].endswith("512 to 1,024 GiB")
    prod_c = next(c for c in site.clusters if c.name == "PROD-C")
    assert len(prod_c.groups) == 2 and L.cvm_text(prod_c) == "CVM: 12 to 16 vCPU, 48 GiB"


def test_cpu_label_and_prefix_free_chips() -> None:
    assert L.cpu_label("Intel(R) Xeon(R) Gold 6438Y+ CPU @ 2.00GHz") == "Xeon Gold 6438Y+"
    assert L.cpu_label("AMD EPYC 9354 32-Core Processor") == "EPYC 9354 32-Core"
    ct = L.Container(id="c", name="prod-a-ctr-images", rf=3, dedup=True)
    assert L.container_flags(ct, "PROD-A") == "ctr-images · RF3 · dedup"
    assert L.container_flags(ct) == "prod-a-ctr-images · RF3 · dedup"


def test_bullets_state_only_collected_facts(design_envs: dict[str, Any]) -> None:
    site = L.logical_model(design_envs["pc02"]).sites[0]
    b = L.logical_bullets(site)
    assert b[0].startswith("4 clusters in 2 PODs (from the POD category), 28 nodes on AHV")
    assert "SEA-PROD-01 hosts Prism Central (3 PCVMs) and Files, 3 FSVMs." in b
    assert any("Pure FlashArray fa-sea-prod-02 over NVMe/TCP" in x for x in b)
    assert 2 <= len(b) <= L.MAX_BULLETS


def test_registry_and_applies(design_envs: dict[str, Any]) -> None:
    t = TYPES["logical"]
    assert (t.chapter, t.section, t.slug) == ("logical_design", "Logical Cluster Design",
                                              "logical-design")
    assert t.applies(design_envs["small"]) and not t.applies(Environment())
    e = TYPES["estate"]
    assert e.embedded and e.slug == "estate-map"
    assert e.applies(design_envs["xlarge"]) and e.applies(design_envs["enterprise"])
    assert not e.applies(design_envs["small"]) and not e.applies(design_envs["medium"])
    assert not e.applies(Environment())


def test_envelope_round_trip(design_envs: dict[str, Any]) -> None:
    for tid, env in (("logical", design_envs["enterprise"]),
                     ("estate", design_envs["xlarge"])):
        m = TYPES[tid].build_model(env)
        doc = json.loads(json.dumps(model_envelope(tid, m)))
        type_id, again = model_from_envelope(doc)
        assert type_id == tid and again == m
        assert [S.scene_json(x) for x in TYPES[tid].layout(again)] == \
            [S.scene_json(x) for x in TYPES[tid].layout(m)]


# ─── layout ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", [*SCENARIOS, "pc02", "large1", "large2", "multi3"])
def test_no_overflow_and_house_rules(design_envs: dict[str, Any], name: str) -> None:
    page = Page()
    for sc in _scenes(design_envs[name]):
        assert check_scene(sc) == [], sc.id
        assert sc.width <= page.width and sc.height <= page.max_h, sc.id
        assert all(EM_DASH not in t for t in _texts(sc))


def test_every_cluster_drawn_once_across_parts(design_envs: dict[str, Any]) -> None:
    m = L.logical_model(design_envs["xlarge"])
    for site in m.sites:
        scenes = layout_site(site, Page())
        drawn = [cid for sc in scenes for cid in sc.meta["drawn"]]
        assert sorted(drawn) == sorted(c.id for c in site.clusters)
        assert [sc.part for sc in scenes] == [(i + 1, len(scenes)) for i in range(len(scenes))]


def test_external_container_is_joined_to_its_array(design_envs: dict[str, Any]) -> None:
    (sc,) = [x for x in _scenes(design_envs["large2"]) if x.part[0] == 1]
    (e,) = [e for e in sc.edges if e.data.get("kind") == "array"]
    assert sc.node(e.src).kind == "strip" and "PowerFlex" in sc.node(e.dst).text
    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(e.points, e.points[1:],
                                                             strict=False))


def test_first_part_budget_splits_without_shrinking(design_envs: dict[str, Any]) -> None:
    site = L.logical_model(design_envs["enterprise"]).sites[0]
    tight = layout_site(site, Page(), lambda _w: 300.0)
    assert len(tight) > 1 and tight[0].height <= 300.0
    assert all(t.size >= 6.0 for sc in tight for n in sc.nodes for t in n.lines)


def _stress() -> L.LogicalModel:
    long = "EMEA-FRANKFURT-DC2-PRODUCTION-GENERAL-PURPOSE-COMPUTE-CLUSTER-"
    clusters = []
    for i in range(14):
        groups = [L.NodeGroup(count=8, model=f"VERY-LONG-HARDWARE-MODEL-NAME-G{k}",
                              cpu="Xeon Platinum 8490H", sockets=2, cores=120, mem_gib=2048,
                              mem_max_gib=4096) for k in range(4)]
        clusters.append(L.LCluster(
            id=f"cluster:{i:02d}", name=f"{long}{i:02d}", hypervisor="AHV", aos="7.5",
            nodes=32, rf=3, fault_domain="rack", tolerates=2, groups=groups, blocks=8,
            per_block=4, racks=[1, 2, 3, 4], cvm_vcpus=[12, 16], cvm_mem_gib=[48, 64],
            hosts_pc=i == 0, pc_vms=3, pc_size="X-Large",
            services=["Files, 6 FSVMs", "Objects, 4 workers", "NDB, 3 DB servers"],
            containers=[L.Container(id=f"cluster:{i:02d}:ctr:{k}",
                                    name=f"container-with-a-really-long-name-{k}", rf=3,
                                    compression=True, dedup=True, ecx=True,
                                    array="arr" if k == 0 and i < 3 else None)
                        for k in range(9)]))
    pods = [L.Pod(id=f"site:x:pod:{k}", name=f"POD-WITH-LONG-NAME-{k}",
                  clusters=clusters[k * 5:(k + 1) * 5]) for k in range(3)]
    for c in clusters[:3]:
        for ct in c.containers:
            if ct.array:
                ct.array = f"{c.id}:ext:a"
    arrays = [L.Array(id=f"{c.id}:ext:a", product="Pure FlashArray", name="fa-" + c.name,
                      endpoint="198.51.100.40", protocol="NVMe/TCP", total_bytes=10**15,
                      free_bytes=10**14, health="healthy", cluster=c.id, cluster_name=c.name,
                      containers=[c.containers[0].name]) for c in clusters[:3]]
    return L.LogicalModel(sites=[L.LSite(id="site:x", name="x" * 70 + ".example.lab",
                                         pods=pods, arrays=arrays, pc_vms=3)])


def test_stress_model_never_overflows() -> None:
    scenes = layout(_stress(), Page())
    assert len(scenes) > 1
    for sc in scenes:
        assert check_scene(sc) == [], sc.id
        assert sc.height <= Page().max_h


# ─── determinism and golden ────────────────────────────────────────────


@pytest.mark.parametrize("name", SCENARIOS)
def test_render_twice_identical(design_envs: dict[str, Any], name: str) -> None:
    a, b = _scenes(design_envs[name]), _scenes(design_envs[name])
    assert [S.scene_json(x) for x in a] == [S.scene_json(x) for x in b]
    assert [render_svg(x) for x in a] == [render_svg(x) for x in b]
    assert [_pdf_bytes(x) for x in a[:2]] == [_pdf_bytes(x) for x in b[:2]]


@pytest.mark.parametrize("name", ["medium", "enterprise", "pc02"])
def test_shuffled_api_order_gives_identical_scenes(design_envs: dict[str, Any],
                                                   name: str) -> None:
    base = [S.scene_json(x) for x in _scenes(design_envs[name])]
    for seed in (1, 2, 3):
        assert [S.scene_json(x) for x in _scenes(_shuffled(design_envs[name], seed))] == base


@pytest.mark.parametrize("name", ["small", "medium"])
def test_golden_scene_and_svg(design_envs: dict[str, Any], name: str) -> None:
    _golden(f"logical-{name}", _scenes(design_envs[name]))


# ─── estate map (A6) ───────────────────────────────────────────────────


def _estate(env: Environment) -> tuple[E.EstateModel, S.Scene]:
    m = E.estate_model(env)
    return m, estate_layout(m, Page(), E.EstateRefs(tuple(f"Figure 5-{i + 2}"
                                                          for i in range(len(m.parts)))))[0]


def test_estate_tiles_follow_the_conceptual_parts(design_envs: dict[str, Any]) -> None:
    env = design_envs["xlarge"]
    m, sc = _estate(env)
    parts = [sc_.meta["sites"] for sc_ in conceptual_layout(conceptual_model(env))]
    assert m.parts == parts and len(m.tiles) == 8
    tile = next(t for t in m.tiles if t.id == parts[1][0])
    assert "Detail: Figure 5-3" in sc.node(f"{tile.id}:detail").text
    assert sc.caption.startswith("Estate map: 8 availability zones, grouped into the parts "
                                 "drawn in Figures 5-2,")
    frames = [n for n in sc.nodes if n.kind == "container"]
    assert len(frames) == len(parts)
    assert all(n.parent == f"estate:part:{i + 1}" for i, p in enumerate(parts)
               for n in sc.nodes if n.id in p)


def test_estate_hypervisor_bar_and_remote_bus(design_envs: dict[str, Any]) -> None:
    m, sc = _estate(design_envs["enterprise"])
    nyc = m.tiles[0]
    assert nyc.hyp_clusters[0] == ["AHV", 4]
    bars = [n for n in sc.nodes if n.id.startswith(f"{nyc.id}:bar:")]
    assert {n.data["hypervisor"] for n in bars} == {"AHV", "ESXi"}
    remotes = [n for n in sc.nodes if n.id.startswith("az:") and n.kind == "box"]
    assert {n.data["name"] for n in remotes} == {"Xi-EU-West", "dr-apac"}
    assert any(e.dst.startswith("az:") for e in sc.edges)


@pytest.mark.parametrize("name", ["enterprise", "xlarge"])
def test_estate_no_overflow_determinism_and_shuffle(design_envs: dict[str, Any],
                                                    name: str) -> None:
    env = design_envs[name]
    _, sc = _estate(env)
    assert check_scene(sc) == [] and sc.height <= Page().max_h
    assert S.scene_json(_estate(env)[1]) == S.scene_json(sc)
    for seed in (1, 2):
        assert S.scene_json(_estate(_shuffled(env, seed))[1]) == S.scene_json(sc)


def test_estate_golden(design_envs: dict[str, Any]) -> None:
    _golden("estate-enterprise", [_estate(design_envs["enterprise"])[1]])


# ─── reports ───────────────────────────────────────────────────────────


def _opts() -> Any:
    from ntnxvision_core.exporters import ExportOptions

    return ExportOptions()


def _report(env: Environment) -> Any:
    from ntnxvision_core.exporters.content import build_report

    return build_report(env, _opts(), eyebrow="Test")


def test_report_places_logical_sections_after_the_overview(design_envs: dict[str, Any]
                                                           ) -> None:
    from ntnxvision_core.exporters.content import SceneFigure, Table

    rep = _report(design_envs["medium"])
    ch = next(c for c in rep.chapters if c.key == "logical_design")
    titles = [s.title for s in ch.sections]
    assert titles[:3] == ["Solution Conceptual Overview",
                          "Logical Cluster Design: md-pc01.demo.local",
                          "Logical Cluster Design: md-pc02.demo.local"]
    sec = ch.sections[1]
    figs = [b for b in sec.blocks if isinstance(b, SceneFigure)]
    assert figs[0].summary and figs[0].scene.type == "logical"
    assert figs[0].summary_beside  # 480 pt wide: summary beside (decision 3)
    (table,) = [b for b in sec.blocks if isinstance(b, Table)]
    assert table.headers[0] == "Cluster" and "Backing storage" in table.headers


def test_report_opens_the_overview_on_the_estate_map(design_envs: dict[str, Any]) -> None:
    from ntnxvision_core.exporters.content import SceneFigure, Table

    rep = _report(design_envs["enterprise"])
    ch = next(c for c in rep.chapters if c.key == "logical_design")
    sec = ch.sections[0]
    assert sec.title == "Solution Conceptual Overview"
    first, second = sec.blocks[0], sec.blocks[1]
    assert isinstance(first, SceneFigure) and first.scene.type == "estate" and first.summary
    assert isinstance(second, Table) and second.headers[-1] == "Drawn in"
    parts = [b for b in sec.blocks[2:] if isinstance(b, SceneFigure)]
    assert parts and all(not f.summary for f in parts)
    labels = [f.label.rstrip(".") for f in parts]
    assert all(lab in first.caption for lab in [x.removeprefix("Figure ") for x in labels])
    assert any(str(r[-1]).startswith(labels[0]) for r in second.rows)


def test_word_and_pdf_carry_the_new_figures(design_envs: dict[str, Any], tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from ntnxvision_core.exporters import export_environment
    from ntnxvision_core.exporters import pdf as pdf_mod

    env = design_envs["pc02"]
    docx = export_environment(env, "word", tmp_path, "pc02")
    with zipfile.ZipFile(docx) as z:
        body = z.read("word/document.xml").decode()
    assert "Logical Cluster Design" in body and "Logical cluster design, pc02.demo.local" in body
    drawn: list[str] = []
    real = pdf_mod.draw_scene

    def spy(pdf: Any, sc: S.Scene, *a: Any, **kw: Any) -> float:
        drawn.append(sc.type)
        return real(pdf, sc, *a, **kw)

    monkeypatch.setattr(pdf_mod, "draw_scene", spy)
    out = pdf_mod.PdfExporter().export(env, tmp_path / "r.pdf")
    assert out.stat().st_size > 20_000
    assert drawn.count("logical") == len(_scenes(env)) or drawn.count("logical") >= 1
    assert drawn.index("conceptual") < drawn.index("logical")
