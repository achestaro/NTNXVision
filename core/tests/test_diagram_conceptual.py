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

from ntnxvision_core.diagrams import (
    TYPES,
    check_scene,
    conceptual_layout,
    conceptual_model,
    model_envelope,
    model_from_envelope,
)
from ntnxvision_core.diagrams import model as M
from ntnxvision_core.diagrams import scene as S
from ntnxvision_core.diagrams.layout.conceptual import MIN_KEEP, Page, Refs
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


def _labels(sc: S.Scene, edge_id: str) -> list[str]:
    return [t.text for e in sc.edges if e.id == edge_id for lb in e.labels for t in lb.lines]


def _pdf_bytes(sc: S.Scene) -> bytes:
    pdf = FPDF(orientation="L", unit="pt", format=(sc.height + 20, sc.width + 20))
    pdf.set_creation_date(datetime(2026, 10, 1, tzinfo=timezone.utc))
    pdf.add_page()
    draw_scene(pdf, sc, 10, 10)
    return bytes(pdf.output())


# ─── facts ─────────────────────────────────────────────────────────────


def test_medium_replication_is_labelled_per_direction(estates: dict[str, Environment]
                                                     ) -> None:
    """md-pc01 to md-pc02 is NearSync 1 min and Async 1 h; back only Async 1 h. Only
    the schedule present both ways reads "both directions"."""
    m = conceptual_model(estates["medium"])
    assert [s.name for s in m.sites] == ["md-pc01.demo.local", "md-pc02.demo.local"]
    assert m.remotes == []  # the paired PC is a collected site, not a remote AZ
    kinds = {lk.kind: lk for lk in m.links}
    assert set(kinds) == {"pairing", "replication"}
    rep = kinds["replication"]
    assert (rep.a, rep.b) == ("site:md-pc01.demo.local", "site:md-pc02.demo.local")
    assert [(r.type, r.rpo_seconds) for r in rep.forward] == [("NearSync", 60),
                                                               ("Async", 3600)]
    assert [(r.type, r.rpo_seconds) for r in rep.backward] == [("Async", 3600)]
    sc = conceptual_layout(m)[0]
    assert _labels(sc, rep.id) == ["Async 1 h", "both directions", "md-pc01 to md-pc02:",
                                   "NearSync 1 min"]
    edge = next(e for e in sc.edges if e.id == rep.id)
    assert edge.arrow_end and edge.arrow_start
    bullets = conceptual_bullets(m, [sc])
    assert ("md-pc01 and md-pc02 are paired: Async 1 h both ways; md-pc01 to md-pc02 also "
            "NearSync 1 min.") in bullets
    # The cluster hosting the PC comes first and carries the role.
    assert all(s.cards[0].hosts_pc for s in m.sites)
    assert any("vCenter" in c.services for s in m.sites for c in s.cards)


def test_enterprise_one_way_pairs_sync_scale_out_and_arrays(estates: dict[str, Environment]
                                                            ) -> None:
    m = conceptual_model(estates["enterprise"])
    nyc = m.sites[0]
    assert nyc.pc is not None and nyc.pc.vms == 3
    assert (nyc.pc.vm_vcpus, nyc.pc.vm_mem_gib, nyc.pc.size) == (10, 44, "Large")
    assert [c.name for c in nyc.cards if c.hosts_pc] == ["NYC-MGMT-01"]
    assert {r.name: r.kind for r in m.remotes} == {"dr-apac": "pc", "Xi-EU-West": "xi"}
    lon_sin = next(lk for lk in m.links if lk.kind == "replication"
                   and {lk.a, lk.b} == {"site:en-pc03.demo.local", "site:en-pc04.demo.local"})
    assert [r.rpo_seconds for r in lon_sin.forward] == [4 * 3600]
    assert [r.rpo_seconds for r in lon_sin.backward] == [86400]
    nyc_chi = next(lk for lk in m.links if lk.kind == "replication"
                   and lk.a == "site:en-pc01.demo.local")
    assert ("Sync", 0) in [(r.type, r.rpo_seconds) for r in nyc_chi.forward]
    # External arrays by product; the DSF strip counts only the cluster's own containers.
    db = next(c for c in nyc.cards if c.name == "NYC-DB-01")
    assert [(x.product, x.containers) for x in db.external] == [("Pure FlashArray", 1)]
    lon = next(c for s in m.sites for c in s.cards if c.name == "LON-PROD-01")
    assert [x.product for x in lon.external] == ["Dell PowerStore"]
    assert db.containers == 3
    # One-way pairs read per direction, and the summary says so.
    sc = [s for s in conceptual_layout(m) if "site:en-pc03.demo.local" in s.meta["sites"]][0]
    assert _labels(sc, lon_sin.id) == ["en-pc03 to en-pc04:", "Async 4 h",
                                       "en-pc04 to en-pc03:", "Async 1 d"]


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
        AvailabilityZone(name="dr-site", url="u-remote", region="OnPrem",
                         connection_status="DISCONNECTED"),
    ]
    env.protection_policies = [ProtectionPolicy(uuid="p1", name="in", rpo_seconds=3600,
                                                replication_type="ASYNC",
                                                source_az="dr-site", target_az="Local AZ")]
    m = conceptual_model(env)
    rep = next(lk for lk in m.links if lk.kind == "replication")
    assert (rep.a, rep.b) == ("site:pc.example.lab", "az:dr-site")
    assert rep.backward and not rep.forward
    pairing = next(lk for lk in m.links if lk.kind == "pairing")
    assert not pairing.connected
    sc = conceptual_layout(m)[0]
    bullets = conceptual_bullets(m, [sc])
    assert any(b.startswith("dr-site, outside this inventory, replicates to pc") for b in bullets)
    assert "AZ pairing, not connected" in _all_text(sc)
    assert not check_scene(sc)


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


def test_remote_names_differing_by_case_get_distinct_ids() -> None:
    from ntnxvision_core.models import AvailabilityZone, Cluster, PrismCentral

    env = Environment(prism_central=PrismCentral(host="pc.example.lab", uuid="u"))
    env.clusters = [Cluster(uuid="c1", name="CL", hypervisor_types=["AHV"], num_nodes=3)]
    env.availability_zones = [AvailabilityZone(name="DR", url="https://192.0.2.10:9440"),
                              AvailabilityZone(name="dr", url="https://192.0.2.11:9440")]
    ids = [r.id for r in conceptual_model(env).remotes]
    assert len(set(ids)) == 2 and "az:dr" in ids


# ─── determinism ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["small", "medium", "enterprise", "xlarge"])
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


def test_model_envelope_and_scene_documents_round_trip(estates: dict[str, Environment]
                                                       ) -> None:
    m = conceptual_model(estates["enterprise"])
    doc = json.loads(json.dumps(model_envelope("conceptual", m)))
    assert doc["schema"] == M.MODEL_SCHEMA and doc["type"] == "conceptual"
    type_id, again = model_from_envelope(doc)
    assert type_id == "conceptual" and again == m
    assert M.model_from_json(M.model_json(m)) == m
    assert M.model_hash(again) == M.model_hash(m)
    with pytest.raises(ValueError):
        model_from_envelope({**doc, "type": "nope"})
    for sc in conceptual_layout(again):
        assert S.scene_json(S.from_doc(json.loads(S.scene_json(sc)))) == S.scene_json(sc)


def test_registry_describes_the_conceptual_type(estates: dict[str, Environment]) -> None:
    t = TYPES["conceptual"]
    assert (t.chapter, t.section) == ("logical_design", "Solution Conceptual Overview")
    assert t.applies(estates["small"]) and not t.applies(Environment())
    assert t.page.width == 672.0 and t.page.max_h == 440.0


def test_edited_model_lays_out_like_a_generated_one(estates: dict[str, Environment]) -> None:
    """The Diagrams editor's contract: rename, reorder, add a user component, delete."""
    m = conceptual_model(estates["medium"])
    site = m.sites[0]
    site.cards[1].name = "Renamed cluster"
    site.cards.reverse()
    site.cards.append(M.Card(id="user:7f2c", name="Backup appliance", kind="user",
                             note="Added by the architect"))
    m.sites[1].cards.pop()
    sc = conceptual_layout(M.model_from_json(M.model_json(m)))[0]
    texts = _all_text(sc)
    assert "Renamed cluster" in texts and "USER-ADDED" in texts
    assert not check_scene(sc)


# ─── golden scenes ─────────────────────────────────────────────────────


def _pretty(sc: S.Scene) -> bytes:
    return (json.dumps(S.to_doc(sc), sort_keys=True, indent=1, ensure_ascii=False)
            + "\n").encode()


@pytest.mark.parametrize("name", ["small", "medium"])
def test_golden_scene_and_svg(estates: dict[str, Environment], name: str) -> None:
    scenes = _scenes(estates[name])
    for suffix, got in (("scene.json", b"".join(_pretty(x) for x in scenes)),
                        ("svg", b"".join(render_svg(x) for x in scenes))):
        path = GOLDEN / f"conceptual-{name}.{suffix}"
        if UPDATE or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(got)
        assert got == path.read_bytes(), (
            f"{path.name} changed; rerun with NTNXVISION_UPDATE_GOLDEN=1 after reviewing")


# ─── overflow and scaling ──────────────────────────────────────────────


@pytest.mark.parametrize("name", ["small", "medium", "enterprise", "xlarge"])
def test_no_text_exceeds_its_box_and_labels_sit_on_their_edges(
        estates: dict[str, Environment], name: str) -> None:
    page = Page()
    for sc in _scenes(estates[name], page):
        assert check_scene(sc) == [], sc.id  # includes the label-anchoring check
        assert sc.width <= page.width and sc.height <= page.max_h
        assert all(EM_DASH not in t for t in _all_text(sc))
        # No PC services line ends on a separator.
        assert not any(t.rstrip().endswith("·") for t in _all_text(sc))


def test_every_summarised_site_shows_the_pc_host_and_two_largest(
        estates: dict[str, Environment]) -> None:
    """Decision 7: a site summarised in a "+N more" card still draws at least the
    cluster hosting PC and the 2 largest clusters; the "+N more" card is short."""
    m = conceptual_model(estates["xlarge"])
    scenes = conceptual_layout(m, Page(), Refs(clusters_table="Table 5-2"))
    by_site = {s.id: s for s in m.sites}
    seen: list[str] = []
    summarised_sites = 0
    for sc in scenes:
        seen += sc.meta["drawn"] + sc.meta["summarised"]
        for sid in sc.meta["sites"]:
            site = by_site[sid]
            drawn = [c for c in site.cards if c.id in sc.meta["drawn"]]
            if len(drawn) < len(site.cards):
                summarised_sites += 1
                assert len(drawn) >= MIN_KEEP
                assert any(c.hosts_pc for c in drawn)
                largest = sorted(site.cards, key=lambda c: -(c.nodes or 0))
                assert {c.id for c in largest[:2]} <= {c.id for c in drawn} | {
                    c.id for c in site.cards if c.hosts_pc}
                more = sc.node(f"{sid}:more")
                card = next(n for n in sc.nodes if n.id == drawn[-1].id)
                assert more.h < card.h
    assert summarised_sites
    assert sorted(seen) == sorted(c.id for s in m.sites for c in s.cards)
    assert len(seen) == 104
    assert any("Table 5-2" in t for sc in scenes for t in _all_text(sc))


def test_same_size_azs_are_drawn_alike_in_every_part(estates: dict[str, Environment]
                                                      ) -> None:
    """One detail level for the whole estate: the 8 equal xlarge AZs (13 clusters each)
    all show 3 clusters and a "+10 more" card, whichever part they land in, over a
    handful of parts of up to 3 AZs."""
    from ntnxvision_core.exporters.content import first_part_budget

    m = conceptual_model(estates["xlarge"])
    for budget in (None, first_part_budget(["x" * 90] * 5)):
        scenes = conceptual_layout(m, None, None, budget)
        counts = {sid: sum(1 for c in m.site(sid).cards if c.id in sc.meta["drawn"])
                  for sc in scenes for sid in sc.meta["sites"]}
        assert set(counts.values()) == {MIN_KEEP}, counts
        assert len(scenes) <= 4 and max(len(sc.meta["sites"]) for sc in scenes) == 3
        assert all(sc.meta["summarise"] for sc in scenes)


def test_two_way_remote_replication_is_one_bullet() -> None:
    from ntnxvision_core.diagrams.summary import conceptual_bullets as bullets

    m = M.ConceptualModel(
        sites=[M.Site(id="site:pc.example.lab", name="pc.example.lab",
                      cards=[M.Card(id="cluster:c", name="CL", hypervisor="AHV", nodes=2)])],
        remotes=[M.Remote(id="az:dr", name="dr", region="OnPrem")],
        links=[M.Link(id="r", kind="replication", a="site:pc.example.lab", b="az:dr",
                      forward=[M.Repl("Async", 3600)], backward=[M.Repl("Async", 3600)])])
    sc = conceptual_layout(m)[0]
    found = [b for b in bullets(m, [sc]) if "dr" in b]
    assert found == ["pc and dr (outside this inventory) replicate both ways (Async 1 h)."]
    assert "Physical AZ, on-premises" in _all_text(sc)


def test_paired_sites_share_a_part(estates: dict[str, Environment]) -> None:
    parts = [sc.meta["sites"] for sc in _scenes(estates["enterprise"])]
    assert parts == [["site:en-pc01.demo.local", "site:en-pc02.demo.local"],
                     ["site:en-pc03.demo.local", "site:en-pc04.demo.local"]]


def test_a_part_that_cannot_fit_splits(estates: dict[str, Environment]) -> None:
    """With a tight budget for part 1, it gives up sites to new parts instead of
    dropping below the decision 7 floor."""
    m = conceptual_model(estates["xlarge"])
    loose = conceptual_layout(m)
    tight = conceptual_layout(m, None, None, lambda _w: 300.0)
    assert len(tight[0].meta["sites"]) <= len(loose[0].meta["sites"])
    assert sum(len(sc.meta["sites"]) for sc in tight) == 8


def _stress_model() -> M.ConceptualModel:
    sites, links = [], []
    for i in range(7):
        host = f"stress{i + 1:02d}-region-with-a-long-management-name.example.lab"
        cards = [M.Card(id=f"cluster:s{i}-{j}",
                        name=f"STRESS-{i}-CLUSTER-WITH-A-VERY-LONG-NAME-{j:02d}-PRODUCTION",
                        hypervisor="AHV" if j % 3 else "ESXi", nodes=[1, 32, 4][j % 3], rf=2,
                        models=["UCSC-C240-M7SX-EXTENDED-MODEL", "NX-8155-G9"],
                        raw_bytes=10**15, containers=9, compute_only=j == 5,
                        services=["Files (3)", "Objects", "NDB (12)", "vCenter"] if j == 0
                        else [], hosts_pc=j == 0,
                        external=[M.ExternalArray(id=f"x{i}{j}", product="Pure FlashArray",
                                                  name="fa-01", containers=2)] if j == 1
                        else [])
                 for j in range(14)]
        sid = f"site:{host}"
        sites.append(M.Site(id=sid, name=host, pc=M.PcBlock(
            id=f"{sid}:pc", version="pc.7.5.1.6", vms=3, vm_vcpus=14, vm_mem_gib=60,
            size="X-Large",
            services=[M.Service("DR", 1234), M.Service("Flow", 5678), M.Service("VPCs", 99),
                      M.Service("X-Play", 42)]), cards=cards))
        links.append(M.Link(id=f"l{i}", kind="replication", a=sid, b="az:vault",
                            forward=[M.Repl("Sync", 0), M.Repl("NearSync", 60),
                                     M.Repl("Async", 3600), M.Repl("Async", 14400),
                                     M.Repl("Async", 86400)],
                            backward=[M.Repl("Async", 3600)], policies=9))
        links.append(M.Link(id=f"p{i}", kind="pairing", a=sid, b="az:vault",
                            connected=i % 2 == 0))
    remotes = [M.Remote(id="az:vault", name="vault-with-an-unusually-long-availability-zone",
                        region="somewhere-far-away")]
    return M.ConceptualModel(sites=sites, remotes=remotes, links=links)


def test_stress_model_never_overflows_and_keeps_six_point_floor() -> None:
    scenes = conceptual_layout(_stress_model())
    for sc in scenes:
        assert check_scene(sc) == []
        assert sc.height <= Page().max_h
    texts = [t for sc in scenes for t in _all_text(sc)]
    assert "External storage" in texts
    assert any("Sync to 1 d" in t for t in texts)
    assert not any("…" in t for t in texts if t.startswith("STRESS"))


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
    land = [s for s in doc.sections if s.orientation == WD_ORIENT.LANDSCAPE]
    assert land and not land[0].header.is_linked_to_previous
    text = "\n".join([p.text for p in doc.paragraphs]
                     + [c.text for t in doc.tables for r in t.rows for c in r.cells])
    assert "DESIGN SUMMARY" in text
    assert "Continued in Figure" in text
    # Decision 3: part 1 (377 pt) has its summary beside it, in the figure's table row.
    beside = [t for t in doc.tables if len(t.rows[0].cells) == 2
              and "DESIGN SUMMARY" in t.rows[0].cells[1].text]
    assert beside
    # The DR chapter names the figures that draw replication.
    assert "Figures 5-1 to 5-2 (Solution Conceptual Overview" in text
    # Plain ASCII hyphens: Word rendered both U+2011 and w:noBreakHyphen with gaps or
    # squeezed text, and they broke copy, paste and Find on host and cluster names.
    xml = doc.element.xml
    assert "\u2011" not in xml and "w:noBreakHyphen" not in xml
    assert "2 AHV" in text or "3 AHV" in text


def test_word_heading_summary_and_figure_are_chained(estates: dict[str, Environment],
                                                     tmp_path: Path) -> None:
    """Heading 1, Heading 2, summary and the figure keep with the next paragraph, and
    part 1 fits under them (so no page holds only a heading or a summary)."""
    from docx import Document

    from ntnxvision_core.exporters.content import BODY_H, HEADINGS_H, first_part_budget
    from ntnxvision_core.exporters.word import WordExporter

    env = estates["xlarge"]
    doc = Document(str(WordExporter().export(env, tmp_path / "x.docx")))
    paras = doc.paragraphs
    h2 = next(i for i, p in enumerate(paras) if p.style.name == "Heading 2"
              and p.text.endswith("Solution Conceptual Overview"))
    assert paras[h2 - 1].style.name == "Heading 1"  # the intro moved below the figure
    assert paras[h2 - 1].paragraph_format.keep_with_next
    assert paras[h2].paragraph_format.keep_with_next
    from ntnxvision_core.exporters.content import build_report

    report = build_report(env, _opts(), "x")
    fig = next(b for ch in report.chapters for s in ch.sections for b in s.blocks
               if hasattr(b, "scene"))
    budget = first_part_budget(fig.summary)(fig.scene.width)
    assert fig.scene.height <= budget <= BODY_H - HEADINGS_H


def _opts() -> Any:
    from ntnxvision_core.exporters import ExportOptions

    return ExportOptions()


def test_pdf_keeps_heading_summary_and_figure_on_one_page(
        estates: dict[str, Environment], tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    from ntnxvision_core.exporters import pdf as pdf_mod

    for name in ("medium", "enterprise", "xlarge"):
        events: list[tuple[str, int]] = []
        real_h2, real_sum, real_draw = (pdf_mod.PdfExporter._h2,
                                        pdf_mod.PdfExporter._summary_block, pdf_mod.draw_scene)

        def h2(self: Any, pdf: Any, text: str, need: float = 26.0,
               ev: list[tuple[str, int]] = events, real: Any = real_h2) -> None:
            real(self, pdf, text, need)
            ev.append((f"h2:{text}", pdf.page_no()))

        def summary(self: Any, pdf: Any, *a: Any, ev: list[tuple[str, int]] = events,
                    real: Any = real_sum) -> float:
            ev.append(("summary", pdf.page_no()))
            return real(self, pdf, *a)

        def draw(pdf: Any, sc: S.Scene, *a: Any, ev: list[tuple[str, int]] = events,
                 real: Any = real_draw, **kw: Any) -> float:
            ev.append((f"figure:{sc.part[0]}", pdf.page_no()))
            h = real(pdf, sc, *a, **kw)
            assert pdf.get_y() <= pdf.h  # never drawn past the page
            return h

        monkeypatch.setattr(pdf_mod.PdfExporter, "_h2", h2)
        monkeypatch.setattr(pdf_mod.PdfExporter, "_summary_block", summary)
        monkeypatch.setattr(pdf_mod, "draw_scene", draw)
        pdf_mod.PdfExporter().export(estates[name], tmp_path / f"{name}.pdf")
        pages = dict(events)
        head = next(p for k, p in events if k.endswith("Solution Conceptual Overview"))
        assert pages["summary"] == pages["figure:1"] == head, name
        monkeypatch.undo()


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
