"""Platform service designs (NKP, NDB, Objects, Files): gating, facts, determinism,
clean scenes, product badges, the editor round trip and the report sections."""

from __future__ import annotations

import re
from typing import Any

import pytest

from ntnxvision_core.diagrams import TYPES, check_scene, model_envelope, model_from_envelope
from ntnxvision_core.diagrams import services as S
from ntnxvision_core.diagrams import store as ST
from ntnxvision_core.diagrams.render.svg import render_svg

from .diagram_helpers import shuffled

SERVICE_TYPES = ("nkp", "ndb", "objects", "files")


@pytest.fixture(scope="module")
def envs(_demo_state: Any) -> dict[str, Any]:
    """pc01 with its NDB appliance and NKP API (NKP, NDB, Objects), pc02 (Files),
    pc04 (none of them) and pc01 to pc03 combined."""
    from ntnxvision_core.api import PrismCredentials
    from ntnxvision_core.api.kube_client import KubeCredentials
    from ntnxvision_core.api.ndb_client import NdbCredentials
    from ntnxvision_core.engine import collect_environment, combine_environments
    from ntnxvision_core.synthetic import demo as demo_mode

    def pc(host: str, **kw: Any) -> Any:
        demo_mode.reset()
        return collect_environment(PrismCredentials(host=f"{host}.demo.local",
                                                    username="admin", password="demo"), **kw)

    out = {
        "pc01": pc("pc01", ndb_creds=NdbCredentials(host="ndb.demo.local", username="admin",
                                                    password="demo"),
                   kube_creds=[KubeCredentials(host="nkp01.demo.local", token="demo")]),
        "pc02": pc("pc02"), "pc03": pc("pc03"), "pc04": pc("pc04")}
    out["multi"] = combine_environments([out["pc01"], out["pc02"], out["pc03"]])
    demo_mode.reset()
    return out


def _texts(type_id: str, env: Any) -> list[str]:
    t = TYPES[type_id]
    return [ln.text for sc in t.layout(t.build_model(env), t.page) for n in sc.nodes
            for ln in n.lines]


def test_each_diagram_exists_only_with_its_product(envs: dict[str, Any]) -> None:
    applies = {k: {t for t in SERVICE_TYPES if TYPES[t].applies(e)} for k, e in envs.items()}
    assert applies["pc01"] == {"nkp", "ndb", "objects"}
    assert applies["pc02"] == {"files"}
    assert applies["pc04"] == set()
    assert applies["multi"] == set(SERVICE_TYPES)
    # The demo estate as a whole draws all four (the Diagrams page lists them).
    listed = {t.id for e in envs.values() for t in ST.applicable(e)}
    assert set(SERVICE_TYPES) <= listed


def test_nkp_facts(envs: dict[str, Any]) -> None:
    m = S.nkp_model(envs["pc01"])
    assert [a.id for a in m.anchors] == ["site:pc01.demo.local:pc"]
    (cl,) = m.clusters
    assert cl.name == "PHX-PROD-02" and cl.id.startswith("cluster:") and \
        cl.data["cluster_uuid"] in cl.id
    (box,) = cl.boxes
    lanes = {ln.id.rsplit(":", 1)[-1]: ln for ln in box.lanes}
    assert [i.kind for i in lanes["endpoints"].items] == ["vip", "lb"]
    assert len(lanes["cp"].items) == 1 and len(lanes["worker"].items) == 3
    # Node names drop the NKP cluster's name (the box title says it).
    assert all(not i.name.startswith("nkp-prod-01-") for i in lanes["worker"].items)


def test_ndb_facts_engines_and_clones(envs: dict[str, Any]) -> None:
    m = S.ndb_model(envs["pc01"])
    assert [a.id for a in m.anchors] == [S.NDB_ANCHOR]
    assert [c.name for c in m.clusters] == ["PHX-PROD-01", "PHX-PROD-02"]
    assert all(c.anchor == S.NDB_ANCHOR for c in m.clusters)
    engines = {b.name for c in m.clusters for b in c.boxes}
    assert engines == {"MariaDB", "MongoDB", "MySQL", "Oracle", "PostgreSQL", "SQL Server"}
    mongo = next(b for c in m.clusters for b in c.boxes if b.name == "MongoDB")
    items = mongo.lanes[0].items
    for i, it in enumerate(items):
        if it.kind == "clone":
            assert it.of == items[i - 1].id or items[i - 1].of == it.of  # under its source


def test_objects_and_files_facts(envs: dict[str, Any]) -> None:
    o = S.objects_model(envs["pc01"])
    (store,) = o.boxes()
    assert store.name == "objects-01" and store.data["badge"] == "nus"
    assert len([i for ln in store.lanes for i in ln.items if i.kind == "bucket"]) == 6
    f = S.files_model(envs["pc02"])
    assert sorted(b.name for b in f.boxes()) == ["fs-corp", "fs-home"]
    shares = [i for b in f.boxes() for ln in b.lanes for i in ln.items if i.kind == "share"]
    assert {i.sub for i in shares} == {"SMB", "NFS"}


def test_combined_estate_draws_one_prism_central_per_source(envs: dict[str, Any]) -> None:
    m = S.objects_model(envs["multi"])
    assert {a.sub for a in m.anchors} == {"pc01.demo.local", "pc03.demo.local"}
    assert all(c.anchor in {a.id for a in m.anchors} for c in m.clusters)


@pytest.mark.parametrize("type_id", SERVICE_TYPES)
def test_scenes_clean_deterministic_and_badged(envs: dict[str, Any], type_id: str) -> None:
    t = TYPES[type_id]
    for name in ("pc01", "pc02", "multi"):
        env = envs[name]
        if not t.applies(env):
            continue
        scenes = t.layout(t.build_model(env), t.page)
        assert scenes
        for sc in scenes:
            assert check_scene(sc) == [], (name, check_scene(sc))
            assert sc.width <= t.page.width + 0.01
        again = t.layout(t.build_model(shuffled(env, 7)), t.page)
        assert [render_svg(s) for s in scenes] == [render_svg(s) for s in again]
        products = {n.data.get("product") for sc in scenes for n in sc.nodes
                    if n.kind == "glyph"}
        assert "nci" in products
        assert ("ndb" if type_id == "ndb" else "prism") in products
        assert {"nkp": "nkp", "ndb": "ndb", "objects": "nus", "files": "nus"}[type_id] \
            in products


def test_no_counts_or_sizes_in_the_figure(envs: dict[str, Any]) -> None:
    """Detail belongs in the tables: no capacities, counts or chips in the figures."""
    for type_id, env in (("nkp", "pc01"), ("ndb", "pc01"), ("objects", "pc01"),
                         ("files", "pc02")):
        text = " ".join(_texts(type_id, envs[env]))
        for word in ("GiB", "TiB", "pods", "namespaces", "vCPU", "registered"):
            assert word not in text, (type_id, word)
        assert not re.search(r"(?<![\w-])\d[\d,]* (objects|buckets|shares|nodes|databases)\b",
                             text), type_id


@pytest.mark.parametrize("type_id", SERVICE_TYPES)
def test_envelope_round_trip_and_rename(envs: dict[str, Any], type_id: str) -> None:
    from ntnxvision_core.diagrams.edits import apply_ops, clean_ops

    env = envs["multi"]
    t = TYPES[type_id]
    m = t.build_model(env)
    tid, back = model_from_envelope(model_envelope(type_id, m))
    assert tid == type_id and t.to_doc(back) == t.to_doc(m)
    target = m.clusters[0].boxes[0].id
    applied = apply_ops(t.to_doc(m), clean_ops([{"op": "set", "id": target, "field": "label",
                                                  "value": "Renamed box"}]), t.edit_spec)
    assert applied.problems == []
    texts = [ln.text for sc in t.layout(t.from_doc(applied.payload), t.page)
             for n in sc.nodes for ln in n.lines]
    assert "Renamed box" in texts


def test_lanes_fold_into_a_more_card() -> None:
    items = [S.SvcItem(id=f"b{i}", name=f"bucket-{i:02}", kind="bucket") for i in range(30)]
    m = S.ServiceModel(type="objects", title="Objects design", clusters=[S.SvcCluster(
        id="cluster:x", name="CL-1", boxes=[S.SvcBox(id="os", name="os-1", kind="objectstore",
                                                      lanes=[S.SvcLane(id="os:buckets",
                                                                       title="BUCKETS",
                                                                       items=items, cols=3,
                                                                       noun="buckets")])])])
    (sc,) = TYPES["objects"].layout(m)
    assert check_scene(sc) == []
    texts = [ln.text for n in sc.nodes for ln in n.lines]
    assert "+19 more buckets" in texts and "bucket-29" not in texts


def test_report_sections(envs: dict[str, Any]) -> None:
    from ntnxvision_core.exporters import ExportOptions
    from ntnxvision_core.exporters.content import SceneFigure, build_report

    found = {}
    for name in ("pc01", "pc02"):
        r = build_report(envs[name], ExportOptions(), "Report")
        assert not [w for w in r.warnings if w.startswith("Figure not drawn")]
        for ch in r.chapters:
            for s in ch.sections:
                for b in s.blocks:
                    if isinstance(b, SceneFigure) and b.scene.type in SERVICE_TYPES:
                        found[b.scene.type] = (ch.key, s.heading.split(" ", 1)[-1],
                                                bool(b.summary))
    assert found == {
        "nkp": ("kubernetes", "Kubernetes Platform Design", True),
        "ndb": ("ndb", "Database Service Design", True),
        "objects": ("nus", "Objects Design", True),
        "files": ("nus", "Files Design", True)}


def test_management_plane_has_no_column_heading(envs: dict[str, Any]) -> None:
    """The Prism Central is named once, inside its block: no name or "Management plane,
    N registered clusters" heading above it."""
    texts = _texts("management", envs["pc01"])
    assert not any(t.startswith("Management plane") for t in texts)
    assert texts.count("pc01.demo.local") == 1
