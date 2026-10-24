"""Diagrams-page edits on the design figures (A2 to A5): every element an author may
remove can be removed, every palette item can be added, and the edited figure always
lays out cleanly (`check_scene() == []`); the estate map is never edited on its own."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from ntnxvision_core.diagrams import TYPES, check_scene
from ntnxvision_core.diagrams import edits as E
from ntnxvision_core.diagrams import store as ST
from ntnxvision_core.diagrams import tokens as K
from ntnxvision_core.diagrams.icons import LOOKS, PALETTE_LOOK
from ntnxvision_core.diagrams.scene import Scene

DESIGN_TYPES = ("logical", "management", "network", "dr")
#: Estates exercised per type: a small and a mid-size one with every feature drawn.
ESTATES = ("small", "medium", "enterprise", "pc01", "pc02")


def _layout(type_id: str, payload: dict[str, Any]) -> list[Scene]:
    t = TYPES[type_id]
    return t.layout(t.from_doc(payload), t.page)


def _problems(scenes: list[Scene]) -> list[str]:
    return [f"{sc.id}: {p}" for sc in scenes for p in check_scene(sc)]


def _cases(env: Any, type_id: str) -> tuple[dict[str, Any], list[str]]:
    t = TYPES[type_id]
    gen = t.to_doc(t.build_model(env))
    spec = t.edit_spec
    slots = E.walk(gen, spec)
    removable = sorted(i for i, sl in slots.items()
                       if (c := spec.collection(sl.field)) is not None and c.delete)
    return gen, removable


@pytest.mark.parametrize("type_id", DESIGN_TYPES)
def test_every_type_has_an_edit_spec_that_normalizes(type_id: str) -> None:
    spec = TYPES[type_id].edit_spec
    assert spec.collections and spec.normalize is not None and spec.source is not None


@pytest.mark.parametrize("type_id", DESIGN_TYPES)
@pytest.mark.parametrize("estate", ESTATES)
def test_removing_any_element_lays_out_cleanly(design_envs: dict[str, Any], type_id: str,
                                               estate: str) -> None:
    env = design_envs[estate]
    t = TYPES[type_id]
    if not t.applies(env):
        pytest.skip(f"{estate} has no {type_id} figure")
    gen, removable = _cases(env, type_id)
    assert removable, f"{type_id}: nothing an author can remove"
    assert not _problems(_layout(type_id, gen))
    for ident in removable:
        applied = E.apply_ops(gen, [{"op": "delete", "id": ident}], t.edit_spec)
        assert not applied.problems, (ident, applied.problems)
        assert applied.payload["edited"] is True
        bad = _problems(_layout(type_id, applied.payload))
        assert not bad, f"{type_id} without {ident}: {bad[:3]}"


@pytest.mark.parametrize("type_id", ("logical", "management", "dr"))
@pytest.mark.parametrize("estate", ("small", "pc01"))
def test_every_palette_item_is_drawn_like_a_collected_element(design_envs: dict[str, Any],
                                                   type_id: str, estate: str) -> None:
    env = design_envs[estate]
    t = TYPES[type_id]
    if not t.applies(env):
        pytest.skip(f"{estate} has no {type_id} figure")
    gen, _ = _cases(env, type_id)
    slots = E.walk(gen, t.edit_spec)
    for item in t.edit_spec.palette:
        parent = next(i for i, sl in slots.items() if sl.field in item.parents)
        uid = f"user:{item.id}"
        op = {"op": "add", "parent": parent, "field": item.collection, "index": 0,
              "element": {"id": uid, "palette": item.id, "name": f"Added {item.label}"}}
        applied = E.apply_ops(gen, E.clean_ops([op]), t.edit_spec)
        assert not applied.problems, applied.problems
        scenes = _layout(type_id, applied.payload)
        assert not _problems(scenes), (item.id, _problems(scenes)[:3])
        nodes = {n.id: n for sc in scenes for n in sc.nodes}
        assert uid in nodes, f"{type_id}: the added {item.id} is not drawn"
        # Drawn with the generated element's style, never the amber USER-ADDED box.
        look = LOOKS[PALETTE_LOOK[item.id]]
        assert nodes[uid].style == look.style, (type_id, item.id)
        assert not any(n.parent == uid and n.style == K.TAG_USER for n in nodes.values()), \
            f"{type_id}: a USER-ADDED chip on the added {item.id}"
        assert not any(n.style in (K.USER, K.USER_SITE) for n in nodes.values())
        assert any("(edited)" in sc.caption for sc in scenes)


def test_dangling_links_flows_and_arrays_are_dropped(design_envs: dict[str, Any]) -> None:
    env = design_envs["enterprise"]
    # DR: removing a zone drops every flow and plan chip that touches it.
    t = TYPES["dr"]
    gen = t.to_doc(t.build_model(env))
    site = gen["sites"][0]["id"]
    out = E.apply_ops(gen, [{"op": "delete", "id": site}], t.edit_spec).payload
    assert all(site not in (f["site_a"], f["site_b"]) for f in out["flows"])
    assert all(site not in (p["a"], p["b"]) for p in out["plans"])
    assert len(out["flows"]) < len(gen["flows"])
    # Management: removing a Prism Central drops its AZ pairings.
    t = TYPES["management"]
    gen = t.to_doc(t.build_model(env))
    linked = gen["links"][0]["a"]
    out = E.apply_ops(gen, [{"op": "delete", "id": linked}], t.edit_spec).payload
    assert all(linked not in (lk["a"], lk["b"]) for lk in out["links"])
    # Logical: removing the cluster an array serves removes the array.
    t = TYPES["logical"]
    gen = t.to_doc(t.build_model(env))
    site_doc = next(s for s in gen["sites"] if s["arrays"])
    owner = site_doc["arrays"][0]["cluster"]
    out = E.apply_ops(gen, [{"op": "delete", "id": owner}], t.edit_spec).payload
    assert all(a["cluster"] != owner for s in out["sites"] for a in s["arrays"])


def test_the_estate_map_is_not_edited_on_its_own(design_envs: dict[str, Any],
                                                tmp_path: Path) -> None:
    env = design_envs["xlarge"]
    assert TYPES["estate"].applies(env) and TYPES["estate"].embedded
    assert "estate" not in {t.id for t in ST.applicable(env)}
    with pytest.raises(E.EditError):
        ST.dtype("estate")
    assert ST.edited_models(None, env) == {}


#: (collection, field holding the label) renamed per type in the report tests.
RENAME = {"logical": "clusters", "management": "clusters", "network": "switches",
          "dr": "clusters"}


def _renamed(env: Any, type_id: str, new: str, drawn: set[str]
             ) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
    """The model with one element the figure draws (`drawn` node ids) renamed."""
    t = TYPES[type_id]
    gen = t.to_doc(t.build_model(env))
    slots = E.walk(gen, t.edit_spec)
    ident = next(i for i, sl in sorted(slots.items()) if sl.field == RENAME[type_id]
                 and i in drawn)
    ops = [{"op": "set", "id": ident, "field": "label", "value": new}]
    applied = E.apply_ops(gen, ops, t.edit_spec)
    return t.from_doc(applied.payload), ops, gen


@pytest.mark.parametrize("type_id", DESIGN_TYPES)
def test_saved_edits_reach_every_part_of_a_multi_part_report(design_envs: dict[str, Any],
                                                             type_id: str) -> None:
    """The report draws the saved edit in the figure (every part keeps it), says so in
    the caption of the parts it touches and in the summary, and keeps the summary and
    tables as collected."""
    from ntnxvision_core.exporters import ExportOptions
    from ntnxvision_core.exporters.content import SceneFigure, build_report

    env = design_envs["xlarge"]
    new = f"Renamed {type_id}"

    def figs(report: Any) -> list[SceneFigure]:
        return [b for ch in report.chapters for s in ch.sections for b in s.blocks
                if isinstance(b, SceneFigure) and b.scene.type == type_id]

    plain = build_report(env, ExportOptions(), "x")
    before = figs(plain)
    model, ops, gen = _renamed(env, type_id, new,
                               {n.id for f in before for n in f.scene.nodes})
    edited = build_report(env, ExportOptions(diagram_models={type_id: model}), "x")
    after = figs(edited)
    assert after and len(after) >= 1
    text = [ln.text for f in after for n in f.scene.nodes for ln in n.lines]
    assert any(new in t for t in text), f"{type_id}: the edit is not drawn"
    # Only the parts that draw the edit say "(edited)"; a logical section (one per
    # zone) discloses only its own zone's edits.
    touched = [f for f in after if any(new in ln.text for n in f.scene.nodes
                                       for ln in n.lines)]
    assert touched and all("(edited)" in f.caption for f in touched)
    assert all("(edited)" not in f.caption for f in after if f not in touched), type_id
    disclosure = [b for f in after for b in f.summary if b.startswith("Figure edited")]
    assert disclosure and "renamed to" in disclosure[0]
    if type_id == "logical":
        assert all(not b.startswith("Figure edited") for f in after if f not in touched
                   for b in f.summary)
    # Everything else in the summary is as collected.
    assert [b for f in after for b in f.summary if not b.startswith("Figure edited")] == \
        [b for f in before for b in f.summary]
    # The saved edit is not stale against the snapshot it was made on.
    entry = {"base_hash": E.payload_hash(type_id, gen), "base_model": gen, "ops": ops}
    assert ST.stale_info(type_id, entry, gen, TYPES[type_id].edit_spec) is None


def test_estate_map_lead_and_tiles_stay_as_collected(design_envs: dict[str, Any]) -> None:
    """With a cluster removed from the overview in the editor, the estate map's lead
    bullet and its tile still give the collected counts, as the overview's headers do."""
    from ntnxvision_core.exporters import ExportOptions
    from ntnxvision_core.exporters.content import SceneFigure, build_report

    env = design_envs["xlarge"]
    t = TYPES["conceptual"]
    gen = t.to_doc(t.build_model(env))
    card = next(c for s in gen["sites"] for c in s["cards"] if c.get("kind") == "cluster")
    model = t.from_doc(E.apply_ops(gen, [{"op": "delete", "id": card["id"]}],
                                   t.edit_spec).payload)

    def estate(report: Any) -> SceneFigure:
        return next(b for ch in report.chapters for s in ch.sections for b in s.blocks
                    if isinstance(b, SceneFigure) and b.scene.type == "estate")

    plain = estate(build_report(env, ExportOptions(), "x"))
    edited = estate(build_report(env, ExportOptions(diagram_models={"conceptual": model}),
                                 "x"))
    assert edited.summary[0] == plain.summary[0]
    assert any(b.startswith("Figure edited") for b in edited.summary)

    def tile_text(f: SceneFigure) -> list[str]:
        """The counts and hypervisor lines of every tile (the parts may differ)."""
        return sorted(ln.text for n in f.scene.nodes for ln in n.lines
                      if re.search(r"\d+ (clusters?|nodes?)\b|^(AHV|ESXi) \d", ln.text))

    assert tile_text(edited) == tile_text(plain)
