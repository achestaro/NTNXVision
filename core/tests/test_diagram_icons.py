"""Diagrams page: icons placed anywhere, text edits, user-added elements drawn like
collected ones, and the Legend switch (`diagrams.icons`, ops ``place`` / ``position`` /
``text`` in `diagrams.edits`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import ntnxvision_core.sidecar as sc
from ntnxvision_core.diagrams import TYPES, check_scene
from ntnxvision_core.diagrams import edits as E
from ntnxvision_core.diagrams import icons as I
from ntnxvision_core.diagrams import store as ST
from ntnxvision_core.diagrams import text as tx
from ntnxvision_core.diagrams.render.png import render_png
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.diagrams.scene import Scene
from ntnxvision_core.exporters import ExportOptions
from ntnxvision_core.exporters.content import SceneFigure, build_report
from ntnxvision_core.models import Environment
from ntnxvision_core.store import SnapshotStore


@pytest.fixture(scope="module")
def env(_demo_state: Any) -> Environment:
    from ntnxvision_core.synthetic import demo
    from ntnxvision_core.synthetic.scenarios import collect_scenario

    demo.reset()
    out = collect_scenario("medium")
    demo.reset()
    return out


@pytest.fixture
def snap(tmp_path: Path, env: Environment) -> Path:
    return SnapshotStore(tmp_path).save(env.model_copy(deep=True), "medium-week1")


def _view(env: Environment, type_id: str, ops: list[dict[str, Any]]) -> ST.View:
    return ST.build_view(env, type_id, E.clean_ops(ops))


def _first(env: Environment, type_id: str) -> Scene:
    return ST.build_view(env, type_id, []).scenes[0]


def _below(sc: Scene, icon: str, ident: str, x: float = 8.0, dy: float = 12.0,
           **kw: Any) -> dict[str, Any]:
    return {"op": "place", "id": ident, "icon": icon, "scene": sc.id, "x": x,
            "y": sc.height + dy, **kw}


def _text(sc: Scene) -> list[str]:
    return [t.text for n in sc.nodes for t in n.lines] + \
        [t.text for e in sc.edges for lb in e.labels for t in lb.lines]


# ─── the catalog ───────────────────────────────────────────────────────


def test_catalog_has_every_kind_the_engine_draws() -> None:
    ids = {i.id for i in I.CATALOG}
    # Every look a user-added palette element can take is a catalog icon too.
    assert set(I.PALETTE_LOOK.values()) <= ids
    assert {"az", "cluster", "pc", "segment", "vpc", "policy", "note"} <= ids
    assert {i.group for i in I.CATALOG} <= set(I.GROUPS)
    docs = I.catalog_docs()
    assert [d["id"] for d in docs] == [i.id for i in I.CATALOG]
    assert all(d["style"]["stroke"] != "#995800" for d in docs if d["id"] != "note")


@pytest.mark.parametrize("type_id", ["conceptual", "logical", "management", "dr",
                                     "segments", "vpc", "flow", "network"])
def test_every_icon_can_go_on_every_figure(env: Environment, type_id: str) -> None:
    """An AZ and a cluster in Microsegmentation, a VPC on the logical design: any icon
    on any figure, drawn with the generated element's own style, no USER-ADDED box."""
    if not TYPES[type_id].applies(env):
        pytest.skip(f"no {type_id} figure")
    sc0 = _first(env, type_id)
    ops = []
    x, y = 8.0, sc0.height + 14
    for i, icon in enumerate(I.CATALOG):
        ops.append({"op": "place", "id": f"user:i{i}", "icon": icon.id, "scene": sc0.id,
                    "x": x, "y": y})
        x += 240
        if x > 700:
            x, y = 8.0, y + 140
    view = _view(env, type_id, ops)
    assert view.problems == []
    scene = view.scenes[0]
    nodes = {n.id: n for n in scene.nodes}
    for i, icon in enumerate(I.CATALOG):
        n = nodes[f"user:i{i}"]
        assert n.style == icon.look.style, icon.id
        assert n.data["origin"] == "user" and n.data["icon"] == icon.id
        assert n.text.startswith(icon.name[:4]), icon.id
    assert scene.height > sc0.height  # the figure grew to hold them
    assert "USER-ADDED" not in _text(scene)
    assert check_scene(scene) == []
    # Every renderer draws them.
    assert b"user:i0" in render_svg(scene) or b"Availability zone" in render_svg(scene)
    assert render_png(scene, 72)[:8] == b"\x89PNG\r\n\x1a\n"


def test_az_and_cluster_in_microsegmentation_connect_and_persist(
        snap: Path, env: Environment) -> None:
    sc0 = _first(env, "flow")
    ops = [_below(sc0, "az", "user:az", w=240, h=110),
           _below(sc0, "cluster", "user:cl", x=30, dy=50, label="DR-W02"),
           {"op": "connect", "id": "user:c1", "a": "user:cl",
            "b": next(n.id for n in sc0.nodes if n.id.startswith("policy:")),
            "label": "Protects"}]
    view = _view(env, "flow", ops)
    assert view.problems == [] and view.notes == []
    scene = view.scenes[0]
    nodes = {n.id: n for n in scene.nodes}
    # The cluster sits in the placed zone: the zone is its parent, painted under it.
    assert nodes["user:cl"].parent == "user:az"
    assert scene.nodes.index(nodes["user:az"]) < scene.nodes.index(nodes["user:cl"])
    assert any(e.id == "user:c1" and e.points for e in scene.edges)
    out = {o["id"]: o for o in E.outline(view.payload, view.generated,
                                         TYPES["flow"].edit_spec)}
    assert out["user:cl"]["free"] and out["user:cl"]["kind"] == "Cluster"
    assert out["user:cl"]["label"] == "DR-W02" and out["user:cl"]["can_delete"]
    # Saved, reloaded: the same figure.
    ST.save_result(snap, env, "flow", ops)
    got = ST.get_result(snap, env, "flow")
    ids = {n["id"] for n in got["scenes"][0]["nodes"]}
    assert {"user:az", "user:cl", "user:az:tag"} <= ids
    assert any(c["id"] == "user:c1" for c in got["connectors"])
    assert [i["id"] for i in got["icons"]] == [i.id for i in I.CATALOG]


def test_move_resize_rename_and_delete_a_placed_icon(env: Environment) -> None:
    sc0 = _first(env, "conceptual")
    other = next(n.id for n in sc0.nodes if n.id.startswith("cluster:"))
    base = [_below(sc0, "cluster", "user:x"),
            {"op": "connect", "id": "user:l", "a": "user:x", "b": other, "label": None}]
    view = _view(env, "conceptual", [
        *base,
        {"op": "position", "id": "user:x", "x": 300, "y": 400, "w": 160},
        {"op": "set", "id": "user:x", "field": "label", "value": "Colo cluster"},
        {"op": "set", "id": "user:x", "field": "sublabel", "value": "Planned for Q3"}])
    n = next(n for n in view.scenes[0].nodes if n.id == "user:x")
    assert (n.x, n.y, n.w) == (300, 400, 160)
    assert [t.text for t in n.lines] == ["Colo cluster", "Planned for Q3"]
    gone = _view(env, "conceptual", [*base, {"op": "delete", "id": "user:x"}])
    assert gone.problems == []
    assert "placed" not in gone.payload and "connectors" not in gone.payload
    stale = _view(env, "conceptual", [{"op": "position", "id": "user:nope", "x": 1, "y": 1}])
    assert stale.problems[0].reason == "the icon is not in this diagram"
    unknown = _view(env, "conceptual", [_below(sc0, "toaster", "user:t")])
    assert unknown.problems[0].reason == "this kind of icon is not known"


def test_place_and_text_ops_are_checked() -> None:
    for bad in ({"op": "place", "id": "x", "icon": "az", "scene": "s", "x": 1, "y": 1},
                {"op": "place", "id": "user:a", "icon": "az", "scene": "s", "x": -1, "y": 1},
                {"op": "place", "id": "user:a", "icon": "", "scene": "s", "x": 1, "y": 1},
                {"op": "position", "id": "user:a", "x": 1, "y": 1, "w": 1},
                {"op": "text", "id": "n", "line": -1, "value": "x"},
                {"op": "text", "id": "n", "line": True, "value": "x"},
                {"op": "text", "id": "n", "line": 0, "value": "x" * 201}):
        with pytest.raises(E.EditError):
            E.clean_ops([bad])
    [op] = E.clean_ops([{"op": "place", "id": "user:a", "icon": "az", "scene": "s",
                         "x": 10.26, "y": 3, "label": "A — B", "junk": 1}])
    assert op == {"op": "place", "id": "user:a", "icon": "az", "scene": "s", "x": 10.5,
                  "y": 3, "label": "A , B"}


# ─── text edits ────────────────────────────────────────────────────────


def test_every_kind_of_text_can_be_rewritten(env: Environment) -> None:
    sc0 = _first(env, "conceptual")
    zone = next(n for n in sc0.nodes if n.kind == "container" and len(n.lines) > 1)
    chip = next(n for n in sc0.nodes if n.kind == "tag")
    legend = next(n for n in sc0.nodes if n.id.startswith("legend:") and n.lines)
    edge = next(e for e in sc0.edges if e.labels)
    long = "A much longer zone heading that cannot fit on one line of this box at all"
    ops = [{"op": "text", "id": zone.id, "line": 0, "value": long},
           {"op": "text", "id": zone.id, "line": 1, "value": "Primary site"},
           {"op": "text", "id": chip.id, "line": 0, "value": "PRIMARY AZ"},
           {"op": "text", "id": legend.id, "line": 0, "value": "Clusters we run"},
           {"op": "text", "id": I.edge_key(edge.id, 0), "line": 0, "value": "Pairing"}]
    view = _view(env, "conceptual", ops)
    assert view.problems == []
    scene = view.scenes[0]
    nodes = {n.id: n for n in scene.nodes}
    z = nodes[zone.id]
    # Fitted into its box: shrunk to 6 pt at most, then ellipsised.
    assert z.lines[0].size >= tx.MIN_SIZE and z.lines[0].text.endswith(tx.ELLIPSIS)
    assert z.lines[0].x + z.lines[0].width <= z.w
    assert z.lines[1].text == "Primary site"
    c = nodes[chip.id]
    assert c.text == "PRIMARY AZ" and c.w > chip.w  # a chip grows with its text
    assert nodes[legend.id].text == "Clusters we run"
    e = next(x for x in scene.edges if x.id == edge.id)
    assert e.labels[0].lines[0].text == "Pairing"
    assert check_scene(scene) == []
    # Back to generated: a None value drops the edit.
    back = _view(env, "conceptual", [*ops, *({"op": "text", "id": o["id"], "line": o["line"],
                                               "value": None} for o in ops)])
    assert "text_edits" not in back.payload
    assert _text(back.scenes[0]) == _text(sc0)


def test_edits_are_disclosed_in_the_report(env: Environment) -> None:
    sc0 = _first(env, "conceptual")
    zone = next(n for n in sc0.nodes if n.kind == "container")
    ops = E.clean_ops([_below(sc0, "pc", "user:pc"),
                       {"op": "text", "id": zone.id, "line": 0, "value": "Primary"}])
    t = TYPES["conceptual"]
    gen = ST.generated(env, "conceptual")
    edited = E.apply_ops(gen, ops, t.edit_spec).payload
    text = E.disclosure(gen, edited, t.edit_spec)
    assert text is not None
    assert "1 user-added item" in text and "1 text line rewritten" in text


# ─── the legend ────────────────────────────────────────────────────────


def test_legend_switch_in_reports_and_downloads(env: Environment, data_dir: Path) -> None:
    from ntnxvision_core.paths import snapshots_dir

    snap = SnapshotStore(snapshots_dir()).save(env.model_copy(deep=True), "medium-week1")
    def figures(legend: bool) -> list[Scene]:
        report = build_report(env, ExportOptions.coerce({"diagram_legend": legend}), "Test")
        return [b.scene for ch in report.chapters
                for b in [*ch.blocks, *(x for s in ch.sections for x in s.blocks)]
                if isinstance(b, SceneFigure) and b.scene.type in TYPES]

    shown, hidden = figures(True), figures(False)
    assert len(shown) == len(hidden) and shown
    assert any(n.id.startswith("legend:") for s in shown for n in s.nodes)
    assert not any(n.id.startswith("legend:") for s in hidden for n in s.nodes)
    assert not any(e.id.startswith("legend:") for s in hidden for e in s.edges)
    for a, b in zip(shown, hidden, strict=True):
        assert b.height <= a.height
        if "legend_cut" in a.meta:
            assert b.height == a.meta["legend_cut"]
    # The Diagrams page's downloads follow the switch too.
    out = sc._m_export_diagram(None, {"snapshot": str(snap), "type": "conceptual",
                                      "format": "svg", "diagram_legend": False,
                                      "export_dir": str(data_dir / "x")})
    svg = Path(out["path"]).read_bytes()
    assert b"Cluster (Prism Element)" not in svg
    out = sc._m_export_diagram(None, {"snapshot": str(snap), "type": "conceptual",
                                      "format": "svg", "export_dir": str(data_dir / "y")})
    assert b"Cluster (Prism Element)" in Path(out["path"]).read_bytes()


def test_legend_is_compact_legible_and_cut_only_when_below(env: Environment) -> None:
    from ntnxvision_core.diagrams.build import LEGEND_GAP, LEGEND_SIZE

    sc0 = _first(env, "conceptual")
    labels = [n for n in sc0.nodes if n.id.startswith("legend:") and n.kind == "label"]
    swatches = [n for n in sc0.nodes if n.kind == "swatch"]
    assert labels and all(n.h <= 9 for n in labels)
    assert all(n.w <= 11 and n.h <= 6 for n in swatches)
    # Legible: never smaller than the cards' detail text it explains.
    details = [t.size for n in sc0.nodes if n.kind == "box" for t in n.lines if not t.bold]
    assert all(t.size == LEGEND_SIZE for n in labels for t in n.lines)
    assert max(details) <= LEGEND_SIZE
    top = min(n.y for n in sc0.nodes if n.id.startswith("legend:"))
    content = max(n.y + n.h for n in sc0.nodes if not n.id.startswith("legend:"))
    assert top - content >= LEGEND_GAP - 0.01
    assert sc0.meta["legend_cut"] < sc0.height


def test_placed_icons_push_the_legend_down(env: Environment) -> None:
    from ntnxvision_core.diagrams.build import LEGEND_GAP

    sc0 = _first(env, "conceptual")
    legend_top = min(n.y for n in sc0.nodes if n.id.startswith("legend:"))
    # An icon put on the legend, and one under it: the legend moves under both and the
    # figure grows by as much, so the legend stays last.
    view = _view(env, "conceptual", [
        _below(sc0, "note", "user:on", dy=-(sc0.height - legend_top)),
        _below(sc0, "cluster", "user:under"),
    ])
    sc1 = view.scenes[0]
    nodes = {n.id: n for n in sc1.nodes}
    placed_bottom = max(nodes[i].y + nodes[i].h for i in ("user:on", "user:under"))
    legend = [n for n in sc1.nodes if n.id.startswith("legend:")]
    assert min(n.y for n in legend) >= placed_bottom + LEGEND_GAP - 0.01
    assert sc1.height >= max(n.y + n.h for n in legend)
    assert sc1.meta["legend_cut"] < sc1.height
    # An icon well above the legend leaves it where it was.
    up = _view(env, "conceptual", [{"op": "place", "id": "user:up", "icon": "text",
                                     "scene": sc0.id, "x": sc0.width - 60, "y": 2}])
    assert min(n.y for n in up.scenes[0].nodes if n.id.startswith("legend:")) == legend_top
