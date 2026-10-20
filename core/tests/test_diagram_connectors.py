"""Diagrams editor v2: connector edits (user-added connectors, changes to generated
ones), moves across containers, and the export bundle's PNGs, folder package and preview.

Connector edits are operations like the others: replayed on the generated model by
stable id, drawn by the router over every type's layout, disclosed in the figure's
design summary, reported when an end is gone, and byte-stable.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

import ntnxvision_core.sidecar as sc
from ntnxvision_core.diagrams import TYPES, check_scene
from ntnxvision_core.diagrams import edits as E
from ntnxvision_core.diagrams import store as ST
from ntnxvision_core.diagrams.bundle import (
    bundle_bytes,
    bundle_preview,
    file_stem,
    report_scenes,
    write_bundle,
)
from ntnxvision_core.diagrams.connectors import USER_LINK
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.diagrams.scene import scene_json
from ntnxvision_core.exporters import ExportOptions

EM_DASH = chr(0x2014)


def _outline(env: Any, type_id: str) -> list[dict[str, Any]]:
    t = TYPES[type_id]
    gen = ST.generated(env, type_id)
    return E.outline(gen, gen, t.edit_spec)


def _conns(env: Any, type_id: str, ops: list[dict[str, Any]] | None = None
           ) -> list[dict[str, Any]]:
    view = ST.build_view(env, type_id, E.clean_ops(ops or []))
    return ST.connector_outline(view, E.outline(view.payload, view.generated,
                                                TYPES[type_id].edit_spec))


def _boxes(env: Any, type_id: str) -> list[dict[str, Any]]:
    return [o for o in _outline(env, type_id)
            if o["collection"] in ("cards", "clusters", "segments", "vpcs", "policies")]


def _edge(scenes: list[Any], ident: str) -> Any:
    for s in scenes:
        for e in s.edges:
            if e.id == ident:
                return e
    return None


# ─── operations ────────────────────────────────────────────────────────


def test_clean_ops_shapes_connector_edits() -> None:
    ops = E.clean_ops([
        {"op": "connect", "id": "user:1", "a": "x", "b": "y", "label": "  Backup " + EM_DASH},
        {"op": "edge", "id": "e", "ends": ["x", "y"], "field": "bend", "value": 12.26},
        {"op": "edge", "id": "e", "ends": ["x", "y"], "field": "a_side", "value": "left"},
        {"op": "move", "id": "x", "index": 0, "parent": "p"},
    ])
    assert ops[0] == {"op": "connect", "id": "user:1", "a": "x", "b": "y", "label": "Backup ,"}
    assert ops[1]["value"] == 12.5 and ops[2]["value"] == "left"
    assert ops[3]["parent"] == "p"
    sided = E.clean_ops([{"op": "connect", "id": "user:2", "a": "x", "b": "y",
                          "a_side": "right", "b_side": "auto"}])
    assert sided[0]["a_side"] == "right" and "b_side" not in sided[0]
    bad = [
        {"op": "connect", "id": "c", "a": "x", "b": "y"},
        {"op": "connect", "id": "user:1", "a": "x", "b": "x"},
        {"op": "edge", "id": "e", "ends": ["x"], "field": "label", "value": "a"},
        {"op": "edge", "id": "e", "ends": ["x", "y"], "field": "points", "value": []},
        {"op": "edge", "id": "e", "ends": ["x", "y"], "field": "a_side", "value": "up"},
        {"op": "edge", "id": "e", "ends": ["x", "y"], "field": "bend", "value": 9000},
        {"op": "edge", "id": "e", "ends": ["x", "y"], "field": "hidden", "value": "yes"},
        {"op": "connect", "id": "user:1", "a": "x", "b": "y", "a_side": "up"},
    ]
    for op in bad:
        with pytest.raises(E.EditError):
            E.clean_ops([op])


def test_touched_names_connector_ends() -> None:
    assert E.touched({"op": "connect", "id": "user:1", "a": "x", "b": "y"}) >= {"x", "y"}
    assert E.touched({"op": "edge", "id": "e", "ends": ["x", "y"], "field": "a",
                      "value": "z"}) == {"e", "x", "y", "z"}


# ─── user connectors ───────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["medium", "enterprise", "multi3"])
def test_user_connectors_route_cleanly_on_every_figure(design_envs: dict[str, Any],
                                                       name: str) -> None:
    env = design_envs[name]
    for t in ST.applicable(env):
        boxes = _boxes(env, t.id)
        if len(boxes) < 2:
            continue
        ops = E.clean_ops([
            {"op": "connect", "id": "user:a", "a": boxes[0]["id"], "b": boxes[1]["id"],
             "label": "Backup path"},
            {"op": "connect", "id": "user:b", "a": boxes[0]["id"], "b": boxes[-1]["id"]},
        ])
        view = ST.build_view(env, t.id, ops)
        assert not view.problems, (name, t.id, view.problems)
        for s in view.scenes:
            assert check_scene(s) == [], (name, t.id, check_scene(s))
        e = _edge(view.scenes, "user:a")
        if e is None:  # the two ends are drawn in different parts
            continue
        assert e.style == USER_LINK and e.data["origin"] == "user" and e.arrow_end
        assert len(e.points) >= 2
        for p, r in zip(e.points, e.points[1:], strict=False):
            assert p[0] == r[0] or p[1] == r[1], "orthogonal"
        text = " ".join(t_.text for lab in e.labels for t_ in lab.lines)
        assert text == "Backup path"


def test_connectors_are_deterministic(design_envs: dict[str, Any]) -> None:
    env = design_envs["enterprise"]
    boxes = _boxes(env, "conceptual")
    ops = E.clean_ops([{"op": "connect", "id": "user:a", "a": boxes[0]["id"],
                        "b": boxes[3]["id"], "label": "Link"}])
    one = [scene_json(s) for s in ST.build_view(env, "conceptual", ops).scenes]
    two = [scene_json(s) for s in ST.build_view(env, "conceptual", ops).scenes]
    assert one == two


def test_unedited_models_carry_no_connector_keys(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    for t in ST.applicable(env):
        doc = t.to_doc(t.build_model(env))
        assert E.CONNECTORS not in doc and E.EDGE_EDITS not in doc


def test_connector_round_trips_through_the_model(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    t = TYPES["dr"]
    boxes = _boxes(env, "dr")
    ops = E.clean_ops([{"op": "connect", "id": "user:a", "a": boxes[0]["id"],
                        "b": boxes[-1]["id"]}])
    applied = E.apply_ops(ST.generated(env, "dr"), ops, t.edit_spec)
    model = t.from_doc(applied.payload)
    assert t.to_doc(model) == applied.payload
    assert _edge(t.layout(model, t.page), "user:a") is not None


def test_connector_with_a_missing_end_is_a_problem(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    boxes = _boxes(env, "conceptual")
    view = ST.build_view(env, "conceptual", E.clean_ops([
        {"op": "connect", "id": "user:a", "a": boxes[0]["id"], "b": "cluster:gone"}]))
    assert [p.reason for p in view.problems] == ["an end of the connector is not in this "
                                                 "snapshot"]


def test_deleting_an_end_drops_its_connector(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    boxes = _boxes(env, "conceptual")
    view = ST.build_view(env, "conceptual", E.clean_ops([
        {"op": "connect", "id": "user:a", "a": boxes[0]["id"], "b": boxes[1]["id"]},
        {"op": "delete", "id": boxes[1]["id"]}]))
    assert not view.problems and E.CONNECTORS not in view.payload
    assert _edge(view.scenes, "user:a") is None


def test_user_connector_edits_and_delete(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    boxes = _boxes(env, "conceptual")
    a, b, c = boxes[0]["id"], boxes[1]["id"], boxes[2]["id"]
    base = [{"op": "connect", "id": "user:a", "a": a, "b": b}]
    edit = [{"op": "edge", "id": "user:a", "ends": [a, b], "field": "b", "value": c},
            {"op": "edge", "id": "user:a", "ends": [a, c], "field": "label", "value": "L"},
            {"op": "edge", "id": "user:a", "ends": [a, c], "field": "arrow",
             "value": "both"}]
    view = ST.build_view(env, "conceptual", E.clean_ops(base + edit))
    e = _edge(view.scenes, "user:a")
    assert e.dst == c and e.arrow_start and e.arrow_end
    out = {x["id"]: x for x in ST.connector_outline(view, E.outline(
        view.payload, view.generated, TYPES["conceptual"].edit_spec))}
    assert out["user:a"]["b"] == c and out["user:a"]["label"] == "L"
    assert out["user:a"]["delete_op"] == "delete"
    gone = ST.build_view(env, "conceptual", E.clean_ops(
        [*base, {"op": "delete", "id": "user:a"}]))
    assert _edge(gone.scenes, "user:a") is None and not gone.problems


# ─── generated connectors ──────────────────────────────────────────────


def test_generated_connectors_are_listed(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    conns = _conns(env, "dr")
    assert conns and all(c["origin"] == "collected" for c in conns)
    assert all(len(c["ends"]) == 2 and c["a"] and c["b"] for c in conns)
    kinds = {c["kind"] for c in _conns(env, "management")}
    assert "Runs on" in kinds


def test_generated_connector_edits(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    for type_id in ("conceptual", "dr", "management"):
        conns = _conns(env, type_id)
        c = conns[0]
        before = _edge(ST.build_view(env, type_id, []).scenes, c["id"])

        def op(field: str, value: Any, c: dict[str, Any] = c) -> dict[str, Any]:
            return {"op": "edge", "id": c["id"], "ends": c["ends"], "field": field,
                    "value": value}

        view = ST.build_view(env, type_id, E.clean_ops([op("label", "Renamed link")]))
        e = _edge(view.scenes, c["id"])
        assert " ".join(t.text for lab in e.labels for t in lab.lines) == "Renamed link"
        assert e.data.get("edited") == "true"
        out = {x["id"]: x for x in ST.connector_outline(view, E.outline(
            view.payload, view.generated, TYPES[type_id].edit_spec))}
        assert out[c["id"]]["collected_label"] == c["label"]
        hidden = ST.build_view(env, type_id, E.clean_ops([op("hidden", True)]))
        assert _edge(hidden.scenes, c["id"]) is None and not hidden.problems
        sided = ST.build_view(env, type_id, E.clean_ops([op("a_side", "bottom")]))
        assert _edge(sided.scenes, c["id"]).points != before.points
        for s in [*view.scenes, *hidden.scenes, *sided.scenes]:
            assert check_scene(s) == []


def test_reconnect_a_generated_connector(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    c = _conns(env, "dr")[0]
    other = next(o["id"] for o in _boxes(env, "dr") if o["id"] not in c["ends"])
    view = ST.build_view(env, "dr", E.clean_ops([
        {"op": "edge", "id": c["id"], "ends": c["ends"], "field": "b", "value": other}]))
    assert not view.problems
    e = _edge(view.scenes, c["id"])
    assert e.dst == other
    self_loop = ST.build_view(env, "dr", E.clean_ops([
        {"op": "edge", "id": c["id"], "ends": c["ends"], "field": "b", "value": c["a"]}]))
    assert [p.reason for p in self_loop.problems] == ["a connector needs two different ends"]


def test_bend_shifts_the_lane_and_stays_clean(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    boxes = _boxes(env, "conceptual")
    ops = [{"op": "connect", "id": "user:a", "a": boxes[0]["id"], "b": boxes[-1]["id"]}]
    flat = _edge(ST.build_view(env, "conceptual", E.clean_ops(ops)).scenes, "user:a")
    bent_view = ST.build_view(env, "conceptual", E.clean_ops([*ops, {
        "op": "edge", "id": "user:a", "ends": [boxes[0]["id"], boxes[-1]["id"]],
        "field": "bend", "value": 9}]))
    bent = _edge(bent_view.scenes, "user:a")
    info = bent_view.scenes[0].meta["connectors"]["user:a"]
    if info["bend"]:
        assert bent.points != flat.points
    for s in bent_view.scenes:
        assert check_scene(s) == []


def test_edit_of_a_connector_the_data_lost_is_a_problem(design_envs: dict[str, Any]
                                                       ) -> None:
    env = design_envs["medium"]
    c = _conns(env, "dr")[0]
    view = ST.build_view(env, "dr", E.clean_ops([
        {"op": "edge", "id": "flow:gone", "ends": c["ends"], "field": "label",
         "value": "x"}]))
    assert [p.reason for p in view.problems] == ["the connector is not in this figure"]
    gone_end = ST.build_view(env, "dr", E.clean_ops([
        {"op": "edge", "id": c["id"], "ends": ["cluster:gone", c["ends"][1]],
         "field": "label", "value": "x"}]))
    assert [p.reason for p in gone_end.problems] == [
        "an end of the connector is not in this snapshot"]


# ─── disclosure ────────────────────────────────────────────────────────


def test_disclosure_names_connector_edits(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    t = TYPES["dr"]
    gen = ST.generated(env, "dr")
    boxes = _boxes(env, "dr")
    c = _conns(env, "dr")[0]
    ops = E.clean_ops([
        {"op": "connect", "id": "user:a", "a": boxes[0]["id"], "b": boxes[1]["id"]},
        {"op": "edge", "id": c["id"], "ends": c["ends"], "field": "hidden", "value": True},
    ])
    applied = E.apply_ops(gen, ops, t.edit_spec)
    text = E.disclosure(gen, applied.payload, t.edit_spec)
    assert text is not None
    assert "1 connector added by the author" in text and "1 connector removed" in text
    assert EM_DASH not in text
    # Scoped to a zone neither end is in: nothing to disclose there.
    sites = [s["id"] for s in gen["sites"]]
    inside = {s for s in sites if E._inside(E.walk(gen, t.edit_spec), boxes[0]["id"], s)}
    others = [s for s in sites if s not in inside]
    only_user = E.apply_ops(gen, ops[:1], t.edit_spec).payload
    if others and E._inside(E.walk(gen, t.edit_spec), boxes[1]["id"], next(iter(inside))):
        assert E.disclosure(gen, only_user, t.edit_spec, scope={others[0]}) is None


def test_report_figure_draws_and_discloses_connectors(design_envs: dict[str, Any]) -> None:
    from ntnxvision_core.exporters.content import SceneFigure, build_report

    env = design_envs["medium"]
    t = TYPES["dr"]
    boxes = _boxes(env, "dr")
    ops = E.clean_ops([{"op": "connect", "id": "user:a", "a": boxes[0]["id"],
                        "b": boxes[-1]["id"], "label": "Backup path"}])
    model = t.from_doc(E.apply_ops(ST.generated(env, "dr"), ops, t.edit_spec).payload)
    report = build_report(env, ExportOptions.coerce({"diagram_models": {"dr": model}}),
                          eyebrow="Architecture design document")
    figs = [b for ch in report.chapters for s in ch.sections for b in s.blocks
            if isinstance(b, SceneFigure) and b.scene.type == "dr"]
    assert figs and _edge([f.scene for f in figs], "user:a") is not None
    assert any("connector added by the author" in " ".join(f.summary or [])
               for f in figs)
    svg = render_svg(figs[0].scene).decode()
    assert 'id="user:a"' in svg


# ─── moves across containers ───────────────────────────────────────────


def test_collected_cluster_moves_to_another_pod_of_its_zone(design_envs: dict[str, Any]
                                                           ) -> None:
    env = design_envs["enterprise"]
    out = _outline(env, "logical")
    cluster = next(o for o in out if o["collection"] == "clusters" and o["move_targets"])
    target = cluster["move_targets"][0]["id"]
    view = ST.build_view(env, "logical", E.clean_ops([
        {"op": "move", "id": cluster["id"], "index": 0, "parent": target}]))
    assert not view.problems
    moved = {o["id"]: o for o in E.outline(view.payload, view.generated,
                                           TYPES["logical"].edit_spec)}
    assert moved[cluster["id"]]["parent"] == target
    text = E.disclosure(view.generated, view.payload, TYPES["logical"].edit_spec)
    assert text and "moved to POD" in text
    for s in view.scenes:
        assert check_scene(s) == []
    # Another zone's POD is refused, with the reason.
    pods = [o for o in out if o["collection"] == "pods"]
    own_site = next(o for o in out if o["id"] == cluster["parent"])["parent"]
    far = next(p for p in pods if p["parent"] != own_site)
    refused = ST.build_view(env, "logical", E.clean_ops([
        {"op": "move", "id": cluster["id"], "index": 0, "parent": far["id"]}]))
    assert refused.problems and "stays in the availability zone" in \
        refused.problems[0].reason
    assert cluster["move_refusal"] and "availability zone" in cluster["move_refusal"]


def test_user_card_moves_across_zones_collected_does_not(design_envs: dict[str, Any]
                                                        ) -> None:
    env = design_envs["medium"]
    out = _outline(env, "conceptual")
    sites = [o for o in out if o["collection"] == "sites"]
    collected = next(o for o in out if o["collection"] == "cards"
                     and o["parent"] == sites[0]["id"])
    assert collected["move_targets"] == []
    ops = [{"op": "add", "parent": sites[0]["id"], "field": "cards", "index": 0,
            "element": {"id": "user:x", "palette": "pc", "name": "Prism Central"}},
           {"op": "move", "id": "user:x", "index": 1, "parent": sites[1]["id"]},
           {"op": "move", "id": collected["id"], "index": 0, "parent": sites[1]["id"]}]
    view = ST.build_view(env, "conceptual", E.clean_ops(ops))
    assert [p.index for p in view.problems] == [2]
    assert "stays in the availability zone" in view.problems[0].reason
    after = {o["id"]: o for o in E.outline(view.payload, view.generated,
                                           TYPES["conceptual"].edit_spec)}
    assert after["user:x"]["parent"] == sites[1]["id"]


def test_palette_lists_prism_central_storage_and_switch(design_envs: dict[str, Any],
                                                        tmp_path: Path) -> None:
    from ntnxvision_core.store import SnapshotStore

    env = design_envs["medium"]
    snap = SnapshotStore(tmp_path).save(env.model_copy(deep=True), "pal")
    got = ST.get_result(snap, env, "logical")
    ids = [p["id"] for p in got["palette"]]
    assert ids == ["cluster", "pc", "storage", "switch", "note"]
    assert "component" not in ids  # still replays for older saved edits
    old = E.clean_ops([{"op": "add", "parent": _outline(env, "logical")[1]["id"],
                        "field": "clusters", "index": 0,
                        "element": {"id": "user:c", "palette": "component"}}])
    assert not ST.build_view(env, "logical", old).problems
    assert "connectors" in got


# ─── store, stale and carry ────────────────────────────────────────────


def test_saved_connector_edits_go_stale_and_carry(design_envs: dict[str, Any],
                                                  tmp_path: Path) -> None:
    from ntnxvision_core.store import SnapshotStore

    env = design_envs["medium"]
    store = SnapshotStore(tmp_path)
    src = store.save(env.model_copy(deep=True), "week1")
    boxes = _boxes(env, "dr")
    ops = [{"op": "connect", "id": "user:a", "a": boxes[0]["id"], "b": boxes[1]["id"]}]
    saved = ST.save_result(src, env, "dr", ops)
    assert saved["edited"] and saved["ops"] == E.clean_ops(ops)
    # The next week loses the second end: the connector is reported, not dropped.
    newer = env.model_copy(deep=True)
    uuid = boxes[1]["id"].split(":", 1)[1]
    newer.clusters = [c for c in newer.clusters if c.uuid != uuid]
    report = ST.carry_report(src, newer, ["dr"])
    assert report and report[0]["applies"] == 0
    assert report[0]["problems"][0]["reason"] == "an end of the connector is not in this snapshot"


# ─── bundle: PNGs, folder, preview ─────────────────────────────────────


def test_bundle_carries_a_png_per_figure(design_envs: dict[str, Any]) -> None:
    env = design_envs["small"]
    scenes = report_scenes(env)
    data = bundle_bytes(scenes, "x_diagrams", "2026-10-01T00:00:00Z")
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
        pngs = [n for n in z.namelist() if n.startswith("png/")]
        assert pngs == [f"png/{file_stem(i, s)}.png" for i, s in enumerate(scenes, 1)]
        assert all(z.read(n)[:8] == b"\x89PNG\r\n\x1a\n" for n in pngs)
    assert bundle_bytes(scenes, "x_diagrams", "2026-10-01T00:00:00Z") == data


def test_bundle_as_a_folder(design_envs: dict[str, Any], tmp_path: Path) -> None:
    env = design_envs["small"]
    out = tmp_path / "acme_diagrams"
    detail = write_bundle(env, None, out, "folder")
    assert detail.endswith(("diagram", "diagrams")) and out.is_dir()
    assert (out / "acme_diagrams.vsdx").is_file() and (out / "acme_diagrams.drawio").is_file()
    assert sorted(p.name for p in (out / "svg").iterdir())
    assert sorted(p.name for p in (out / "png").iterdir())
    assert not list(tmp_path.glob("*.partial"))
    with pytest.raises(FileExistsError):
        write_bundle(env, None, out, "folder")


def test_bundle_preview_matches_the_bundle(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    scenes = report_scenes(env)
    plan = bundle_preview(scenes, "x_diagrams", "zip", {"dr": ["edited", "stale"]})
    names = [e["name"] for e in plan["entries"]]
    assert names == ["x_diagrams.vsdx", "x_diagrams.drawio", "svg/", "png/"]
    files = dict((n, d) for n, d in __import__(
        "ntnxvision_core.diagrams.bundle", fromlist=["bundle_files"]).bundle_files(
        scenes, "x_diagrams", png=False))
    assert plan["entries"][0]["size_bytes"] == len(files["x_diagrams.vsdx"])
    svgs = plan["entries"][2]["children"]
    assert [c["path"] for c in svgs] == [f"svg/{file_stem(i, s)}.svg"
                                         for i, s in enumerate(scenes, 1)]
    assert all(c["size_bytes"] == len(files[c["path"]]) for c in svgs)
    dr = [c for c in svgs if "data-protection" in c["name"]]
    assert dr and dr[0]["tags"] == ["edited", "stale"]
    assert all(c["approx"] for c in plan["entries"][3]["children"])
    assert plan["count"] == len(scenes) and plan["size_bytes"] > 0


def test_sidecar_preview_and_folder_export(demo: Any, data_dir: Path) -> None:
    run = sc._m_inventory(None, {
        "host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
        "insecure": False, "name": "pc01-preview", "formats": []})
    snap = run["snapshot_path"]
    out = data_dir / "exp"
    params = {"snapshot": snap, "formats": ["pdf", "word"], "export_dir": str(out),
              "file_prefix": "Acme", "diagrams": True, "diagrams_package": "folder"}
    pre = sc._m_export_preview(None, params)
    assert [f["kind"] for f in pre["files"]] == ["PDF", "Word ADD"]
    assert pre["files"][0]["name"].startswith("Acme-pc01-preview-")
    bundle = pre["diagrams"]
    assert bundle and bundle["package"] == "folder" and bundle["name"].endswith("_diagrams")
    assert not out.exists() or not any(out.iterdir())  # a preview writes nothing
    res = sc._m_export(None, params)
    assert res["errors"] == {}
    folder = Path(res["diagrams"]["path"])
    assert folder.is_dir() and folder.name == bundle["name"]
    assert sorted(p.name for p in (folder / "svg").iterdir()) == \
        [c["name"] for c in bundle["entries"][2]["children"]]
    assert res["diagrams"]["size_bytes"] > 0
    # The next preview names the next free stem.
    again = sc._m_export_preview(None, params)
    assert again["diagrams"]["name"] != bundle["name"]
    no_docs = sc._m_export_preview(None, {**params, "formats": ["excel"]})
    assert no_docs["diagrams"] is None and no_docs["files"][0]["kind"] == "Excel"


def test_sidecar_preview_tags_edited_and_stale(demo: Any, data_dir: Path) -> None:
    run = sc._m_inventory(None, {
        "host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
        "insecure": False, "name": "pc01-tags", "formats": []})
    snap = run["snapshot_path"]
    got = sc._m_get_diagram(None, {"snapshot": snap, "type": "conceptual"})
    site = next(o for o in got["outline"] if o["collection"] == "sites")
    sc._m_save_diagram(None, {"snapshot": snap, "type": "conceptual", "ops": [
        {"op": "set", "id": site["id"], "field": "label", "value": "Zone A"}]})
    pre = sc._m_export_preview(None, {"snapshot": snap, "formats": ["pdf"],
                                      "export_dir": str(data_dir / "e"), "diagrams": True})
    tagged = [c for c in pre["diagrams"]["entries"][2]["children"] if c["tags"]]
    assert tagged and all(c["tags"] == ["edited"] for c in tagged)
    assert pre["diagrams"]["edited"] == ["conceptual"] and pre["diagrams"]["stale"] == []


@pytest.mark.parametrize("fmt", ["vsdx", "drawio", "svg", "png"])
def test_sidecar_exports_one_diagram(demo: Any, data_dir: Path, fmt: str) -> None:
    run = sc._m_inventory(None, {
        "host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
        "insecure": False, "name": "pc01-one", "formats": []})
    out = data_dir / "one"
    res = sc._m_export_diagram(None, {"snapshot": run["snapshot_path"], "type": "dr",
                                      "format": fmt, "export_dir": str(out),
                                      "file_prefix": "Acme"})
    path = Path(res["path"])
    assert path.is_file() and path.suffix == f".{fmt}" and path.parent == out
    assert path.name.startswith("Acme-pc01-one-") and "_data-protection" in path.name
    again = sc._m_export_diagram(None, {"snapshot": run["snapshot_path"], "type": "dr",
                                        "format": fmt, "export_dir": str(out),
                                        "file_prefix": "Acme"})
    assert again["path"] != res["path"]  # never overwrites
    with pytest.raises(sc.NutanixApiError):
        sc._m_export_diagram(None, {"snapshot": run["snapshot_path"], "type": "dr",
                                    "format": "pdf"})


def test_sidecar_layout_returns_connectors(demo: Any, data_dir: Path) -> None:
    run = sc._m_inventory(None, {
        "host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
        "insecure": False, "name": "pc01-lay", "formats": []})
    snap = run["snapshot_path"]
    got = sc._m_get_diagram(None, {"snapshot": snap, "type": "conceptual"})
    cards = [o for o in got["outline"] if o["collection"] == "cards"]
    lay = sc._m_layout_diagram(None, {"snapshot": snap, "type": "conceptual", "ops": [
        {"op": "connect", "id": "user:k", "a": cards[0]["id"], "b": cards[1]["id"],
         "label": "Link"}]})
    ids = [c["id"] for c in lay["connectors"]]
    assert "user:k" in ids
    json.dumps(lay)  # JSON-safe for the IPC
    edges = [e for s in lay["scenes"] for e in s["edges"] if e["id"] == "user:k"]
    assert edges and edges[0]["data"]["origin"] == "user"


def test_editable_assets_carry_connector_edits(design_envs: dict[str, Any]) -> None:
    """The .vsdx keeps glue and groups and the .drawio its ends with connector edits."""
    import io
    import xml.etree.ElementTree as ET

    from ntnxvision_core.diagrams.bundle import tab_name
    from ntnxvision_core.diagrams.render.drawio import render_drawio
    from ntnxvision_core.diagrams.render.vsdx import render_vsdx

    from .test_diagram_bundle import check_vsdx

    env = design_envs["medium"]
    t = TYPES["dr"]
    boxes = _boxes(env, "dr")
    c = _conns(env, "dr")[0]
    ops = E.clean_ops([
        {"op": "connect", "id": "user:a", "a": boxes[0]["id"], "b": boxes[-1]["id"],
         "label": "Backup path"},
        {"op": "edge", "id": c["id"], "ends": c["ends"], "field": "a_side", "value": "top"},
    ])
    model = t.from_doc(E.apply_ops(ST.generated(env, "dr"), ops, t.edit_spec).payload)
    scenes = t.layout(model, t.page)
    check_vsdx(render_vsdx(scenes, [tab_name(s) for s in scenes]), scenes)
    drawio = render_drawio(scenes, [tab_name(s) for s in scenes]).decode()
    root = ET.fromstring(drawio)
    cells = [x for x in root.iter("mxCell") if x.get("edge") == "1"]
    assert any(x.get("source") and x.get("target") for x in cells)
    assert "Backup path" in drawio
    assert io.BytesIO(render_svg(scenes[0])).getvalue().count(b'id="user:a"') == 1
