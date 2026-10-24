"""Arrowhead shapes on connectors: the ``head_start`` / ``head_end`` edge edits (the
Diagrams page's right-click menu), replayed on generated and user connectors, kept in
the connector outline, and drawn the same way by every renderer."""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

import pytest

from ntnxvision_core.diagrams import TYPES, check_scene
from ntnxvision_core.diagrams import edits as E
from ntnxvision_core.diagrams import store as ST
from ntnxvision_core.diagrams.render import heads as H
from ntnxvision_core.diagrams.render.drawio import render_drawio
from ntnxvision_core.diagrams.render.pdf import draw_scene
from ntnxvision_core.diagrams.render.png import render_png
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.diagrams.render.vsdx import render_vsdx
from ntnxvision_core.diagrams.scene import Edge, Style, from_doc, scene_json, to_doc


def _conns(view: Any, type_id: str) -> dict[str, dict[str, Any]]:
    out = ST.connector_outline(view, E.outline(view.payload, view.generated,
                                               TYPES[type_id].edit_spec))
    return {c["id"]: c for c in out}


def _edge(view: Any, ident: str) -> Edge:
    return next(e for s in view.scenes for e in s.edges if e.id == ident)


def _scene_of(view: Any, ident: str) -> Any:
    return next(s for s in view.scenes if any(e.id == ident for e in s.edges))


def _head(c: dict[str, Any], end: str, kind: str) -> dict[str, Any]:
    return {"op": "edge", "id": c["id"], "ends": c["ends"], "field": f"head_{end}",
            "value": kind}


def test_clean_ops_accepts_only_known_heads() -> None:
    ok = E.clean_ops([{"op": "edge", "id": "e", "ends": ["a", "b"], "field": "head_end",
                       "value": "diamond"}])
    assert ok[0]["value"] == "diamond"
    for bad in ("triangle", None, 1):
        with pytest.raises(E.EditError):
            E.clean_ops([{"op": "edge", "id": "e", "ends": ["a", "b"],
                          "field": "head_start", "value": bad}])


def test_heads_on_a_generated_connector_persist_and_draw(design_envs: dict[str, Any]
                                                         ) -> None:
    env = design_envs["medium"]
    view0 = ST.build_view(env, "conceptual", [])
    c = next(x for x in _conns(view0, "conceptual").values()
             if x["origin"] == "collected" and x["parts"])
    ops = E.clean_ops([_head(c, "start", "circle"), _head(c, "end", "diamond")])
    view = ST.build_view(env, "conceptual", ops)
    assert not view.problems
    e = _edge(view, c["id"])
    assert H.kinds(e) == ("circle", "diamond")
    out = _conns(view, "conceptual")[c["id"]]
    assert (out["head_start"], out["head_end"]) == ("circle", "diamond")
    assert out["edited"]
    for s in view.scenes:
        assert check_scene(s) == []
    # Kept in the payload's edge edits, so a reload replays them.
    assert view.payload[E.EDGE_EDITS][c["id"]]["head_end"] == "diamond"
    sc = _scene_of(view, c["id"])
    svg = render_svg(sc).decode()
    assert "<circle" in svg
    # The scene keeps the shapes through its JSON (the editor and the stored bundle).
    assert H.kinds(next(x for x in from_doc(to_doc(sc)).edges if x.id == c["id"])) == \
        ("circle", "diamond")
    # The disclosure counts it.
    assert "with new arrowheads" in (E.disclosure(
        view.generated, view.payload, TYPES["conceptual"].edit_spec) or "")


def test_arrow_switch_after_heads_puts_plain_arrows_back(design_envs: dict[str, Any]
                                                         ) -> None:
    env = design_envs["medium"]
    boxes = [o for o in E.outline(ST.generated(env, "conceptual"),
                                  ST.generated(env, "conceptual"),
                                  TYPES["conceptual"].edit_spec)
             if o["collection"] == "cards"]
    a, b = boxes[0]["id"], boxes[1]["id"]
    user = {"id": "user:h", "ends": [a, b]}
    ops = [{"op": "connect", "id": "user:h", "a": a, "b": b},
           _head(user, "start", "open"), _head(user, "end", "none")]
    view = ST.build_view(env, "conceptual", E.clean_ops(ops))
    e = _edge(view, "user:h")
    assert H.kinds(e) == ("open", "none") and not e.arrow_end
    again = ST.build_view(env, "conceptual", E.clean_ops(ops + [
        {"op": "edge", "id": "user:h", "ends": [a, b], "field": "arrow", "value": "both"}]))
    assert H.kinds(_edge(again, "user:h")) == ("arrow", "arrow")


def _sample(head_start: str, head_end: str) -> Any:
    from ntnxvision_core.diagrams.scene import Scene

    sc = Scene(id="t", type="t", title="t", caption="t", width=120, height=40)
    sc.edges.append(Edge(id="e", src="", dst="", points=[(10, 20), (60, 20), (60, 30),
                                                         (110, 30)],
                         style=Style(stroke="#123456", stroke_w=1.0, dash=(3, 2)),
                         arrow_start=head_start not in ("", "none"),
                         arrow_end=head_end not in ("", "none"),
                         head_start=head_start, head_end=head_end))
    return sc


@pytest.mark.parametrize("kind", [k for k in H.HEADS if k != "none"])
def test_every_renderer_draws_each_head(kind: str) -> None:
    sc = _sample(kind, kind)
    svg = render_svg(sc).decode()
    expect = {"arrow": "<polygon", "diamond": "<polygon", "circle": "<circle",
              "open": 'stroke-linejoin="miter"'}[kind]
    assert svg.count(expect) == 2
    assert render_png(sc, dpi=72)[:4] == b"\x89PNG"
    from fpdf import FPDF

    pdf = FPDF(unit="pt")
    pdf.add_page()
    assert draw_scene(pdf, sc, 10, 10) > 0
    assert bytes(pdf.output())[:4] == b"%PDF"
    drawio = render_drawio([sc]).decode()
    assert f"endArrow={H_DRAWIO[kind]}" in drawio and f"startArrow={H_DRAWIO[kind]}" in drawio
    with zipfile.ZipFile(io.BytesIO(render_vsdx([sc]))) as z:
        page = next(z.read(n).decode() for n in z.namelist() if n.endswith("page1.xml"))
    assert re.search(f"""N=.EndArrow. V=.{H_VISIO[kind]}.""", page)
    assert re.search(f"""N=.BeginArrow. V=.{H_VISIO[kind]}.""", page)


H_DRAWIO = {"arrow": "block", "open": "open", "diamond": "diamond", "circle": "oval"}
H_VISIO = {"arrow": "4", "open": "1", "diamond": "22", "circle": "20"}


def test_stroke_stops_under_a_filled_head_and_default_scenes_are_unchanged() -> None:
    sc = _sample("diamond", "circle")
    pts = H.trimmed(sc.edges[0])
    assert pts[0][0] > 10 and pts[-1][0] < 110
    # No shapes chosen: the scene JSON carries no head keys (older readers, byte-stable).
    plain = _sample("", "")
    plain.edges[0].arrow_end = True
    assert b"head_" not in scene_json(plain)
    assert H.kinds(plain.edges[0]) == ("none", "arrow")
