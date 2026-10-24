"""Diagrams editor: icons nested in each other (`diagrams.nesting`): the containment
hierarchy, the slot rules, containers that grow, frames that wrap a collected zone, and
the NC2 / cloud icons."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ntnxvision_core.diagrams import check_scene
from ntnxvision_core.diagrams import edits as E
from ntnxvision_core.diagrams import icons as I
from ntnxvision_core.diagrams import nesting as N
from ntnxvision_core.diagrams import store as ST
from ntnxvision_core.diagrams.render.png import render_png
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.diagrams.scene import Node, Scene
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


def _view(env: Environment, ops: list[dict[str, Any]], type_id: str = "conceptual"
          ) -> ST.View:
    return ST.build_view(env, type_id, E.clean_ops(ops))


def _scene0(env: Environment, type_id: str = "conceptual") -> Scene:
    return ST.build_view(env, type_id, []).scenes[0]


def _p(sc: Scene, ident: str, icon: str, **kw: Any) -> dict[str, Any]:
    return {"op": "place", "id": ident, "icon": icon, "scene": sc.id,
            "x": kw.pop("x", sc.width + 40), "y": kw.pop("y", 20), **kw}


def _inside(o: Node, n: Node) -> bool:
    return (o.x - 0.05 <= n.x and o.y - 0.05 <= n.y and n.x + n.w <= o.x + o.w + 0.05
            and n.y + n.h <= o.y + o.h + 0.05)


def _overlap(a: Node, b: Node) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


# ─── the hierarchy ─────────────────────────────────────────────────────


def test_the_hierarchy_is_explicit_and_uses_catalog_kinds() -> None:
    ids = {i.id for i in I.CATALOG}
    for parent, kids in N.CHILDREN.items():
        assert parent in ids, parent
        assert set(kids) <= ids, (parent, set(kids) - ids)
    chain = [("datacenter", "az"), ("az", "pod"), ("pod", "pc"), ("pod", "cluster"),
             ("cluster", "node"), ("node", "nic"), ("node", "cvm"),
             ("cloud_aws", "region"), ("cloud_azure", "region"), ("cloud_gcp", "region"),
             ("region", "nc2_cluster"), ("nc2_cluster", "bm_node"), ("bm_node", "nic"),
             ("vpc", "overlay"), ("policy", "group"), ("datacenter", "directconnect"),
             ("cloud_net", "nc2_cluster")]
    for parent, child in chain:
        assert N.can_contain(parent, child), (parent, child)
    for parent, child in [("node", "az"), ("nic", "node"), ("pc", "cluster"),
                          ("cluster", "az"), ("region", "datacenter"), ("note", "text"),
                          (None, "az"), ("az", None)]:
        assert not N.can_contain(parent, child), (parent, child)
    # Nothing contains itself, and the frames are all containers.
    assert all(k not in kids for k, kids in N.CHILDREN.items())
    assert all(I.ICONS[f].look.container for f in N.FRAMES)


def test_slot_rules() -> None:
    assert N.slot("pod", "pc") == "top"
    assert N.slot("region", "pc") == "top"
    assert N.slot("node", "nic") == "badge_br"
    assert N.slot("bm_node", "cvm") == "badge_bl"
    assert N.slot("cluster", "container") == "strip"
    assert N.slot("vpc", "overlay") == "strip"
    assert N.slot("pod", "cluster") == "flow"
    assert N.slot("datacenter", "az") == "flow"
    assert N.slot("switch", "nic") == "flow"


def test_collected_elements_are_classified() -> None:
    from ntnxvision_core.diagrams.scene import Style

    def node(i: str, kind: str = "box") -> Node:
        return Node(id=i, kind=kind, x=0, y=0, w=10, h=10, style=Style())

    cases = {("site:pc01.example.lab", "container"): "az",
             ("site:pc01.example.lab:pc", "box"): "pc",
             ("site:pc01.example.lab:pod:A", "container"): "pod",
             ("az:remote.example.lab", "box"): "remote",
             ("cluster:0000-1", "box"): "cluster",
             ("cluster:0000-1:nodes", "box"): "node",
             ("profile:ab12:host", "container"): "node",
             ("profile:ab12:vs:vs0:nic:eth0", "box"): "nic",
             ("vpc:1", "box"): "vpc",
             ("cluster:0000-1:tag", "tag"): None,
             ("legend:x", "box"): None}
    for (ident, kind), want in cases.items():
        assert N.collected_kind(node(ident, kind)) == want, ident


def test_catalog_has_the_datacenter_and_nc2_icons() -> None:
    docs = {d["id"]: d for d in I.catalog_docs()}
    assert docs["datacenter"]["container"] and docs["datacenter"]["chip"]["label"] == \
        "DATACENTER"
    assert docs["datacenter"]["children"][:2] == ["az", "remote"]
    cloud = [d for d in docs.values() if d["group"] == "NC2 / cloud"]
    assert {d["id"] for d in cloud} >= {"cloud_aws", "cloud_azure", "cloud_gcp", "region",
                                        "nc2_cluster", "bm_node", "directconnect",
                                        "expressroute", "interconnect", "vpn",
                                        "cloud_net", "transit", "flow_gateway"}
    assert {docs[c]["chip"]["label"] for c in N.CLOUDS} == {"AWS", "AZURE", "GCP"}
    assert "expressroute" in docs["expressroute"]["keywords"]
    assert "direct connect" in docs["directconnect"]["keywords"]
    # The picker groups keep catalog order: one contiguous NC2 / cloud run.
    groups = [i.group for i in I.CATALOG]
    first = groups.index("NC2 / cloud")
    assert groups[first:first + len(cloud)] == ["NC2 / cloud"] * len(cloud)


# ─── arranging placed icons ────────────────────────────────────────────


def test_children_fall_into_their_slots_and_parents_grow(env: Environment) -> None:
    sc0 = _scene0(env)
    ops = [_p(sc0, "user:dc", "datacenter", w=60, h=30),
           _p(sc0, "user:az", "az", parent="user:dc"),
           _p(sc0, "user:az2", "az", parent="user:dc", label="Second zone"),
           _p(sc0, "user:pod", "pod", parent="user:az"),
           _p(sc0, "user:c1", "cluster", parent="user:pod"),
           _p(sc0, "user:c2", "cluster", parent="user:pod"),
           _p(sc0, "user:pc", "pc", parent="user:pod"),
           _p(sc0, "user:n1", "node", parent="user:c1"),
           _p(sc0, "user:nic", "nic", parent="user:n1"),
           _p(sc0, "user:cvm", "cvm", parent="user:n1")]
    view = _view(env, ops)
    assert view.problems == []
    sc = view.scenes[0]
    assert check_scene(sc) == []
    n = {x.id: x for x in sc.nodes}
    # Every child inside its parent, and the nodes say so.
    for child, parent in [("user:az", "user:dc"), ("user:az2", "user:dc"),
                          ("user:pod", "user:az"), ("user:c1", "user:pod"),
                          ("user:pc", "user:pod"), ("user:n1", "user:c1"),
                          ("user:nic", "user:n1"), ("user:cvm", "user:n1")]:
        assert _inside(n[parent], n[child]), (child, parent)
        assert n[child].parent == parent
    # The datacenter grew past the 60 x 30 asked (ancestors grow with their children).
    assert n["user:dc"].w > 60 and n["user:dc"].h > 30
    # Siblings in a tidy row: same top, a consistent gap, no overlap.
    assert n["user:az"].y == n["user:az2"].y
    assert n["user:az2"].x - (n["user:az"].x + n["user:az"].w) == N.GAP
    assert n["user:c1"].y == n["user:c2"].y and not _overlap(n["user:c1"], n["user:c2"])
    # Prism Central on top of the clusters, full width.
    assert n["user:pc"].y + n["user:pc"].h <= n["user:c1"].y
    assert n["user:pc"].w >= n["user:c2"].x + n["user:c2"].w - n["user:c1"].x - 0.05
    # The NIC is a small badge in the node's bottom-right corner, the CVM bottom-left.
    nic, node, cvm = n["user:nic"], n["user:n1"], n["user:cvm"]
    assert (nic.w, nic.h) == (N.BADGE_W, N.BADGE_H)
    assert node.x + node.w - (nic.x + nic.w) == N.BADGE_IN
    assert node.y + node.h - (nic.y + nic.h) == N.BADGE_IN
    assert cvm.x - node.x == N.BADGE_IN and not _overlap(nic, cvm)
    # Frames paint under what they hold.
    order = [x.id for x in sc.nodes]
    assert order.index("user:dc") < order.index("user:az") < order.index("user:pod")
    meta = sc.meta["placed"]
    assert meta["user:nic"]["slot"] == "badge_br" and meta["user:az"]["parent"] == "user:dc"
    assert meta["user:dc"]["min_w"] <= n["user:dc"].w


def test_resize_keeps_children_inside(env: Environment) -> None:
    sc0 = _scene0(env)
    ops = [_p(sc0, "user:pod", "pod"), _p(sc0, "user:c1", "cluster", parent="user:pod"),
           _p(sc0, "user:c2", "cluster", parent="user:pod")]
    n = {x.id: x for x in _view(env, ops).scenes[0].nodes}
    need = _view(env, ops).scenes[0].meta["placed"]["user:pod"]
    # Too small: the minimum wins. Larger: the author's size, children where they were.
    small = ops + [{"op": "position", "id": "user:pod", "x": n["user:pod"].x,
                    "y": n["user:pod"].y, "w": 30, "h": 20}]
    s = {x.id: x for x in _view(env, small).scenes[0].nodes}
    assert (s["user:pod"].w, s["user:pod"].h) == (need["min_w"], need["min_h"])
    big = ops + [{"op": "position", "id": "user:pod", "x": n["user:pod"].x,
                  "y": n["user:pod"].y, "w": 600, "h": 200}]
    b = {x.id: x for x in _view(env, big).scenes[0].nodes}
    assert (b["user:pod"].w, b["user:pod"].h) == (600, 200)
    assert (b["user:c1"].x, b["user:c1"].y) == (n["user:c1"].x, n["user:c1"].y)


def test_unnest_cycles_and_delete(env: Environment) -> None:
    sc0 = _scene0(env)
    ops = [_p(sc0, "user:az", "az"), _p(sc0, "user:pod", "pod", parent="user:az"),
           _p(sc0, "user:c", "cluster", parent="user:pod")]
    # Dragged out: free again at the drop point.
    out = ops + [{"op": "position", "id": "user:pod", "x": 12, "y": 900,
                  "parent": None}]
    n = {x.id: x for x in _view(env, out).scenes[0].nodes}
    assert (n["user:pod"].x, n["user:pod"].y) == (12, 900)
    assert n["user:pod"].parent is None and n["user:c"].parent == "user:pod"
    # A container cannot go inside what it holds.
    loop = ops + [{"op": "position", "id": "user:az", "x": 0, "y": 0, "parent": "user:c"}]
    assert [p.reason for p in _view(env, loop).problems] == ["an icon cannot go inside "
                                                             "itself"]
    # A kind the parent cannot hold stays free (a datacenter in a cluster).
    odd = ops + [_p(sc0, "user:dc", "datacenter", parent="user:c", x=10, y=1200)]
    n = {x.id: x for x in _view(env, odd).scenes[0].nodes}
    assert n["user:dc"].parent is None and (n["user:dc"].x, n["user:dc"].y) == (10, 1200)
    # Deleting a container deletes what it holds.
    gone = _view(env, ops + [{"op": "delete", "id": "user:az"}])
    assert gone.payload.get("placed") is None


def test_nesting_ops_are_checked() -> None:
    base = {"op": "position", "id": "user:a", "x": 1, "y": 1}
    assert E.clean_ops([{**base, "parent": ""}])[0]["parent"] is None
    assert E.clean_ops([{**base, "wraps": ["site:a", "site:a"]}])[0]["wraps"] == ["site:a"]
    for bad in ({"parent": 3}, {"wraps": "site:a"}, {"wraps": ["x"] * 17}, {"wraps": [""]}):
        with pytest.raises(E.EditError):
            E.clean_ops([{**base, **bad}])


# ─── collected containers and wrapping frames ──────────────────────────


def test_icons_go_into_collected_containers_which_grow(env: Environment) -> None:
    sc0 = _scene0(env)
    kinds = N.collected_kinds(sc0)
    site = next(i for i, k in kinds.items() if k == "az")
    cluster = next(i for i, k in kinds.items() if k == "cluster")
    before = {x.id: x for x in sc0.nodes}
    ops = [_p(sc0, "user:pod", "pod", parent=site),
           _p(sc0, "user:c", "cluster", parent="user:pod"),
           _p(sc0, "user:n", "node", parent=cluster)]
    sc = _view(env, ops).scenes[0]
    assert check_scene(sc) == []
    n = {x.id: x for x in sc.nodes}
    assert _inside(n[site], n["user:pod"]) and n["user:pod"].parent == site
    assert _inside(n[cluster], n["user:n"])
    assert n[site].h > before[site].h and n[cluster].h > before[cluster].h
    # The generated layout keeps its order: what was above the new room did not move.
    assert (n[site].x, n[site].y) == (before[site].x, before[site].y)
    assert (n[cluster].x, n[cluster].y) == (before[cluster].x, before[cluster].y)
    for i, b in before.items():
        a = n[i]
        assert a.x >= b.x - 0.05 and a.y >= b.y - 0.05, i  # moved aside, never back


def test_a_datacenter_wraps_the_collected_zone(env: Environment, snap: Path) -> None:
    sc0 = _scene0(env)
    kinds = N.collected_kinds(sc0)
    site = next(i for i, k in kinds.items() if k == "az")
    ops = [_p(sc0, "user:dc", "datacenter", wraps=[site]),
           _p(sc0, "user:az2", "az", parent="user:dc", label="Planned zone"),
           _p(sc0, "user:aws", "cloud_aws", y=sc0.height + 400),
           _p(sc0, "user:reg", "region", parent="user:aws"),
           _p(sc0, "user:nc2", "nc2_cluster", parent="user:reg"),
           _p(sc0, "user:bm", "bm_node", parent="user:nc2"),
           _p(sc0, "user:dx", "directconnect", x=20, y=sc0.height + 340),
           {"op": "connect", "id": "user:l1", "a": "user:dc", "b": "user:dx"},
           {"op": "connect", "id": "user:l2", "a": "user:dx", "b": "user:aws"}]
    view = _view(env, ops)
    assert view.problems == []
    sc = view.scenes[0]
    assert check_scene(sc) == []
    n = {x.id: x for x in sc.nodes}
    dc = n["user:dc"]
    assert _inside(dc, n[site]) and n[site].parent == "user:dc"
    assert _inside(dc, n["user:az2"]) and n["user:az2"].x > n[site].x + n[site].w
    assert n["user:az2"].y == n[site].y  # aligned with the collected zone
    assert sc.meta["placed"]["user:dc"]["anchored"] is True
    # Every subtree of the cloud sits in place.
    assert _inside(n["user:reg"], n["user:nc2"]) and _inside(n["user:nc2"], n["user:bm"])
    assert {e.id for e in sc.edges} >= {"user:l1", "user:l2"}
    # Saved through the edit store, it survives a reload and reaches every format.
    ST.save_result(snap, env, "conceptual", ops)
    got = ST.get_result(snap, env, "conceptual")
    assert got["ops"] == E.clean_ops(ops) and got["nest_kinds"][site] == "az"
    again = {x["id"]: x for x in got["scenes"][0]["nodes"]}
    assert (again["user:dc"]["x"], again["user:dc"]["w"]) == (dc.x, dc.w)
    assert b"DATACENTER" in render_svg(sc) and b"Direct Connect" in render_svg(sc)
    assert render_png(sc, 72)[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("type_id", ["conceptual", "logical", "management", "dr",
                                     "segments", "vpc", "flow", "network"])
def test_hybrid_example_on_every_figure(env: Environment, type_id: str) -> None:
    from ntnxvision_core.diagrams import TYPES

    if not TYPES[type_id].applies(env):
        pytest.skip(f"no {type_id} figure")
    sc0 = _scene0(env, type_id)
    kinds = N.collected_kinds(sc0)
    tops = [i for i, k in kinds.items() if k in ("az", "remote") and
            next(x for x in sc0.nodes if x.id == i).parent is None]
    ops = ([_p(sc0, "user:dc", "datacenter", wraps=tops[:1])] if tops else []) + [
        _p(sc0, "user:gcp", "cloud_gcp"), _p(sc0, "user:r", "region", parent="user:gcp"),
        _p(sc0, "user:k", "nc2_cluster", parent="user:r"),
        _p(sc0, "user:b1", "bm_node", parent="user:k"),
        _p(sc0, "user:b2", "bm_node", parent="user:k"),
        _p(sc0, "user:nic", "nic", parent="user:b1")]
    view = _view(env, ops, type_id)
    assert view.problems == []
    sc = view.scenes[0]
    problems = [p for p in check_scene(sc) if "user:gcp" not in p]
    assert problems == []
