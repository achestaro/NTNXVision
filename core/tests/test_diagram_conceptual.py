"""Solution Conceptual Overview: facts, layout, renderers, scaling, reports.

The scenario estates (`synthetic.profiles.SCENARIOS`) are collected once per session
through the mock API with the real collectors, so these tests see exactly what a demo
run sees.
"""

from __future__ import annotations

import io
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fpdf import FPDF
from PIL import Image

from ntnxvision_core.diagrams import check_scene, conceptual_layout, conceptual_model
from ntnxvision_core.diagrams import model as M
from ntnxvision_core.diagrams import scene as S
from ntnxvision_core.diagrams.layout.conceptual import Page, Refs
from ntnxvision_core.diagrams.render.pdf import draw_scene
from ntnxvision_core.diagrams.render.png import render_png
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.diagrams.summary import conceptual_bullets
from ntnxvision_core.models import Environment

GOLDEN = Path(__file__).parent / "golden" / "diagrams"
UPDATE = os.environ.get("NTNXVISION_UPDATE_GOLDEN") == "1"
EM_DASH = chr(0x2014)


@pytest.fixture(scope="session")
def estates(_demo_state: Any) -> dict[str, Environment]:
    from ntnxvision_core.synthetic import demo
    from ntnxvision_core.synthetic.scenarios import SCENARIOS, collect_scenario

    demo.reset()
    out = {name: collect_scenario(name) for name in SCENARIOS}
    demo.reset()
    return out


def _scenes(env: Environment, page: Page | None = None) -> list[S.Scene]:
    return conceptual_layout(conceptual_model(env), page)


def _all_text(sc: S.Scene) -> list[str]:
    return [t.text for n in sc.nodes for t in n.lines] + \
        [t.text for e in sc.edges for lb in e.labels for t in lb.lines]


def _pdf_bytes(sc: S.Scene) -> bytes:
    pdf = FPDF(orientation="L", unit="pt", format=(sc.height + 20, sc.width + 20))
    pdf.set_creation_date(datetime(2026, 10, 1, tzinfo=timezone.utc))
    pdf.add_page()
    draw_scene(pdf, sc, 10, 10)
    return bytes(pdf.output())


# ─── facts ─────────────────────────────────────────────────────────────


def test_medium_pairs_two_sites_and_replicates_both_ways(estates: dict[str, Environment]
                                                         ) -> None:
    m = conceptual_model(estates["medium"])
    assert [s.name for s in m.sites] == ["md-pc01.demo.local", "md-pc02.demo.local"]
    assert m.remotes == []  # the paired PC is a collected site, not a remote AZ
    kinds = {lk.kind: lk for lk in m.links}
    assert set(kinds) == {"pairing", "replication"}
    rep = kinds["replication"]
    assert rep.both
    assert [(r.type, r.rpo_seconds) for r in rep.repl] == [("NearSync", 60), ("Async", 3600)]
    hyps = sorted(c.hypervisor or "" for s in m.sites for c in s.cards)
    assert hyps.count("ESXi") == 2 and hyps.count("AHV") == 4
    # The cluster hosting the PC comes first and carries the role.
    assert all(s.cards[0].hosts_pc for s in m.sites)
    assert any("vCenter" in c.services for s in m.sites for c in s.cards)


def test_enterprise_scale_out_remote_and_xi(estates: dict[str, Environment]) -> None:
    m = conceptual_model(estates["enterprise"])
    nyc = m.sites[0]
    assert nyc.pc is not None and nyc.pc.vms == 3
    assert nyc.cards[0].name == "NYC-MGMT-01" and nyc.cards[0].hosts_pc
    assert {r.name: r.kind for r in m.remotes} == {"dr-apac": "pc", "Xi-EU-West": "xi"}
    vendors = {c.models[0].split()[0][:2] for s in m.sites for c in s.cards}
    assert len(vendors) >= 4


def test_inbound_policy_points_at_the_site() -> None:
    """A policy replicating from a remote AZ into this PC (as on the lab) is drawn from
    the remote to the site, and the summary says so."""
    from ntnxvision_core.models import (
        AvailabilityZone,
        Cluster,
        PrismCentral,
        ProtectionPolicy,
    )

    env = Environment(prism_central=PrismCentral(host="pc.example.lab", uuid="u-local"))
    env.clusters = [Cluster(uuid="c1", name="CL-01", hypervisor_types=["AHV"], num_nodes=3)]
    env.availability_zones = [
        AvailabilityZone(name="Local AZ", url="u-local"),
        AvailabilityZone(name="dr-site", url="u-remote", region="OnPrem"),
    ]
    env.protection_policies = [ProtectionPolicy(uuid="p1", name="in", rpo_seconds=3600,
                                                replication_type="ASYNC",
                                                source_az="dr-site", target_az="Local AZ")]
    m = conceptual_model(env)
    rep = next(lk for lk in m.links if lk.kind == "replication")
    assert (rep.a, rep.b) == ("az:dr-site", "site:pc.example.lab")
    bullets = conceptual_bullets(m, conceptual_layout(m))
    assert any(b.startswith("dr-site, outside this inventory, replicates to pc") for b in bullets)
    assert not check_scene(conceptual_layout(m)[0])


def test_prism_element_mode_has_no_pc_block() -> None:
    from ntnxvision_core.models import Cluster, PrismCentral

    env = Environment(source_type="prism_element",
                      prism_central=PrismCentral(host="pe.example.lab"))
    env.clusters = [Cluster(uuid="c1", name="PE-CL", hypervisor_types=["AHV"], num_nodes=4)]
    m = conceptual_model(env)
    assert m.sites[0].kind == "pe" and m.sites[0].pc is None and not m.links
    sc = conceptual_layout(m)[0]
    assert "PRISM ELEMENT" in _all_text(sc)
    assert not check_scene(sc)


# ─── determinism ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["small", "medium", "enterprise"])
def test_render_twice_identical(estates: dict[str, Environment], name: str) -> None:
    a, b = _scenes(estates[name]), _scenes(estates[name])
    assert [S.scene_json(x) for x in a] == [S.scene_json(x) for x in b]
    assert [render_svg(x) for x in a] == [render_svg(x) for x in b]
    assert [_pdf_bytes(x) for x in a] == [_pdf_bytes(x) for x in b]
    pa, pb = render_png(a[0], dpi=96), render_png(b[0], dpi=96)
    assert Image.open(io.BytesIO(pa)).tobytes() == Image.open(io.BytesIO(pb)).tobytes()


def _shuffled(env: Environment, seed: int) -> Environment:
    r = random.Random(seed)
    out = env.model_copy(deep=True)
    for name in type(out).model_fields:
        value = getattr(out, name)
        if isinstance(value, list):
            r.shuffle(value)
    return out


@pytest.mark.parametrize("name", ["medium", "enterprise"])
def test_shuffled_api_order_gives_identical_scenes(estates: dict[str, Environment],
                                                   name: str) -> None:
    base = [S.scene_json(x) for x in _scenes(estates[name])]
    for seed in (1, 2, 3):
        assert [S.scene_json(x) for x in _scenes(_shuffled(estates[name], seed))] == base


def test_model_and_scene_documents_round_trip(estates: dict[str, Environment]) -> None:
    m = conceptual_model(estates["enterprise"])
    doc = json.loads(M.model_json(m))
    again = M.from_doc(doc)
    assert again == m and M.model_hash(again) == M.model_hash(m)
    scenes = conceptual_layout(again)
    for sc in scenes:
        assert S.scene_json(S.from_doc(json.loads(S.scene_json(sc)))) == S.scene_json(sc)


def test_edited_model_lays_out_like_a_generated_one(estates: dict[str, Environment]) -> None:
    """The Diagrams editor's contract: rename, reorder, add a user component, delete."""
    m = conceptual_model(estates["medium"])
    site = m.sites[0]
    site.cards[1].name = "Renamed cluster"
    site.cards.reverse()
    site.cards.append(M.Card(id="user:7f2c", name="Backup appliance", kind="user",
                             note="Added by the architect"))
    m.sites[1].cards.pop()
    sc = conceptual_layout(M.from_doc(json.loads(M.model_json(m))))[0]
    texts = _all_text(sc)
    assert "Renamed cluster" in texts and "USER-ADDED" in texts
    assert not check_scene(sc)


# ─── golden scenes ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["small", "medium"])
def test_golden_scene(estates: dict[str, Environment], name: str) -> None:
    got = b"".join(S.scene_json(x) for x in _scenes(estates[name]))
    path = GOLDEN / f"conceptual-{name}.scene.json"
    if UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(got)
    assert got == path.read_bytes(), (
        f"{path.name} changed; rerun with NTNXVISION_UPDATE_GOLDEN=1 after reviewing")


# ─── overflow and scaling ──────────────────────────────────────────────


@pytest.mark.parametrize("name", ["small", "medium", "enterprise", "xlarge"])
def test_no_text_exceeds_its_box(estates: dict[str, Environment], name: str) -> None:
    page = Page()
    for sc in _scenes(estates[name], page):
        assert check_scene(sc) == [], sc.id
        assert sc.width <= page.width
        assert all(EM_DASH not in t for t in _all_text(sc))


def test_xlarge_splits_into_parts_and_loses_no_cluster(estates: dict[str, Environment]
                                                       ) -> None:
    env = estates["xlarge"]
    m = conceptual_model(env)
    page = Page()
    scenes = conceptual_layout(m, page, Refs(clusters_table="Table 5-2"))
    assert [sc.part for sc in scenes] == [(1, 3), (2, 3), (3, 3)]
    assert all(sc.height <= page.max_h for sc in scenes)
    seen: list[str] = []
    for sc in scenes:
        seen += sc.meta["drawn"] + sc.meta["summarised"]
    assert sorted(seen) == sorted(c.id for s in m.sites for c in s.cards)
    assert len(seen) == 104
    texts = [t for sc in scenes for t in _all_text(sc)]
    assert any(t.startswith("+") and "more clusters" in t for t in texts)
    assert any("Table 5-2" in t for t in texts)
    # The chain 1-2-3-4 is larger than a part: the link to xl-pc04 leaves part 1 as a
    # reference box, and the pairs 5-6 and 7-8 stay together.
    assert scenes[0].meta["sites"][:3] == [f"site:xl-pc0{i}.demo.local" for i in (1, 2, 3)]
    assert any(n.id == "site:xl-pc04.demo.local" and n.kind == "box"
               for n in scenes[0].nodes)


def test_paired_sites_share_a_part(estates: dict[str, Environment]) -> None:
    parts = [sc.meta["sites"] for sc in _scenes(estates["enterprise"])]
    assert parts == [["site:en-pc01.demo.local", "site:en-pc02.demo.local"],
                     ["site:en-pc03.demo.local", "site:en-pc04.demo.local"]]


def _stress_model() -> M.ConceptualModel:
    sites, links = [], []
    for i in range(7):
        host = f"stress{i + 1:02d}-region-with-a-long-management-name.example.lab"
        cards = [M.Card(id=f"cluster:s{i}-{j}",
                        name=f"STRESS-{i}-CLUSTER-WITH-A-VERY-LONG-NAME-{j:02d}-PRODUCTION",
                        hypervisor="AHV" if j % 3 else "ESXi", nodes=[1, 32, 4][j % 3], rf=2,
                        models=["UCSC-C240-M7SX-EXTENDED-MODEL", "NX-8155-G9"],
                        raw_bytes=10**15, containers=9,
                        services=["Files (3)", "Objects", "NDB (12)", "vCenter"] if j == 0
                        else [], hosts_pc=j == 0,
                        external=[M.ExternalArray(id=f"x{i}{j}", product="Pure FlashArray",
                                                  name="fa-01", containers=2)] if j == 1
                        else [])
                 for j in range(14)]
        sid = f"site:{host}"
        sites.append(M.Site(id=sid, name=host, pc=M.PcBlock(
            id=f"{sid}:pc", version="pc.7.5.1.6", vms=3, vcpus=30, mem_gib=132,
            services=[M.Service("DR", 1234), M.Service("Flow", 5678), M.Service("VPCs", 99),
                      M.Service("X-Play", 42)]), cards=cards))
        links.append(M.Link(id=f"l{i}", kind="replication", a=sid, b="az:vault",
                            repl=[M.Repl("NearSync", 60), M.Repl("Async", 3600),
                                  M.Repl("Async", 86400), M.Repl("Sync", 0)], policies=9))
        links.append(M.Link(id=f"p{i}", kind="pairing", a=sid, b="az:vault", both=True))
    remotes = [M.Remote(id="az:vault", name="vault-with-an-unusually-long-availability-zone",
                        region="somewhere-far-away")]
    return M.ConceptualModel(sites=sites, remotes=remotes, links=links)


def test_stress_model_never_overflows_and_keeps_six_point_floor() -> None:
    scenes = conceptual_layout(_stress_model())
    assert len(scenes) == 3
    for sc in scenes:
        assert check_scene(sc) == []
    legend = [t for sc in scenes for t in _all_text(sc)]
    assert "External storage" in legend


# ─── reports ───────────────────────────────────────────────────────────


def test_word_report_contains_the_figure(estates: dict[str, Environment],
                                         tmp_path: Path) -> None:
    from docx import Document
    from docx.enum.section import WD_ORIENT

    from ntnxvision_core.exporters.word import WordExporter

    out = WordExporter().export(estates["enterprise"], tmp_path / "add.docx")
    doc = Document(str(out))
    headings = [p.text for p in doc.paragraphs if p.style.name == "Heading 2"]
    assert any(h.endswith("Solution Conceptual Overview") for h in headings)
    descr = [el.get("descr") or "" for el in doc.element.body.iter()
             if el.tag.endswith("}docPr")]
    figs = [d for d in descr if "Solution conceptual overview" in d]
    assert len(figs) == 2 and "part 1 of 2" in figs[0]
    # The wide figure sits in a landscape section with its own running header.
    land = [s for s in doc.sections if s.orientation == WD_ORIENT.LANDSCAPE]
    assert land and not land[0].header.is_linked_to_previous
    text = "\n".join([p.text for p in doc.paragraphs]
                     + [c.text for t in doc.tables for r in t.rows for c in r.cells])
    assert "DESIGN SUMMARY" in text
    assert "Continued in Figure" in text


def test_pdf_report_draws_the_figure_as_vectors(estates: dict[str, Environment],
                                                tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from ntnxvision_core.exporters import pdf as pdf_mod

    drawn: list[str] = []
    real = pdf_mod.draw_scene

    def spy(pdf: Any, sc: S.Scene, *a: Any, **kw: Any) -> float:
        drawn.append(sc.caption)
        return real(pdf, sc, *a, **kw)

    monkeypatch.setattr(pdf_mod, "draw_scene", spy)
    out = pdf_mod.PdfExporter().export(estates["medium"], tmp_path / "r.pdf")
    assert out.stat().st_size > 20_000
    assert drawn == ["Solution conceptual overview: 2 availability zones, 6 clusters, "
                     "33 nodes."]


def test_pdf_drawing_uses_real_embedded_text(estates: dict[str, Environment]) -> None:
    sc = _scenes(estates["small"])[0]
    pdf = FPDF(unit="pt", format=(sc.width + 20, sc.height + 20))
    pdf.compress = False
    pdf.add_page()
    draw_scene(pdf, sc, 10, 10)
    raw = bytes(pdf.output())
    assert b"/FontFile2" in raw  # Figtree embedded
    assert raw.count(b" Tj") + raw.count(b" TJ") >= len(_all_text(sc))
