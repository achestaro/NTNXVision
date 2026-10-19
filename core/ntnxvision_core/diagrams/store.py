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


def load(snapshot: Path) -> dict[str, dict[str, Any]]:
    """The stored entries by type id ({} when there is no usable file). Entries whose
    operations do not pass `clean_ops` are left out."""
    try:
        doc = json.loads(path_for(snapshot).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return {}
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


def _write(snapshot: Path, entries: dict[str, dict[str, Any]]) -> None:
    target = path_for(snapshot)
    if not entries:
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
        return
    doc = {"schema": SCHEMA, "generator": __version__,
           "diagrams": dict(sorted(entries.items()))}
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
        if not isinstance(type_id, str) or not isinstance(entry, dict) or \
                not isinstance(entry.get("base_model"), dict):
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
    save(snapshot, type_id, [], {})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── models ────────────────────────────────────────────────────────────


def applicable(env: Any) -> list[DiagramType]:
    """The registered types this snapshot can draw, in registry order."""
    return [t for t in TYPES.values() if t.applies(env)]


def dtype(type_id: str) -> DiagramType:
    if type_id not in TYPES:
        raise E.EditError(f"Unknown diagram: {type_id}")
    return TYPES[type_id]


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
        if t is None:
            continue
        try:
            if not t.applies(env):
                continue
            applied = E.apply_ops(generated(env, type_id), entry["ops"], t.edit_spec)
            out[type_id] = t.from_doc(applied.payload)
        except Exception:  # noqa: BLE001 — a bad edit file must never break an export
            continue
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
    return View(type_id, applied.payload, gen, scenes, applied.problems)


def _scene_docs(scenes: list[Scene]) -> list[dict[str, Any]]:
    return [scene_doc(sc) for sc in scenes]


def _problem_docs(view: View) -> list[dict[str, Any]]:
    return [p.to_doc() for p in view.problems]


def layout_result(env: Any, type_id: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Re-layout with `ops` (unsaved edits): scenes, outline and the operations that did
    not apply."""
    t = dtype(type_id)
    view = build_view(env, type_id, ops)
    return {
        "type": type_id,
        "model": envelope(type_id, view.payload),
        "scenes": _scene_docs(view.scenes),
        "outline": E.outline(view.payload, view.generated, t.edit_spec),
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
        "outline": E.outline(view.payload, gen, t.edit_spec),
        "problems": _problem_docs(view),
        "palette": [{"id": p.id, "label": p.label, "collection": p.collection,
                     "parents": list(p.parents), "hint": p.hint,
                     "template": {k: v for k, v in p.template.items() if k != "cards"}}
                    for p in t.edit_spec.palette],
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
        if t is None or (types is not None and type_id not in types) or \
                not t.applies(dst_env):
            continue
        gen = generated(dst_env, type_id)
        applied = E.apply_ops(gen, entry["ops"], t.edit_spec)
        names = _labels(entry["base_model"], t.edit_spec)
        out.append({
            "type": type_id, "title": t.title, "edits": len(entry["ops"]),
            "applies": len(entry["ops"]) - len(applied.problems),
            "problems": [{**p.to_doc(), "label": names.get(str(p.op.get("id")), None)}
                         for p in applied.problems],
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
