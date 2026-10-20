"""Saved diagram edits, one ``<name>.ntnxdiagrams.json`` next to each snapshot.

The file holds, per diagram type, the author's operations (`edits.py`) and the generated
model they were made against (``base_model``, ``base_hash``)::

    {"schema": "ntnxvision.diagrams/1", "generator": "0.5.0",
     "diagrams": {"conceptual": {"ops": [...], "base_hash": "...", "base_model": {...},
                                 "saved_at": "2026-10-18T09:12:00Z",
                                 "carried_from": null}}}

Unedited diagrams are not stored: they are always generated from the snapshot. Every
consumer replays the operations on the model generated now, so an edit follows the data
(a renamed cluster keeps its new name when its node count changes). When the generated
model differs from ``base_model`` the edit is **stale**: the editor says what changed and
which operations touch it, and the author keeps the edits (saving again moves the base)
or resets.

Writes are atomic (temp file + rename) and serialised by one lock. A missing, unreadable
or foreign file reads as "no edits": it can never stop a snapshot from loading or
exporting.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ntnxvision_core import __version__
from ntnxvision_core.paths import write_private_text
from ntnxvision_core.store.snapshot import diagrams_path

from . import TYPES, DiagramType
from . import edits as E
from .model import envelope
from .scene import Scene
from .scene import to_doc as scene_doc

SCHEMA = "ntnxvision.diagrams/1"

_lock = threading.Lock()


def path_for(snapshot: Path) -> Path:
    """``<name>.ntnxdiagrams.json`` beside ``<name>.ntnxsnap.json``."""
    return diagrams_path(snapshot)


# ─── file ──────────────────────────────────────────────────────────────


def _read(snapshot: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path_for(snapshot).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return {}
    return doc


def markers(snapshot: Path) -> dict[str, Any]:
    """What happened to the snapshot's edits besides the edits themselves:
    ``reset`` (type ids the author reset to generated) and ``carry_failed_from`` (the
    snapshot whose edits did not carry to this one: {snapshot_id, name}). The carry
    search reads them: a reset is final, a failed carry points further back."""
    raw = _read(snapshot).get("markers")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    resets = raw.get("reset")
    if isinstance(resets, list):
        out["reset"] = sorted({str(t) for t in resets if isinstance(t, str)})[:MAX_ENTRIES]
    failed = raw.get("carry_failed_from")
    if isinstance(failed, dict):
        out["carry_failed_from"] = {k: str(failed[k]) for k in ("snapshot_id", "name")
                                    if isinstance(failed.get(k), str)}
    return out


def _set_markers(snapshot: Path, update: dict[str, Any]) -> None:
    with _lock:
        current = markers(snapshot)
        current.update(update)
        _write(snapshot, load(snapshot), {k: v for k, v in current.items() if v})


def mark_carry_failed(snapshot: Path, source: dict[str, Any]) -> None:
    """Record that `source`'s edits did not carry to `snapshot`."""
    _set_markers(snapshot, {"carry_failed_from": {
        k: str(source[k]) for k in ("snapshot_id", "name") if source.get(k)}})


def load(snapshot: Path) -> dict[str, dict[str, Any]]:
    """The stored entries by type id ({} when there is no usable file). Entries whose
    operations do not pass `clean_ops` are left out."""
    doc = _read(snapshot)
    out: dict[str, dict[str, Any]] = {}
    for type_id, entry in sorted((doc.get("diagrams") or {}).items()):
        if not isinstance(entry, dict) or not isinstance(entry.get("base_model"), dict):
            continue
        try:
            ops = E.clean_ops(entry.get("ops"))
        except E.EditError:
            continue
        out[str(type_id)] = {**entry, "ops": ops}
    return out


def _write(snapshot: Path, entries: dict[str, dict[str, Any]],
           marks: dict[str, Any] | None = None) -> None:
    """Write the file (`marks`: None keeps the markers it has)."""
    target = path_for(snapshot)
    marks = markers(snapshot) if marks is None else marks
    if not entries and not marks:
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
        return
    doc: dict[str, Any] = {"schema": SCHEMA, "generator": __version__,
                           "diagrams": dict(sorted(entries.items()))}
    if marks:
        doc["markers"] = marks
    tmp = target.with_name(target.name + ".tmp")
    write_private_text(tmp, json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=1))
    os.replace(tmp, target)


def save(snapshot: Path, type_id: str, ops: list[dict[str, Any]],
         base_model: dict[str, Any], *, carried_from: dict[str, Any] | None = None,
         now: str | None = None) -> dict[str, Any] | None:
    """Store `ops` for `type_id` against `base_model` (no ops: the entry is removed).
    Returns the stored entry."""
    with _lock:
        entries = load(snapshot)
        if not ops:
            entries.pop(type_id, None)
            _write(snapshot, entries)
            return None
        entry = {"ops": ops, "base_model": base_model,
                 "base_hash": E.payload_hash(type_id, base_model),
                 "saved_at": now or _now(), "carried_from": carried_from}
        entries[type_id] = entry
        _write(snapshot, entries)
        return entry


#: Most diagram entries one snapshot file may carry (imports are untrusted).
MAX_ENTRIES = 64
#: Largest serialised base model an imported entry may carry (a very large estate's
#: conceptual model is a few hundred KB).
MAX_BASE_BYTES = 2 * 1024 * 1024


def portable(snapshot: Path) -> dict[str, Any] | None:
    """The saved edits as they travel inside a ``.ntnxsnap`` file (None: no edits)."""
    entries = load(snapshot)
    if not entries:
        return None
    return {"schema": SCHEMA, "diagrams": {
        t: {k: e.get(k) for k in ("ops", "base_model", "base_hash", "saved_at")}
        for t, e in entries.items()}}


def restore(snapshot: Path, doc: Any) -> int:
    """Write edits that came inside a ``.ntnxsnap`` file next to the imported snapshot.
    Every entry is checked like a local file (schema, edit shapes and bounds, a model
    object); anything else is dropped. Returns the number of diagrams restored."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return 0
    raw = doc.get("diagrams")
    if not isinstance(raw, dict):
        return 0
    entries: dict[str, dict[str, Any]] = {}
    for type_id, entry in sorted(raw.items())[:MAX_ENTRIES]:
        if not isinstance(type_id, str) or len(type_id) > 64 or \
                not isinstance(entry, dict) or not isinstance(entry.get("base_model"), dict):
            continue
        if len(json.dumps(entry["base_model"], ensure_ascii=False)) > MAX_BASE_BYTES:
            continue
        try:
            ops = E.clean_ops(entry.get("ops"))
        except E.EditError:
            continue
        if not ops:
            continue
        saved_at = entry.get("saved_at")
        entries[type_id] = {
            "ops": ops, "base_model": entry["base_model"],
            "base_hash": E.payload_hash(type_id, entry["base_model"]),
            "saved_at": saved_at if isinstance(saved_at, str) and len(saved_at) < 40
            else None,
            "carried_from": None}
    with _lock:
        _write(snapshot, entries)
    return len(entries)


def reset(snapshot: Path, type_id: str) -> None:
    """Drop the edits of `type_id` and mark it reset: later collections do not bring
    older edits back."""
    save(snapshot, type_id, [], {})
    resets = set(markers(snapshot).get("reset", []))
    _set_markers(snapshot, {"reset": sorted(resets | {type_id})})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── models ────────────────────────────────────────────────────────────


def editable() -> list[DiagramType]:
    """The types the Diagrams page edits: every registered type except those drawn
    inside another's section (the estate map is built from the conceptual overview)."""
    return [t for t in TYPES.values() if not t.embedded]


def applicable(env: Any) -> list[DiagramType]:
    """The editable types this snapshot can draw, in registry order."""
    return [t for t in editable() if t.applies(env)]


def dtype(type_id: str) -> DiagramType:
    t = TYPES.get(type_id)
    if t is None or t.embedded:
        raise E.EditError(f"Unknown diagram: {type_id}")
    return t


def generated(env: Any, type_id: str) -> dict[str, Any]:
    """The generated model payload of `type_id` for `env`."""
    t = dtype(type_id)
    return t.to_doc(t.build_model(env))


def edited_models(snapshot: Path | None, env: Any) -> dict[str, Any]:
    """type id -> the edited model, for every saved diagram that applies to `env`
    (what exports draw instead of the generated model). Never raises."""
    if snapshot is None:
        return {}
    out: dict[str, Any] = {}
    for type_id, entry in load(snapshot).items():
        t = TYPES.get(type_id)
        if t is None or t.embedded:
            continue
        try:
            if not t.applies(env):
                continue
            applied = E.apply_ops(generated(env, type_id), entry["ops"], t.edit_spec)
            out[type_id] = t.from_doc(applied.payload)
        except Exception:  # noqa: BLE001 — a bad edit file must never break an export
            continue
    return out


def carried_origins(snapshot: Path | None) -> dict[str, str]:
    """type id -> the snapshot name its saved edits were carried from automatically."""
    if snapshot is None:
        return {}
    out = {}
    for type_id, entry in load(snapshot).items():
        src = entry.get("carried_from") or {}
        if src.get("auto") and src.get("name"):
            out[type_id] = str(src["name"])
    return out


# ─── the editor's view of one diagram ──────────────────────────────────


@dataclass
class View:
    type_id: str
    payload: dict[str, Any]
    generated: dict[str, Any]
    scenes: list[Scene]
    problems: list[E.Problem]


def build_view(env: Any, type_id: str, ops: list[dict[str, Any]],
               gen: dict[str, Any] | None = None) -> View:
    t = dtype(type_id)
    gen = gen if gen is not None else generated(env, type_id)
    applied = E.apply_ops(gen, ops, t.edit_spec)
    scenes = t.layout(t.from_doc(applied.payload), t.page)
    problems = sorted([*applied.problems, *_undrawn_edge_ops(ops, scenes, applied.problems)],
                      key=lambda p: p.index)
    return View(type_id, applied.payload, gen, scenes, problems)


def _undrawn_edge_ops(ops: list[dict[str, Any]], scenes: list[Scene],
                      known: list[E.Problem]) -> list[E.Problem]:
    """Edits of a generated connector that no part of the figure draws (the data no
    longer has it): problems, like an edit whose element is gone."""
    seen = {e.id for sc in scenes for e in sc.edges} | \
        {i for sc in scenes for i in (sc.meta.get("connectors") or {})}
    bad = {p.index for p in known}
    return [E.Problem(i, op, "the connector is not in this figure")
            for i, op in enumerate(ops)
            if op["op"] == "edge" and i not in bad and not op["id"].startswith(E.USER_PREFIX)
            and op["id"] not in seen]


def connector_outline(view: View, elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the inspector shows for each connector of the figure: the generated ones the
    author may change and the user-added ones, with their ends (element ids), label,
    sides, lane offset, arrowheads and the parts that draw them."""
    from .connectors import KIND_NAMES, editable

    labels = {o["id"]: str(o["label"] or o["id"]) for o in elements}
    out: dict[str, dict[str, Any]] = {}
    for i, sc in enumerate(view.scenes):
        nodes = {n.id: n for n in sc.nodes}

        def owner(ident: str, nodes: dict[str, Any] = nodes) -> str | None:
            seen: set[str] = set()
            cur: str | None = ident
            while cur is not None and cur not in seen:
                if cur in labels:
                    return cur
                seen.add(cur)
                n = nodes.get(cur)
                cur = n.parent if n is not None else None
            return None

        info = sc.meta.get("connectors") or {}
        for e in sc.edges:
            if not editable(e, nodes):
                continue
            a, b = owner(e.src), owner(e.dst)
            if a is None or b is None or a == b:
                continue
            if e.id in out:
                out[e.id]["parts"].append(i)
                continue
            user = e.id.startswith(E.USER_PREFIX)
            note = info.get(e.id, {})
            ca = owner(str(note.get("collected_src") or e.src)) or a
            cb = owner(str(note.get("collected_dst") or e.dst)) or b
            label = " ".join(t.text for lab in e.labels for t in lab.lines)
            kind = KIND_NAMES.get(str(e.data.get("kind")), "Connector")
            arrow = ("both" if e.arrow_start and e.arrow_end else "end" if e.arrow_end
                     else "start" if e.arrow_start else "none")
            out[e.id] = {
                "id": e.id, "origin": "user" if user else "collected", "kind": kind,
                "label": label or None,
                "collected_label": None if user else note.get("collected_label", label) or None,
                "a": a, "b": b, "a_label": labels.get(a, a), "b_label": labels.get(b, b),
                "ends": [ca, cb],
                "a_side": note.get("a_side", "auto"), "b_side": note.get("b_side", "auto"),
                "bend": note.get("bend", 0), "arrow": arrow, "parts": [i],
                "edited": user or bool(note), "hidden": False,
                "can_delete": True,
                "delete_op": "delete" if user or e.id in labels else "hide",
                "source": ("Added on the Diagrams page. Exports draw it dashed amber; it "
                           "never appears in the tables.") if user else
                f"{kind} from the snapshot, {labels.get(ca, ca)} to {labels.get(cb, cb)}.",
            }
        for ident, note in info.items():
            if note.get("hidden") and ident not in out:
                out[ident] = {"id": ident, "origin": "collected", "kind": "Connector",
                              "label": note.get("collected_label") or None,
                              "collected_label": note.get("collected_label") or None,
                              "a": None, "b": None, "a_label": None, "b_label": None,
                              "ends": [], "a_side": "auto", "b_side": "auto", "bend": 0,
                              "arrow": "none", "parts": [], "edited": True, "hidden": True,
                              "can_delete": False, "delete_op": "hide",
                              "source": "Removed from this figure on the Diagrams page."}
    for c in view.payload.get(E.CONNECTORS) or []:
        if c["id"] in out:
            continue
        out[c["id"]] = {
            "id": c["id"], "origin": "user", "kind": "Connector",
            "label": c.get("label") or None, "collected_label": None,
            "a": c.get("a"), "b": c.get("b"), "a_label": labels.get(str(c.get("a"))),
            "b_label": labels.get(str(c.get("b"))), "ends": [c.get("a"), c.get("b")],
            "a_side": c.get("a_side", "auto"), "b_side": c.get("b_side", "auto"),
            "bend": c.get("bend", 0), "arrow": c.get("arrow", "end"), "parts": [],
            "edited": True, "hidden": False, "can_delete": True, "delete_op": "delete",
            "source": "Not drawn: its ends are not in the same part of this figure. "
                      "Exports leave it out.",
        }
    return [out[k] for k in sorted(out, key=lambda k: (out[k]["parts"][:1] or [10**6], k))]


def _scene_docs(scenes: list[Scene]) -> list[dict[str, Any]]:
    return [scene_doc(sc) for sc in scenes]


def _problem_docs(view: View) -> list[dict[str, Any]]:
    return [p.to_doc() for p in view.problems]


def layout_result(env: Any, type_id: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Re-layout with `ops` (unsaved edits): scenes, outline and the operations that did
    not apply."""
    t = dtype(type_id)
    view = build_view(env, type_id, ops)
    elements = E.outline(view.payload, view.generated, t.edit_spec)
    return {
        "type": type_id,
        "model": envelope(type_id, view.payload),
        "scenes": _scene_docs(view.scenes),
        "outline": elements,
        "connectors": connector_outline(view, elements),
        "problems": _problem_docs(view),
    }


def stale_info(type_id: str, entry: dict[str, Any], gen: dict[str, Any],
               spec: E.EditSpec) -> dict[str, Any] | None:
    """What changed in the generated model since the edits were made, and which
    operations touch it; None when nothing changed."""
    if entry.get("base_hash") == E.payload_hash(type_id, gen):
        return None
    changes = E.diff(entry["base_model"], gen, spec)
    if not changes:
        return None
    changed = {c.id for c in changes}
    affected = [i for i, op in enumerate(entry["ops"]) if E.touched(op) & changed]
    return {"changes": [c.to_doc() for c in changes], "affected_ops": affected}


def get_result(snapshot: Path, env: Any, type_id: str) -> dict[str, Any]:
    t = dtype(type_id)
    entry = load(snapshot).get(type_id)
    ops = entry["ops"] if entry else []
    gen = generated(env, type_id)
    view = build_view(env, type_id, ops, gen)
    stale = stale_info(t.id, entry, gen, t.edit_spec) if entry else None
    elements = E.outline(view.payload, gen, t.edit_spec)
    return {
        "type": type_id,
        "title": t.title,
        "model": envelope(type_id, view.payload),
        "generated_hash": E.payload_hash(type_id, gen),
        "base_hash": entry.get("base_hash") if entry else None,
        "edited": entry is not None,
        "ops": ops,
        "saved_at": entry.get("saved_at") if entry else None,
        "carried_from": entry.get("carried_from") if entry else None,
        "stale": stale,
        "scenes": _scene_docs(view.scenes),
        "outline": elements,
        "connectors": connector_outline(view, elements),
        "problems": _problem_docs(view),
        "palette": [{"id": p.id, "label": p.label, "collection": p.collection,
                     "parents": list(p.parents), "hint": p.hint,
                     "template": {k: v for k, v in p.template.items() if k != "cards"}}
                    for p in t.edit_spec.palette if p.listed],
    }


def list_result(snapshot: Path, env: Any) -> list[dict[str, Any]]:
    """One entry per diagram this snapshot can draw: its parts and its edit state."""
    entries = load(snapshot)
    out = []
    for t in applicable(env):
        entry = entries.get(t.id)
        ops = entry["ops"] if entry else []
        gen = generated(env, t.id)
        view = build_view(env, t.id, ops, gen)
        stale = stale_info(t.id, entry, gen, t.edit_spec) if entry else None
        out.append({
            "type": t.id,
            "title": t.title,
            "parts": [{"index": i, "id": sc.id, "caption": sc.caption,
                       "part": list(sc.part)} for i, sc in enumerate(view.scenes)],
            "edited": entry is not None,
            "edits": len(ops),
            "saved_at": entry.get("saved_at") if entry else None,
            "stale": stale is not None,
        })
    return out


def save_result(snapshot: Path, env: Any, type_id: str, raw_ops: Any) -> dict[str, Any]:
    t = dtype(type_id)
    if not t.applies(env):
        raise E.EditError(f"This snapshot has no {t.title.lower()}")
    ops = E.clean_ops(raw_ops)
    if not ops and type_id in load(snapshot):
        # Undo everything, then Save: the same as Reset to generated, and as final.
        reset(snapshot, type_id)
        return get_result(snapshot, env, type_id)
    gen = generated(env, type_id)
    save(snapshot, type_id, ops, gen)
    return get_result(snapshot, env, type_id)


def reset_result(snapshot: Path, env: Any, type_id: str) -> dict[str, Any]:
    dtype(type_id)
    reset(snapshot, type_id)
    return get_result(snapshot, env, type_id)


# ─── carrying edits to a newer snapshot ────────────────────────────────


def carry_report(src: Path, dst_env: Any, types: list[str] | None = None
                 ) -> list[dict[str, Any]]:
    """Per edited diagram of `src`: how many of its operations apply to `dst_env`'s
    generated model, and why the others do not."""
    out = []
    for type_id, entry in load(src).items():
        t = TYPES.get(type_id)
        if t is None or t.embedded or (types is not None and type_id not in types) or \
                not t.applies(dst_env):
            continue
        gen = generated(dst_env, type_id)
        view = build_view(dst_env, type_id, entry["ops"], gen)
        names = _labels(entry["base_model"], t.edit_spec)
        out.append({
            "type": type_id, "title": t.title, "edits": len(entry["ops"]),
            "applies": len(entry["ops"]) - len(view.problems),
            "problems": [{**p.to_doc(), "label": names.get(str(p.op.get("id")), None)}
                         for p in view.problems],
        })
    return out


def _labels(payload: dict[str, Any], spec: E.EditSpec) -> dict[str, str]:
    return {o["id"]: str(o["label"] or o["id"]) for o in E.outline(payload, payload, spec)}


def carry_forward(src: Path, src_info: dict[str, Any], dst: Path, dst_env: Any,
                  types: list[str] | None = None) -> list[dict[str, Any]]:
    """Replay `src`'s edits on `dst` and save them there. Operations that cannot apply
    are reported and not stored; the target opens as stale (its base is the source's),
    so the author sees what changed under the edits."""
    report = carry_report(src, dst_env, types)
    taken = sorted(set(load(dst)) & {item["type"] for item in report})
    if taken:
        titles = ", ".join(TYPES[t].title if t in TYPES else t for t in taken)
        raise E.EditError(f"This snapshot already has saved edits ({titles}). Reset them "
                          "before carrying other edits forward.")
    entries = load(src)
    for item in report:
        entry = entries[item["type"]]
        failed = {p["index"] for p in item["problems"]}
        ops = [op for i, op in enumerate(entry["ops"]) if i not in failed]
        if not ops:
            continue
        save(dst, item["type"], ops, entry["base_model"],
             carried_from={**src_info, "dropped": item["problems"]})
    return report
