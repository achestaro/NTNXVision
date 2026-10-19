"""Disaster recovery design (A5): facts, layout, determinism, golden scenes, reports.

Estates come from the session fixture `design_envs` (scenario estates and demo PCs,
collected through the mock API with the real collectors).
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

import pytest

from ntnxvision_core.diagrams import TYPES, check_scene, model_envelope, model_from_envelope
from ntnxvision_core.diagrams import dr as D
from ntnxvision_core.diagrams import scene as S
from ntnxvision_core.diagrams.layout.conceptual import Page
from ntnxvision_core.diagrams.layout.dr import layout
from ntnxvision_core.diagrams.model import Repl
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.models import Environment

from .diagram_helpers import shuffled as _shuffled

GOLDEN = Path(__file__).parent / "golden" / "diagrams"
UPDATE = os.environ.get("NTNXVISION_UPDATE_GOLDEN") == "1"
EM_DASH = chr(0x2014)


def _scenes(env: Environment) -> list[S.Scene]:
    return layout(D.dr_model(env))


def _texts(sc: S.Scene) -> list[str]:
    return [t.text for n in sc.nodes for t in n.lines] + \
        [t.text for e in sc.edges for lb in e.labels for t in lb.lines]


def _flow(m: D.DrModel, a: str, b: str) -> D.DrFlow:
    return next(f for f in m.flows if {m.name_of(f.a), m.name_of(f.b)} == {a, b})


# ─── facts ─────────────────────────────────────────────────────────────


def test_multi3_sync_inside_a_zone_nearsync_across_and_remote(
        design_envs: dict[str, Environment]) -> None:
    m = D.dr_model(design_envs["multi3"])
    sync = _flow(m, "PHX-PROD-01", "PHX-DR-01")
    assert (m.name_of(sync.a), m.name_of(sync.b)) == ("PHX-PROD-01", "PHX-DR-01")
    assert sync.site_a == sync.site_b == "site:pc01.demo.local"
    assert [(r.type, r.rpo_seconds) for r in sync.forward] == [("Sync", 0)]
    near = _flow(m, "PHX-PROD-01", "SEA-PROD-01")
    assert D.flow_text(near) == "NearSync 1 min"
    both = _flow(m, "SEA-PROD-02", "pc-dr")
    assert D.flow_text(both) == "Async 1 h, both ways" and D.mutual(both)
    remote = m.remote("az:pc-dr")
    assert remote is not None and remote.clusters == 6 and remote.cluster_names == []
    assert {(p.a, p.b): p.count for p in m.plans}[("az:pc-dr", "site:pc01.demo.local")] == 6
    unprotected = [c.name for s in m.sites for c in s.clusters if c.protected is False]
    assert unprotected == ["AMS-EDGE-02"]
    assert D.protected_line(m.sites[0]) == "3 of 4 clusters protected"
    bullets = D.bullets(m)
    assert bullets[1] == "Type follows the RPO: 0 Sync, up to 15 min NearSync, else Async."
    assert "Not protected: AMS-EDGE-02." in bullets


def test_enterprise_one_way_pairs_and_outside_targets(design_envs: dict[str, Environment]
                                                      ) -> None:
    m = D.dr_model(design_envs["enterprise"])
    assert (m.policies, m.recovery_plans) == (8, 3)
    # The Metro policy's recovery plan names a witness.
    assert [(p.count, p.witness) for p in m.plans if p.witness] == [(1, True)]
    assert "a witness arbitrates the Metro recovery plans between en-pc01 and en-pc02" in \
        D.bullets(m)[1]
    tier0 = _flow(m, "NYC-PROD-01", "CHI-PROD-01")
    assert [(r.type, r.rpo_seconds) for r in tier0.repl] == [("Sync", 0)]
    assert {r.name: r.kind for r in m.remotes} == {"dr-apac": "pc", "Xi-EU-West": "xi"}
    rows = D.policy_rows(design_envs["enterprise"], m)
    by_name = {r[0]: r for r in rows}
    assert by_name["Tier0-Metro"][1:5] == ["en-pc01 / NYC-PROD-01", "en-pc02 / CHI-PROD-01",
                                           "Sync", "0 s"]
    # A remote cluster known only by uuid is never printed.
    assert by_name["SIN-Vault-Daily"][2] == "dr-apac (remote)"
    assert by_name["SIN-Vault-Daily"][6] == 1


def test_medium_mixed_directions_read_from_the_source(design_envs: dict[str, Environment]
                                                      ) -> None:
    m = D.dr_model(design_envs["medium"])
    f = _flow(m, "PROD-E01", "PROD-W01")
    assert D.flow_text(f) == "NearSync 1 min, back Async 1 h"
    assert D.bullets(m)[0] == ("3 protection policies across 2 availability zones; no "
                               "recovery plans.")


def test_applies_only_with_a_replication_target(design_envs: dict[str, Environment]) -> None:
    t = TYPES["dr"]
    assert (t.chapter, t.section, t.slug) == ("data_protection", "Disaster Recovery Design",
                                              "data-protection")
    assert t.applies(design_envs["small"]) and not t.applies(Environment())
    local = design_envs["small"].model_copy(deep=True)
    for p in local.protection_policies:
        p.replications, p.locations, p.target_az = [], [], None
    assert not t.applies(local)


# ─── determinism ───────────────────────────────────────────────────────




@pytest.mark.parametrize("name", ["medium", "enterprise", "multi3"])
def test_twice_and_shuffled_give_identical_bytes(design_envs: dict[str, Environment],
                                                 name: str) -> None:
    base = [S.scene_json(x) + render_svg(x) for x in _scenes(design_envs[name])]
    assert [S.scene_json(x) + render_svg(x) for x in _scenes(design_envs[name])] == base
    for seed in (1, 2, 3):
        got = [S.scene_json(x) + render_svg(x)
               for x in _scenes(_shuffled(design_envs[name], seed))]
        assert got == base


def test_envelope_round_trip(design_envs: dict[str, Environment]) -> None:
    m = D.dr_model(design_envs["enterprise"])
    doc = json.loads(json.dumps(model_envelope("dr", m)))
    type_id, again = model_from_envelope(doc)
    assert type_id == "dr" and again == m
    assert [S.scene_json(x) for x in layout(again)] == [S.scene_json(x) for x in layout(m)]


def _pretty(sc: S.Scene) -> bytes:
    return (json.dumps(S.to_doc(sc), sort_keys=True, indent=1, ensure_ascii=False)
            + "\n").encode()


@pytest.mark.parametrize("name", ["small", "medium"])
def test_golden_scene_and_svg(design_envs: dict[str, Environment], name: str) -> None:
    scenes = _scenes(design_envs[name])
    for suffix, got in (("scene.json", b"".join(_pretty(x) for x in scenes)),
                        ("svg", b"".join(render_svg(x) for x in scenes))):
        path = GOLDEN / f"dr-{name}.{suffix}"
        if UPDATE or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(got)
        assert got == path.read_bytes(), (
            f"{path.name} changed; rerun with NTNXVISION_UPDATE_GOLDEN=1 after reviewing")


# ─── overflow and scaling ──────────────────────────────────────────────


@pytest.mark.parametrize("name", ["small", "medium", "enterprise", "xlarge", "multi3",
                                  "pc01", "large2"])
def test_no_overflow_and_parts_fit_the_page(design_envs: dict[str, Environment],
                                            name: str) -> None:
    page = Page()
    scenes = _scenes(design_envs[name])
    assert scenes
    for sc in scenes:
        assert check_scene(sc) == [], sc.id
        assert sc.width <= page.width and sc.height <= page.max_h
        assert all(EM_DASH not in t for t in _texts(sc))
    assert [sc.part for sc in scenes] == [(i + 1, len(scenes)) for i in range(len(scenes))]


def test_linked_zones_share_a_part(design_envs: dict[str, Environment]) -> None:
    scenes = _scenes(design_envs["enterprise"])
    assert [sc.meta["sites"] for sc in scenes] == [
        ["site:en-pc01.demo.local", "site:en-pc02.demo.local"],
        ["site:en-pc03.demo.local", "site:en-pc04.demo.local"]]


def _stress() -> D.DrModel:
    sites = []
    for s in range(5):
        sid = f"site:pc{s}.example.lab"
        clusters = [D.DrCluster(id=f"cluster:{s}-{c}",
                                name=f"VERY-LONG-CLUSTER-NAME-FOR-STRESS-SITE{s}-NUMBER-{c:02d}",
                                hypervisor="AHV", nodes=c + 1, rf=2, nearsync=c % 2 == 0,
                                protected=c < 6 if c != 3 else None)
                    for c in range(9)]
        sites.append(D.DrSite(id=sid, name=f"pc{s}.example.lab", clusters=clusters))
    remotes = [D.DrRemote(id="az:vault", name="vault", clusters=4),
               D.DrRemote(id="az:cloud", name="cloud", kind="xi")]
    flows = []
    for s in range(5):
        for c in range(6):
            for t in ((s + 1) % 5, (s + 2) % 5):
                a, b = f"cluster:{s}-{c}", f"cluster:{t}-{(c + 1) % 6}"
                if a < b:
                    flows.append(D.DrFlow(id=f"flow:{a}->{b}", a=a, b=b,
                                          site_a=f"site:pc{s}.example.lab",
                                          site_b=f"site:pc{t}.example.lab",
                                          forward=[Repl("Async", 3600)],
                                          backward=[Repl("NearSync", 60)] if c % 2 else []))
            flows.append(D.DrFlow(id=f"flow:{s}-{c}->vault", a=f"cluster:{s}-{c}",
                                  b="az:vault", site_a=f"site:pc{s}.example.lab",
                                  site_b="az:vault", forward=[Repl("Async", 86400)]))
        flows.append(D.DrFlow(id=f"flow:{s}-0->{s}-1", a=f"cluster:{s}-0",
                              b=f"cluster:{s}-1", site_a=f"site:pc{s}.example.lab",
                              site_b=f"site:pc{s}.example.lab", forward=[Repl("Sync", 0)]))
    flows.append(D.DrFlow(id="flow:x->cloud", a="site:pc0.example.lab", b="az:cloud",
                          site_a="site:pc0.example.lab", site_b="az:cloud",
                          forward=[Repl("Async", 14400)]))
    plans = [D.DrPlans(id="plans:1", a="site:pc0.example.lab", b="site:pc1.example.lab",
                       count=12, witness=True)]
    return D.DrModel(sites=sites, remotes=remotes, flows=flows, plans=plans, policies=40,
                     recovery_plans=12)


def test_stress_model_never_overflows() -> None:
    scenes = layout(_stress())
    assert len(scenes) >= 2
    for sc in scenes:
        assert check_scene(sc) == [], sc.id
        assert all(t.size >= 6.0 for n in sc.nodes for t in n.lines)


# ─── reports ───────────────────────────────────────────────────────────


def _opts() -> Any:
    from ntnxvision_core.exporters import ExportOptions

    return ExportOptions()


def test_reports_carry_the_section_and_its_table(design_envs: dict[str, Environment],
                                                 tmp_path: Path) -> None:
    from ntnxvision_core.exporters import export_environment
    from ntnxvision_core.exporters.content import SceneFigure, Table, build_report

    env = design_envs["multi3"]
    report = build_report(env, _opts(), eyebrow="test")
    ch = next(c for c in report.chapters if c.key == "data_protection")
    sec = next(s for s in ch.sections if s.title == "Disaster Recovery Design")
    figs = [b for b in sec.blocks if isinstance(b, SceneFigure)]
    tables = [b for b in sec.blocks if isinstance(b, Table)]
    assert figs and figs[0].summary and figs[0].scene.type == "dr"
    assert tables[0].headers[:3] == ["Policy", "Source (AZ / cluster)",
                                     "Target (AZ / cluster)"]
    # The plain policy table gives way to the design section's.
    assert not any(s.title == "Protection Policies" for s in ch.sections)
    docx = export_environment(env, "word", tmp_path, "r")
    xml = zipfile.ZipFile(io.BytesIO(docx.read_bytes())).read("word/document.xml").decode()
    # Word headers may break after "/" (a zero-width space follows it).
    assert "Disaster Recovery Design" in xml and "Source (AZ /" in xml
    pdf = export_environment(env, "pdf", tmp_path, "r")
    assert pdf.stat().st_size > 0
