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
    # Added elements: text limits apply to their name and note, and the palette and
    # field are short text.
    add = {"op": "add", "parent": "site:x", "field": "cards", "index": 0}
    for bad in ({"id": "user:1", "palette": "note", "name": "n" * 201},
                {"id": "user:1", "palette": "note", "note": "n" * 201},
                {"id": "user:1", "palette": "note", "name": ["not", "text"]},
                {"id": "user:1", "palette": "p" * 65},
                {"id": "user:1", "palette": 7}):
        with pytest.raises(E.EditError):
            E.clean_ops([{**add, "element": bad}])
    for field_name in ("f" * 65, "", 3, None):
        with pytest.raises(E.EditError):
            E.clean_ops([{**add, "field": field_name,
                          "element": {"id": "user:1", "palette": "note"}}])
    [ok] = E.clean_ops([{**add, "element": {"id": "user:1", "palette": "note",
                                            "name": f" A  {EM_DASH} B ", "note": None}}])
    assert ok["element"] == {"id": "user:1", "palette": "note", "name": "A , B", "note": None}
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
    # A base model over 2 MB serialised is refused.
    huge = {**gen, "padding": "x" * (ST.MAX_BASE_BYTES + 1)}
    assert ST.restore(snap, {"schema": ST.SCHEMA, "diagrams": {"conceptual": {
        "ops": E.clean_ops(_ops(env)), "base_model": huge}}}) == 0


def test_stale_ignores_fields_the_base_model_lacks(snap: Path, env: Environment) -> None:
    old = ST.generated(env, "conceptual")
    for s in old["sites"]:  # saved by an app version without these fields
        s.pop("cluster_count", None)
        s.pop("node_count", None)
    ST.save(snap, "conceptual", E.clean_ops(_ops(env)), old)
    assert ST.get_result(snap, env, "conceptual")["stale"] is None


def test_carry_never_overwrites_saved_edits(tmp_path: Path, env: Environment) -> None:
    store = SnapshotStore(tmp_path)
    old = store.save(env.model_copy(deep=True), "week1")
    new = store.save(env.model_copy(deep=True), "week2")
    ST.save_result(old, env, "conceptual", _ops(env))
    ST.save_result(new, env, "conceptual", _ops(env)[:1])
    with pytest.raises(E.EditError, match="already has saved edits"):
        ST.carry_forward(old, {"name": "week1"}, new, env)
    assert len(ST.load(new)["conceptual"]["ops"]) == 1


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
    # Every edit applies, so the collection carried them already; reset to try by hand.
    assert ST.load(Path(snap2))["conceptual"]["carried_from"]["auto"]
    sc._m_reset_diagram(None, {"snapshot": snap2, "type": "conceptual"})
    offer = sc._m_list_diagrams(None, {"snapshot": snap2})["carry"]
    assert offer is not None and offer["snapshot"]["name"] == "pc01-week1"
    assert offer["diagrams"][0]["applies"] == 1
    sc._m_carry_diagrams(None, {"from": snap, "to": snap2})
    after = sc._m_get_diagram(None, {"snapshot": snap2, "type": "conceptual"})
    assert after["edited"] and after["carried_from"]["name"] == "pc01-week1"
    # Compare's baseline: the snapshot the edits came from, by its stable id.
    assert after["carried_from"]["snapshot_id"] == offer["snapshot"]["snapshot_id"]
    assert sc._m_list_diagrams(None, {"snapshot": snap2})["carry"] is None
    reset = sc._m_reset_diagram(None, {"snapshot": snap2, "type": "conceptual"})
    assert not reset["edited"]


# ─── edited figures in the reports: as collected, disclosed, paired AZs together ───


@pytest.fixture(scope="module")
def enterprise(_demo_state: Any) -> Environment:
    from ntnxvision_core.synthetic import demo
    from ntnxvision_core.synthetic.scenarios import collect_scenario

    demo.reset()
    out = collect_scenario("enterprise")
    demo.reset()
    return out


def _edited(env: Environment, ops: list[dict[str, Any]]) -> Any:
    t = TYPES["conceptual"]
    return t.from_doc(E.apply_ops(ST.generated(env, "conceptual"), E.clean_ops(ops),
                                  SPEC).payload)


def _figures(env: Environment, models: dict[str, Any] | None) -> list[SceneFigure]:
    return [b for b in _blocks(_report(env, models))
            if isinstance(b, SceneFigure) and b.scene.type == "conceptual"]


def test_edited_figure_keeps_collected_counts_and_discloses_the_edit(env: Environment
                                                                     ) -> None:
    ops = _ops(env)
    figs = _figures(env, {"conceptual": _edited(env, ops)})
    plain = _figures(env, None)
    gen = ST.generated(env, "conceptual")
    deleted = next(c for c in gen["sites"][0]["cards"] if c["id"] == ops[4]["id"])
    # Summary bullets as collected, plus one disclosure bullet.
    assert figs[0].summary[:-1] == plain[0].summary
    note = figs[0].summary[-1]
    assert note.startswith("Figure edited on the Diagrams page: ")
    assert f"1 cluster ({deleted['name']}, {deleted['nodes']} nodes) not drawn" in note
    assert "1 user-added item" in note
    assert ' renamed to \u201cSQL tier\u201d' in note and '"' not in note
    assert note.endswith("Counts are as collected.")
    # The caption keeps the collected numbers and says the figure is edited.
    assert figs[0].caption.startswith("Solution conceptual overview (edited): ")
    assert plain[0].caption.split(": ", 1)[1] == figs[0].caption.split(": ", 1)[1]
    # The AZ header keeps the collected counts and names what is not drawn.
    site = figs[0].scene.node(gen["sites"][0]["id"])
    flat = " ".join(site.text.split())
    assert "(1 not drawn)" in flat
    plain_site = plain[0].scene.node(gen["sites"][0]["id"])
    assert flat.replace(" (1 not drawn)", "") == " ".join(plain_site.text.split())
    assert all(check_scene(f.scene) == [] for f in figs)


def test_user_cards_never_split_paired_azs(enterprise: Environment) -> None:
    gen = ST.generated(enterprise, "conceptual")
    a, b = next((lk["a"], lk["b"]) for lk in gen["links"]
                if lk["a"].startswith("site:") and lk["b"].startswith("site:"))
    ops: list[dict[str, Any]] = [
        {"op": "add", "parent": a, "field": "cards", "index": 99,
         "element": {"id": f"user:{i}", "palette": "storage", "name": f"Array {i}"}}
        for i in range(4)]
    ops.append({"op": "add", "parent": None, "field": "sites", "index": 99,
                "element": {"id": "user:colo", "palette": "az", "name": "Colocation"}})
    figs = _figures(enterprise, {"conceptual": _edited(enterprise, ops)})
    part_of = {sid: i for i, f in enumerate(figs) for sid in f.scene.meta["sites"]}
    assert part_of[a] == part_of[b]
    for lk in gen["links"]:
        if lk["a"] in part_of and lk["b"] in part_of:
            assert part_of[lk["a"]] == part_of[lk["b"]], lk["id"]
    assert all(check_scene(f.scene) == [] for f in figs)
    assert not any("where they fit" in s for f in figs for s in f.summary)


def test_word_tables_never_orphan_their_header(env: Environment, tmp_path: Path) -> None:
    from docx import Document
    from docx.oxml.ns import qn

    from ntnxvision_core.exporters import export_environment
    from ntnxvision_core.exporters.word import SMALL_TABLE

    path = export_environment(env, "word", tmp_path, "medium",
                              {"diagram_models": {"conceptual": _edited(env, _ops(env))}})
    doc = Document(str(path))
    sec = doc.sections[0]
    checked = 0
    for table in doc.tables:
        rows = table.rows
        tr_pr = rows[0]._tr.trPr
        if tr_pr is None or tr_pr.find(qn("w:tblHeader")) is None:
            continue
        checked += 1
        assert tr_pr.find(qn("w:cantSplit")) is not None
        assert all(p.paragraph_format.keep_with_next for c in rows[0].cells
                   for p in c.paragraphs)
        if len(rows) - 1 <= SMALL_TABLE:
            for r in rows[1:-1]:
                assert all(p.paragraph_format.keep_with_next for c in r.cells
                           for p in c.paragraphs)
    assert checked
    # No table is wider than the text (two-column summaries included), and no run uses
    # w:noBreakHyphen (Word squeezed the following words in table cells).
    for s_ in doc.sections:
        text_w = (s_.page_width - s_.left_margin - s_.right_margin) / 635
        for table in doc.tables:
            w = table._tbl.tblPr.find(qn("w:tblW"))
            if w is not None and w.get(qn("w:type")) == "dxa":
                assert int(w.get(qn("w:w"))) <= text_w + 1 or s_ is not sec
    xml = doc.element.xml
    assert "w:noBreakHyphen" not in xml and "‑" not in xml
    # Every run keeps its spaces: a w:t that starts or ends with a space is preserved,
    # and each design-summary bullet reads in the document exactly as written.
    for t in doc.element.body.iter(qn("w:t")):
        if t.text and t.text != t.text.strip():
            assert t.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve", t.text
    texts = [p.text for p in doc.paragraphs] + [
        p.text for tb in doc.tables for r in tb.rows for c in r.cells for p in c.paragraphs]
    models = {"conceptual": _edited(env, _ops(env))}
    figs = _figures(env, models)
    for item in figs[0].summary:
        assert any(item in t for t in texts), item


def test_word_columns_hold_their_longest_word(enterprise: Environment, tmp_path: Path) -> None:
    """Every Word table's columns are at least as wide as their longest word measured
    with real font metrics (so "NYC-PROD-01" never breaks at its hyphen); when the
    minimums cannot all fit, the Cluster column still gets its own."""
    from docx import Document
    from docx.oxml.ns import qn

    from ntnxvision_core.exporters import export_environment
    from ntnxvision_core.exporters.word import CELL_PAD_PT, MAX_WORD_PT, _text_pt

    doc = Document(str(export_environment(enterprise, "word", tmp_path, "ent")))
    checked = 0
    for table in doc.tables:
        rows = table.rows
        tr_pr = rows[0]._tr.trPr
        if tr_pr is None or tr_pr.find(qn("w:tblHeader")) is None:
            continue  # figure and summary layout tables
        grid = [int(c.get(qn("w:w"))) / 20 for c in
                table._tbl.tblGrid.findall(qn("w:gridCol"))]
        need = []
        for ci in range(len(grid)):
            words = [(w, ri == 0) for ri, r in enumerate(rows)
                     for w in r.cells[ci].text.split()]
            longest = max((_text_pt(w, b) for w, b in words), default=0.0)
            need.append(min(longest, MAX_WORD_PT) + CELL_PAD_PT)
        head = rows[0].cells[0].text
        if sum(need) <= sum(grid) + 1:
            assert all(g >= n - 1 for g, n in zip(grid, need, strict=True)), (head, grid, need)
        elif head == "Cluster":
            assert grid[0] >= need[0] - 1, (grid, need)
        checked += 1
    assert checked > 10


def test_empty_user_zone_is_only_its_header(env: Environment) -> None:
    [scene] = _scenes(env, [{"op": "add", "parent": None, "field": "sites", "index": 99,
                             "element": {"id": "user:az", "palette": "az", "name": "Colo"}}])
    zone = scene.node("user:az")
    collected = scene.node(ST.generated(env, "conceptual")["sites"][0]["id"])
    assert zone.h < 40 < collected.h
    assert check_scene(scene) == []


def test_every_diagram_type_draws_the_figure_model(env: Environment,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Each registered type's report section lays out `figure_model()` (the saved
    edit), so a type added later cannot silently ignore the Diagrams page."""
    import dataclasses

    from ntnxvision_core.exporters import content as C

    marks: dict[str, Any] = {}
    seen: list[Any] = []

    def figure_model(self: Any, dtype: Any, generated: Any) -> Any:
        marks[dtype.id] = dtype.from_doc(dtype.to_doc(generated))
        return marks[dtype.id]

    monkeypatch.setattr(C._Builder, "figure_model", figure_model)
    for tid, t in list(TYPES.items()):
        def spy(model: Any, *a: Any, _real: Any = t.layout, **k: Any) -> Any:
            seen.append(model)
            return _real(model, *a, **k)
        monkeypatch.setitem(TYPES, tid, dataclasses.replace(t, layout=spy))
    build_report(env, ExportOptions(), "Report")
    for tid, t in TYPES.items():
        if t.applies(env):
            assert tid in marks, f"{tid}: the report never asked for figure_model()"
            assert any(m is marks[tid] for m in seen), f"{tid}: figure_model() not drawn"


def test_fresh_collection_replays_saved_edits(demo: Any, data_dir: Path) -> None:
    params = {"host": "pc01.demo.local", "username": "admin", "password": "demo",
              "port": 9440, "insecure": False, "export_dir": str(data_dir / "exports")}
    first = sc._m_inventory(None, {**params, "name": "week1", "formats": []})
    got = sc._m_get_diagram(None, {"snapshot": first["snapshot_path"], "type": "conceptual"})
    site = next(o for o in got["outline"] if o["collection"] == "sites")
    sc._m_save_diagram(None, {"snapshot": first["snapshot_path"], "type": "conceptual",
                              "ops": [{"op": "add", "parent": site["id"], "field": "cards",
                                       "index": 0, "element": {
                                           "id": "user:b", "palette": "storage",
                                           "name": "Backup appliance"}}]})
    sc._advance_demo("pc01.demo.local")
    second = sc._m_inventory(None, {**params, "name": "week2", "formats": ["pdf"]})
    entry = ST.load(Path(second["snapshot_path"]))["conceptual"]
    assert entry["carried_from"]["auto"] and entry["carried_from"]["name"] == "week1"
    assert not any("Saved diagram edits" in w for w in second["warnings"])
    # An edit that no longer applies: generated diagrams, and the run says why.
    sc._m_save_diagram(None, {"snapshot": second["snapshot_path"], "type": "conceptual",
                              "ops": [{"op": "delete", "id": "cluster:gone"}]})
    sc._advance_demo("pc01.demo.local")
    third = sc._m_inventory(None, {**params, "name": "week3", "formats": ["pdf"]})
    assert ST.load(Path(third["snapshot_path"])) == {}
    assert any(w.startswith("Saved diagram edits from week2 were not applied: 1 edit no "
                            "longer matches the collected data.")
               for w in third["warnings"])
    path = Path(third["snapshot_path"])
    notes = sc._diagram_notes(path, sc._cache.get(path))
    assert notes and notes[0].startswith("Saved diagram edits from week2")


def test_scheduled_run_reports_draw_carried_edits(demo: Any, data_dir: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """A schedule collects with formats [] and writes its reports in send_email: the
    edits are carried at collection, and the emailed reports draw them."""
    from ntnxvision_core import mailer

    params = {"host": "pc01.demo.local", "username": "admin", "password": "demo",
              "port": 9440, "insecure": False, "export_dir": str(data_dir / "exports")}
    first = sc._m_inventory(None, {**params, "name": "week1", "formats": []})
    got = sc._m_get_diagram(None, {"snapshot": first["snapshot_path"], "type": "conceptual"})
    site = next(o for o in got["outline"] if o["collection"] == "sites")
    sc._m_save_diagram(None, {"snapshot": first["snapshot_path"], "type": "conceptual",
                              "ops": [{"op": "add", "parent": site["id"], "field": "cards",
                                       "index": 0, "element": {
                                           "id": "user:b", "palette": "storage",
                                           "name": "Backup appliance"}}]})
    sc._advance_demo("pc01.demo.local")
    second = sc._m_inventory(None, {**params, "name": "week2", "formats": []})
    snap2 = Path(second["snapshot_path"])
    assert ST.load(snap2)["conceptual"]["carried_from"]["auto"]

    drawn: list[Any] = []
    real = sc.export_environment

    def spy(env: Any, fmt: str, *a: Any, **k: Any) -> Any:
        drawn.append(dict(k.get("options") or {}).get("diagram_models"))
        return real(env, fmt, *a, **k)

    sent: dict[str, str] = {}

    def send_files(cfg: Any, to: Any, cc: Any, subject: str, body: str, paths: Any,
                   zip_name: str = "") -> dict[str, Any]:
        sent["body"] = body
        return mailer.result(True, "ok", "Sent.")

    monkeypatch.setattr(sc, "export_environment", spy)
    monkeypatch.setattr(mailer, "send_files", send_files)
    r = sc._m_send_email(None, {
        "smtp": {"host": "127.0.0.1", "port": 2525, "security": "none", "username": "",
                 "password": "", "from_address": "ntnxvision@example.com",
                 "from_name": "NTNXVision", "allow_untrusted": False,
                 "max_attachment_mb": 20, "zip_attachments": False},
        "to": ["user@example.com"], "subject": "s", "body": "b",
        "snapshots": [str(snap2)], "formats": ["word"],
        "export_dir": str(data_dir / "exports")})
    assert r["ok"], r
    assert drawn and drawn[0] and "conceptual" in drawn[0]
    model = drawn[0]["conceptual"]
    assert any(c.id == "user:b" for s in model.sites for c in s.cards)
    assert "were carried forward" in sent["body"]


def test_failed_auto_carry_is_a_warning(demo: Any, data_dir: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    params = {"host": "pc01.demo.local", "username": "admin", "password": "demo",
              "port": 9440, "insecure": False, "export_dir": str(data_dir / "exports")}
    first = sc._m_inventory(None, {**params, "name": "week1", "formats": []})
    sc._m_save_diagram(None, {"snapshot": first["snapshot_path"], "type": "conceptual",
                              "ops": [{"op": "set", "id": next(
                                  o["id"] for o in sc._m_get_diagram(None, {
                                      "snapshot": first["snapshot_path"],
                                      "type": "conceptual"})["outline"]
                                  if o["collection"] == "sites"),
                                       "field": "sublabel", "value": "Main DC"}]})

    def boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(ST, "carry_forward", boom)
    sc._advance_demo("pc01.demo.local")
    second = sc._m_inventory(None, {**params, "name": "week2", "formats": []})
    warning = next(w for w in second["warnings"] if w.startswith("Saved diagram edits"))
    assert "could not be saved with this snapshot (disk full)" in warning
    # Kept with the snapshot, so a later email says it too.
    path = Path(second["snapshot_path"])
    sc._cache.invalidate(path)
    assert warning in sc._cache.get(path).warnings


_PC01 = {"host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
         "insecure": False}


def _collect(data_dir: Path, name: str) -> dict[str, Any]:
    return sc._m_inventory(None, {**_PC01, "name": name, "formats": [],
                                  "export_dir": str(data_dir / "exports")})


def _add_backup(snap: str, extra: list[dict[str, Any]] | None = None) -> None:
    got = sc._m_get_diagram(None, {"snapshot": snap, "type": "conceptual"})
    site = next(o for o in got["outline"] if o["collection"] == "sites")
    sc._m_save_diagram(None, {"snapshot": snap, "type": "conceptual", "ops": [
        {"op": "add", "parent": site["id"], "field": "cards", "index": 0,
         "element": {"id": "user:b", "palette": "storage", "name": "Backup appliance"}},
        *(extra or [])]})


def test_reset_edits_never_come_back(demo: Any, data_dir: Path) -> None:
    """week1 edited, week2 carries them, the author resets week2: week3 starts from the
    generated diagram (only the newest earlier snapshot counts, and it has no edits)."""
    first = _collect(data_dir, "week1")
    _add_backup(first["snapshot_path"])
    sc._advance_demo("pc01.demo.local")
    second = _collect(data_dir, "week2")
    s2 = Path(second["snapshot_path"])
    assert ST.load(s2)["conceptual"]["carried_from"]["auto"]
    assert second["diagram_carry"]["from"] == "week1"
    sc._m_reset_diagram(None, {"snapshot": str(s2), "type": "conceptual"})
    sc._advance_demo("pc01.demo.local")
    third = _collect(data_dir, "week3")
    s3 = Path(third["snapshot_path"])
    assert ST.load(s3) == {}
    assert "diagram_carry" not in third
    assert not any("diagram edits" in w for w in third["warnings"])
    # The Diagrams page's carry offer (C3) follows the same rule.
    assert sc._m_list_diagrams(None, {"snapshot": str(s3)})["carry"] is None


def test_not_applied_warning_is_not_repeated(demo: Any, data_dir: Path) -> None:
    first = _collect(data_dir, "week1")
    _add_backup(first["snapshot_path"], [{"op": "delete", "id": "cluster:gone"}])
    sc._advance_demo("pc01.demo.local")
    second = _collect(data_dir, "week2")
    assert sum("were not applied" in w for w in second["warnings"]) == 1
    sc._advance_demo("pc01.demo.local")
    third = _collect(data_dir, "week3")
    assert not any("were not applied" in w for w in third["warnings"])


def test_auto_carried_figure_says_where_its_edits_came_from(demo: Any, data_dir: Path,
                                                          monkeypatch: pytest.MonkeyPatch
                                                          ) -> None:
    first = _collect(data_dir, "week1")
    _add_backup(first["snapshot_path"])
    sc._advance_demo("pc01.demo.local")
    second = _collect(data_dir, "week2")
    seen: list[Any] = []
    real = sc.export_environment

    def spy(env: Any, fmt: str, *a: Any, **k: Any) -> Any:
        seen.append(dict(k.get("options") or {}))
        return real(env, fmt, *a, **k)

    monkeypatch.setattr(sc, "export_environment", spy)
    sc._m_export(None, {"snapshot": second["snapshot_path"], "formats": ["pdf"],
                        "export_dir": str(data_dir / "exports")})
    opts = seen[0]
    assert opts["diagram_origins"] == {"conceptual": "week1"}
    env = sc._cache.get(Path(second["snapshot_path"]))
    report = build_report(env, ExportOptions.coerce(opts), "Report")
    bullets = [s for b in _blocks(report) if isinstance(b, SceneFigure) for s in b.summary]
    assert any(s.startswith("Figure edited on the Diagrams page (edits carried from "
                            "week1): ") for s in bullets)
