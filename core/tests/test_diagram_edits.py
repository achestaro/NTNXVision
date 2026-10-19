"""Diagrams page edits: operations, store, stale detection, carry forward, exports.

Edits are operations replayed by stable id on the generated model (`diagrams.edits`),
saved per snapshot in ``<name>.ntnxdiagrams.json`` (`diagrams.store`). Exports draw the
edited model in figures only; tables always come from the generated model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import ntnxvision_core.sidecar as sc
from ntnxvision_core.diagrams import TYPES, check_scene
from ntnxvision_core.diagrams import edits as E
from ntnxvision_core.diagrams import scene as S
from ntnxvision_core.diagrams import store as ST
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.exporters import ExportOptions
from ntnxvision_core.exporters.content import SceneFigure, Table, build_report
from ntnxvision_core.models import Environment
from ntnxvision_core.store import SnapshotStore
from ntnxvision_core.store import snapshot as snapstore

SPEC = TYPES["conceptual"].edit_spec
EM_DASH = chr(0x2014)


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


def _outline(env: Environment) -> list[dict[str, Any]]:
    gen = ST.generated(env, "conceptual")
    return E.outline(gen, gen, SPEC)


def _ops(env: Environment) -> list[dict[str, Any]]:
    """Rename, reorder, add, note, delete: what the e2e test does too."""
    out = _outline(env)
    sites = [o for o in out if o["collection"] == "sites"]
    cards = [o for o in out if o["collection"] == "cards" and o["parent"] == sites[0]["id"]]
    return [
        {"op": "set", "id": cards[0]["id"], "field": "label", "value": "SQL tier"},
        {"op": "move", "id": cards[2]["id"], "index": 0},
        {"op": "add", "parent": sites[0]["id"], "field": "cards", "index": 9,
         "element": {"id": "user:backup-1", "palette": "storage",
                     "name": "Backup appliance"}},
        {"op": "set", "id": "user:backup-1", "field": "sublabel", "value": "Not collected"},
        {"op": "delete", "id": cards[1]["id"]},
    ]


def _scenes(env: Environment, ops: list[dict[str, Any]]) -> list[S.Scene]:
    return ST.build_view(env, "conceptual", E.clean_ops(ops)).scenes


def _text(sc_: S.Scene) -> list[str]:
    return [t.text for n in sc_.nodes for t in n.lines]


# ─── operations ────────────────────────────────────────────────────────


def test_clean_ops_rejects_malformed_edits() -> None:
    with pytest.raises(E.EditError):
        E.clean_ops([{"op": "explode", "id": "x"}])
    with pytest.raises(E.EditError):  # a new element must carry a user: id
        E.clean_ops([{"op": "add", "parent": None, "field": "sites", "index": 0,
                      "element": {"id": "cluster:forged", "name": "x"}}])
    with pytest.raises(E.EditError):
        E.clean_ops([{"op": "set", "id": "x", "field": "nodes", "value": "999"}])
    with pytest.raises(E.EditError):
        E.clean_ops([{"op": "move", "id": "x", "index": -1}])
    with pytest.raises(E.EditError):
        E.clean_ops("not a list")
    with pytest.raises(E.EditError):
        E.clean_ops([{"op": "delete", "id": "x"}] * (E.MAX_OPS + 1))
    # Text is trimmed, em dashes never reach a figure, unknown element keys are dropped.
    [op] = E.clean_ops([{"op": "set", "id": "a", "field": "label",
                         "value": f"  Prod {EM_DASH} SQL  ", "extra": 1}])
    assert op == {"op": "set", "id": "a", "field": "label", "value": "Prod , SQL"}
    [add] = E.clean_ops([{"op": "add", "parent": "site:x", "field": "cards", "index": 0,
                          "element": {"id": "user:1", "name": "n", "nodes": 99}}])
    assert "nodes" not in add["element"]


def test_edits_apply_by_id(env: Environment) -> None:
    ops = _ops(env)
    gen = ST.generated(env, "conceptual")
    applied = E.apply_ops(gen, E.clean_ops(ops), SPEC)
    assert applied.problems == []
    site = applied.payload["sites"][0]
    ids = [c["id"] for c in site["cards"]]
    names = {c["id"]: c["name"] for c in site["cards"]}
    assert names[ops[0]["id"]] == "SQL tier"
    assert ids[0] == ops[1]["id"]  # moved to the front
    assert ids[-1] == "user:backup-1"
    assert ops[4]["id"] not in ids
    user = site["cards"][-1]
    assert user["kind"] == "user" and user["note"] == "Not collected"
    assert user["data"] == {"origin": "user"}
    # The generated payload is never touched.
    assert gen == ST.generated(env, "conceptual")


def test_edited_figure_styles_user_elements(env: Environment) -> None:
    [scene] = _scenes(env, _ops(env))
    assert check_scene(scene) == []
    node = scene.node("user:backup-1")
    assert node.style.dash is not None and node.style.stroke == "#995800"
    tag = scene.node("user:backup-1:tag")
    assert tag.text == "USER-ADDED"
    text = _text(scene)
    assert "SQL tier" in text and "Backup appliance" in text
    assert any(lab == "User-added (not collected)" for lab in text)
    assert not any(EM_DASH in t for t in text)


def test_user_zone_and_note(env: Environment) -> None:
    ops = [{"op": "add", "parent": None, "field": "sites", "index": 99,
            "element": {"id": "user:az", "palette": "az", "name": "Colo east"}},
           {"op": "add", "parent": "user:az", "field": "cards", "index": 0,
            "element": {"id": "user:note", "palette": "note",
                        "name": "Planned for Q3, not yet deployed"}}]
    [scene] = _scenes(env, ops)
    assert check_scene(scene) == []
    assert scene.node("user:az").style.dash is not None
    assert scene.node("user:az:tag").text == "USER-ADDED"
    assert scene.node("user:note:tag").text == "USER-ADDED"
    # The caption counts only collected zones and names the author's.
    assert "plus 1 user-added zone" in scene.caption
    assert check_scene(scene) == []
    # A component cannot go to the top level, an AZ cannot go into a zone.
    bad = E.apply_ops(ST.generated(env, "conceptual"), E.clean_ops([
        {"op": "add", "parent": None, "field": "cards", "index": 0,
         "element": {"id": "user:x", "palette": "cluster"}},
        {"op": "add", "parent": "user:nowhere", "field": "cards", "index": 0,
         "element": {"id": "user:y", "palette": "cluster"}},
    ]), SPEC)
    assert [p.index for p in bad.problems] == [0, 1]


def test_role_chip_and_cluster_note(env: Environment) -> None:
    card = next(o for o in _outline(env) if o["collection"] == "cards")
    assert [r["value"] for r in card["roles"]] == ["workload", "hosts_pc", "mgmt", "dr_target"]
    [scene] = _scenes(env, [
        {"op": "set", "id": card["id"], "field": "role", "value": "dr_target"},
        {"op": "set", "id": card["id"], "field": "sublabel", "value": "Moves to AZ 2 in 2027"}])
    assert scene.node(f"{card['id']}:tag").text == "DR TARGET"
    assert "Moves to AZ 2 in 2027" in scene.node(card["id"]).text
    [plain] = _scenes(env, [{"op": "set", "id": card["id"], "field": "role",
                             "value": "workload"}])
    assert f"{card['id']}:tag" not in {n.id for n in plain.nodes}
    bad = E.apply_ops(ST.generated(env, "conceptual"), E.clean_ops(
        [{"op": "set", "id": card["id"], "field": "role", "value": "boss"}]), SPEC)
    assert bad.problems[0].reason == "not a valid role"


def test_deleting_a_zone_drops_its_links(env: Environment) -> None:
    gen = ST.generated(env, "conceptual")
    assert gen["links"]
    site = gen["sites"][1]["id"]
    applied = E.apply_ops(gen, [{"op": "delete", "id": site}], SPEC)
    assert all(site not in (lk["a"], lk["b"]) for lk in applied.payload["links"])
    scenes = TYPES["conceptual"].layout(TYPES["conceptual"].from_doc(applied.payload))
    assert all(check_scene(s) == [] for s in scenes)


def test_replay_is_deterministic(env: Environment) -> None:
    ops = _ops(env)
    first = [render_svg(s) for s in _scenes(env, ops)]
    second = [render_svg(s) for s in _scenes(env, json.loads(json.dumps(ops)))]
    assert first == second
    a = ST.layout_result(env, "conceptual", E.clean_ops(ops))
    b = ST.layout_result(env, "conceptual", E.clean_ops(ops))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_missing_elements_are_reported(env: Environment) -> None:
    result = ST.layout_result(env, "conceptual", E.clean_ops(
        [{"op": "delete", "id": "cluster:gone"},
         {"op": "set", "id": "cluster:gone", "field": "label", "value": "x"}]))
    assert [p["index"] for p in result["problems"]] == [0, 1]
    assert result["problems"][0]["reason"] == "the element is not in this snapshot"


def test_outline_describes_elements(env: Environment) -> None:
    applied = E.apply_ops(ST.generated(env, "conceptual"), E.clean_ops(_ops(env)), SPEC)
    out = {o["id"]: o for o in E.outline(applied.payload, ST.generated(env, "conceptual"),
                                         SPEC)}
    renamed = out[_ops(env)[0]["id"]]
    assert renamed["label"] == "SQL tier" and renamed["collected_label"] != "SQL tier"
    assert renamed["origin"] == "collected" and renamed["kind"] == "Cluster"
    assert renamed["source"].startswith("Cluster ")
    user = out["user:backup-1"]
    assert user["origin"] == "user" and user["kind"] == "Component"
    assert "USER-ADDED" in user["source"] and user["can_delete"]
    link = next(o for o in out.values() if o["collection"] == "links")
    assert link["label"].startswith(("AZ pairing: ", "Replication: "))
    assert not link["can_rename"] and not link["can_reorder"]


# ─── store ─────────────────────────────────────────────────────────────


def test_store_round_trip(snap: Path, env: Environment) -> None:
    assert ST.load(snap) == {}
    result = ST.save_result(snap, env, "conceptual", _ops(env))
    path = snap.with_name("medium-week1.ntnxdiagrams.json")
    assert path.exists() and ST.path_for(snap) == path
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["schema"] == ST.SCHEMA
    entry = ST.load(snap)["conceptual"]
    assert entry["ops"] == E.clean_ops(_ops(env))
    assert entry["base_hash"] == E.payload_hash("conceptual", ST.generated(env, "conceptual"))
    assert result["edited"] and result["stale"] is None and result["problems"] == []
    listing = ST.list_result(snap, env)
    assert listing[0]["edited"] and listing[0]["edits"] == 5 and not listing[0]["stale"]
    # Saving no edits, or resetting, removes the entry and the file.
    ST.reset_result(snap, env, "conceptual")
    assert not path.exists() and ST.load(snap) == {}


def test_unreadable_file_reads_as_no_edits(snap: Path, env: Environment) -> None:
    ST.path_for(snap).write_text("{not json", encoding="utf-8")
    assert ST.load(snap) == {} and ST.edited_models(snap, env) == {}
    ST.path_for(snap).write_text(json.dumps({"schema": ST.SCHEMA, "diagrams": {
        "conceptual": {"ops": [{"op": "boom"}], "base_model": {}}}}), encoding="utf-8")
    assert ST.load(snap) == {}


def test_file_follows_rename_and_delete(snap: Path, env: Environment) -> None:
    ST.save_result(snap, env, "conceptual", _ops(env))
    moved = snapstore.rename(snap, "renamed")
    assert ST.load(moved) and not ST.path_for(snap).exists()
    snapstore.delete(moved)
    assert not ST.path_for(moved).exists()


def test_stale_detection(snap: Path, env: Environment) -> None:
    ops = _ops(env)
    old = ST.generated(env, "conceptual")
    changed_id = ops[0]["id"]
    now = 0
    for card in old["sites"][0]["cards"]:
        if card["id"] == changed_id:
            now = card["nodes"]
            card["nodes"] = now - 1  # the data the edit was made on
    ST.save(snap, "conceptual", E.clean_ops(ops), old)
    got = ST.get_result(snap, env, "conceptual")
    assert got["stale"] is not None
    assert got["stale"]["changes"] == [{"id": changed_id, "kind": "changed",
                                        "label": got["stale"]["changes"][0]["label"],
                                        "fields": ["nodes"],
                                        "values": {"nodes": [now - 1, now]}}]
    assert got["stale"]["affected_ops"] == [0]
    assert ST.list_result(snap, env)[0]["stale"]
    # Keep my edits = save again: the base moves to today's data.
    assert ST.save_result(snap, env, "conceptual", got["ops"])["stale"] is None


def test_carry_forward(tmp_path: Path, env: Environment) -> None:
    store = SnapshotStore(tmp_path)
    old = store.save(env.model_copy(deep=True), "week1")
    ops = _ops(env)
    ST.save_result(old, env, "conceptual", ops)
    newer = env.model_copy(deep=True)
    gone = ops[0]["id"].removeprefix("cluster:")
    newer.clusters = [c for c in newer.clusters if c.uuid != gone]
    new = store.save(newer, "week2")
    report = ST.carry_report(old, newer)
    assert report[0]["edits"] == 5 and report[0]["applies"] == 4
    assert report[0]["problems"][0]["index"] == 0
    collected = next(o["label"] for o in _outline(env) if o["id"] == ops[0]["id"])
    assert report[0]["problems"][0]["label"] == collected  # the name the data has
    ST.carry_forward(old, {"name": "week1", "collected_at": None}, new, newer)
    entry = ST.load(new)["conceptual"]
    assert len(entry["ops"]) == 4 and entry["carried_from"]["name"] == "week1"
    assert entry["carried_from"]["dropped"][0]["index"] == 0
    got = ST.get_result(new, newer, "conceptual")
    assert got["problems"] == []
    assert got["stale"] is not None  # the cluster that left is reported
    assert any(c["id"] == ops[0]["id"] and c["kind"] == "removed"
               for c in got["stale"]["changes"])


@pytest.mark.parametrize("password", [None, "s3cret-pass"])
def test_snapshot_files_carry_the_edits(tmp_path: Path, env: Environment,
                                        password: str | None) -> None:
    from ntnxvision_core.store import transfer

    src_store = SnapshotStore(tmp_path / "a")
    snap = src_store.save(env.model_copy(deep=True), "week1")
    ST.save_result(snap, env, "conceptual", _ops(env))
    item = transfer.ExportItem(env=src_store.load(snap), meta=snapstore.read_meta(snap))
    dest = tmp_path / "week1.ntnxsnap"
    transfer.export_file([item], dest, password=password)
    dst_store = SnapshotStore(tmp_path / "b")
    result = transfer.import_file(dest, dst_store, password=password)
    imported = Path(result["imported"][0]["path"])
    entry = ST.load(imported)["conceptual"]
    assert entry["ops"] == ST.load(snap)["conceptual"]["ops"]
    assert entry["base_hash"] == ST.load(snap)["conceptual"]["base_hash"]
    got = ST.get_result(imported, env, "conceptual")
    assert got["edited"] and got["stale"] is None and got["problems"] == []
    # A file without edits imports without a diagrams file.
    plain = src_store.save(env.model_copy(deep=True), "week2")
    item = transfer.ExportItem(env=src_store.load(plain), meta=snapstore.read_meta(plain))
    transfer.export_file([item], tmp_path / "week2.ntnxsnap", password=password)
    res = transfer.import_file(tmp_path / "week2.ntnxsnap", dst_store, password=password)
    assert not ST.path_for(Path(res["imported"][0]["path"])).exists()


def test_restore_drops_bad_entries(tmp_path: Path, env: Environment) -> None:
    snap = SnapshotStore(tmp_path).save(env.model_copy(deep=True), "x")
    gen = ST.generated(env, "conceptual")
    doc = {"schema": ST.SCHEMA, "diagrams": {
        "conceptual": {"ops": E.clean_ops(_ops(env)), "base_model": gen,
                       "base_hash": "forged", "saved_at": "x" * 500},
        "evil": {"ops": [{"op": "rm -rf"}], "base_model": {}},
        "nomodel": {"ops": [{"op": "delete", "id": "a"}], "base_model": "no"},
    }}
    assert ST.restore(snap, doc) == 1
    entry = ST.load(snap)["conceptual"]
    assert entry["base_hash"] == E.payload_hash("conceptual", gen)  # recomputed
    assert entry["saved_at"] is None
    assert ST.restore(snap, {"schema": "other"}) == 0


# ─── exports ───────────────────────────────────────────────────────────


def _report(env: Environment, models: dict[str, Any] | None) -> Any:
    return build_report(env, ExportOptions(diagram_models=models), "Report")


def _blocks(report: Any) -> list[Any]:
    out = []
    for ch in report.chapters:
        out.extend(ch.blocks)
        for sec in ch.sections:
            out.extend(sec.blocks)
    return out


def test_exports_draw_edits_in_figures_only(snap: Path, env: Environment) -> None:
    ops = _ops(env)
    ST.save_result(snap, env, "conceptual", ops)
    models = ST.edited_models(snap, env)
    assert set(models) == {"conceptual"}
    blocks = _blocks(_report(env, models))
    figs = [b for b in blocks if isinstance(b, SceneFigure) and b.scene.type == "conceptual"]
    text = [t for f in figs for t in _text(f.scene)]
    assert "Backup appliance" in text and "SQL tier" in text and "USER-ADDED" in text
    cells = [str(c) for b in blocks if isinstance(b, Table) for row in b.rows for c in row]
    assert "Backup appliance" not in cells and "SQL tier" not in cells
    deleted = next(c for c in ST.generated(env, "conceptual")["sites"][0]["cards"]
                   if c["id"] == ops[4]["id"])
    assert deleted["name"] in cells  # removed from the figure, still in the tables
    plain = [b for b in _blocks(_report(env, None))
             if isinstance(b, SceneFigure) and b.scene.type == "conceptual"]
    assert "Backup appliance" not in [t for f in plain for t in _text(f.scene)]


def test_sidecar_operations_and_word_export(demo: Any, data_dir: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from docx import Document

    import ntnxvision_core.exporters.pdf as pdf_mod
    import ntnxvision_core.exporters.word as word_mod

    drawn: dict[str, list[S.Scene]] = {"pdf": [], "word": []}
    real_draw, real_png = pdf_mod.draw_scene, word_mod.render_png

    def spy_draw(pdf: Any, scene: S.Scene, *a: Any, **k: Any) -> Any:
        drawn["pdf"].append(scene)
        return real_draw(pdf, scene, *a, **k)

    def spy_png(scene: S.Scene, *a: Any, **k: Any) -> Any:
        drawn["word"].append(scene)
        return real_png(scene, *a, **k)

    monkeypatch.setattr(pdf_mod, "draw_scene", spy_draw)
    monkeypatch.setattr(word_mod, "render_png", spy_png)

    snap = sc._m_inventory(None, {
        "host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
        "insecure": False, "name": "pc01-week1", "formats": []})["snapshot_path"]
    listed = sc._m_list_diagrams(None, {"snapshot": snap})
    assert [d["type"] for d in listed["diagrams"]] == ["conceptual"]
    assert listed["carry"] is None
    got = sc._m_get_diagram(None, {"snapshot": snap, "type": "conceptual"})
    assert not got["edited"] and got["palette"] and got["scenes"]
    site = next(o for o in got["outline"] if o["collection"] == "sites")
    ops = [{"op": "add", "parent": site["id"], "field": "cards", "index": 0,
            "element": {"id": "user:e2e", "palette": "storage", "name": "Backup appliance"}}]
    laid = sc._m_layout_diagram(None, {"snapshot": snap, "type": "conceptual", "ops": ops})
    assert any(n["id"] == "user:e2e" for n in laid["scenes"][0]["nodes"])
    saved = sc._m_save_diagram(None, {"snapshot": snap, "type": "conceptual", "ops": ops})
    assert saved["edited"]
    with pytest.raises(Exception, match="Unknown diagram"):
        sc._m_get_diagram(None, {"snapshot": snap, "type": "nope"})
    with pytest.raises(Exception, match="not a known operation"):
        sc._m_save_diagram(None, {"snapshot": snap, "type": "conceptual",
                                  "ops": [{"op": "drop table"}]})
    out = sc._m_export(None, {"snapshot": snap, "formats": ["word", "pdf"],
                              "export_dir": str(data_dir / "exports")})
    assert set(out["paths"]) == {"word", "pdf"}, out["errors"]
    doc = Document(out["paths"]["word"])
    cells = [c.text for t in doc.tables for r in t.rows for c in r.cells]
    assert "Backup appliance" not in cells
    for fmt in ("word", "pdf"):
        text = [t for scene in drawn[fmt] if scene.type == "conceptual"
                for t in _text(scene)]
        assert "Backup appliance" in text and "USER-ADDED" in text, fmt
    # Carry forward: a second collection of the same PC offers the edits.
    sc._advance_demo("pc01.demo.local")
    snap2 = sc._m_inventory(None, {
        "host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
        "insecure": False, "name": "pc01-week2", "formats": []})["snapshot_path"]
    offer = sc._m_list_diagrams(None, {"snapshot": snap2})["carry"]
    assert offer is not None and offer["snapshot"]["name"] == "pc01-week1"
    assert offer["diagrams"][0]["applies"] == 1
    sc._m_carry_diagrams(None, {"from": snap, "to": snap2})
    after = sc._m_get_diagram(None, {"snapshot": snap2, "type": "conceptual"})
    assert after["edited"] and after["carried_from"]["name"] == "pc01-week1"
    assert sc._m_list_diagrams(None, {"snapshot": snap2})["carry"] is None
    reset = sc._m_reset_diagram(None, {"snapshot": snap2, "type": "conceptual"})
    assert not reset["edited"]
