"""Diagram edits: a list of operations replayed on a generated model, by stable id.

The Diagrams editor never stores a laid-out picture. It stores what the author did, as
operations against the element ids of the generated model (`model.py`), and every
consumer (the editor, the Word and PDF reports, the editable assets) replays them on the
model generated from the snapshot, then lays the result out with the same engine:

* ``{"op": "set", "id", "field", "value"}``: change an element's label, sublabel or role;
* ``{"op": "move", "id", "index"}``: reorder an element within its container;
* ``{"op": "add", "parent", "field", "index", "element"}``: add a user element (its id
  starts with ``user:``) to a container (``parent`` None: the model's top level);
* ``{"op": "delete", "id"}``: remove an element from this diagram.

Replaying is pure and deterministic: the same model and the same operations give the same
document, byte for byte. An operation whose element is gone (a cluster no longer in a
newer snapshot) is reported, never silently dropped.

The engine works on any registered diagram type: a model payload is a JSON tree whose
editable elements are objects with an ``id`` inside lists ("collections"). A type
describes its collections, labels, roles and palette with an `EditSpec`
(`DiagramType.edit`); a type without one gets a generic spec (rename by ``name``,
reorder, delete, no palette).
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .model import envelope
from .scene import dumps

#: Upper bounds that keep a stored or relayed edit list sane.
MAX_OPS = 2000
MAX_TEXT = 200
USER_PREFIX = "user:"
OPS = ("set", "move", "add", "delete")


@dataclass(frozen=True)
class Collection:
    """A list field of the payload whose items are editable elements."""

    field: str
    #: What one item is called in the editor ("Cluster", "Availability zone").
    kind: str
    #: The field shown and edited as the label (None: no rename).
    label: str | None = "name"
    #: The field edited as the sublabel (None: none).
    sublabel: str | None = None
    #: (value, label) choices of the "role" field (empty: no role).
    roles: tuple[tuple[str, str], ...] = ()
    reorder: bool = True
    delete: bool = True
    #: Per element: the kind shown for it ("Note" for a note card), else `kind`.
    kind_of: Callable[[dict[str, Any]], str] | None = None
    #: Per element: the text shown for an element without a label field.
    title_of: Callable[[dict[str, Any]], str] | None = None
    #: Per element: whether `roles` apply to it (None: to every element).
    has_role: Callable[[dict[str, Any]], bool] | None = None


@dataclass(frozen=True)
class PaletteItem:
    """Something the author can add: a template element put into `collection`."""

    id: str
    label: str
    collection: str
    template: dict[str, Any]
    #: Container kinds (collection field of the parent) it can go into; () = top level.
    parents: tuple[str, ...] = ()
    hint: str = ""


@dataclass(frozen=True)
class EditSpec:
    collections: tuple[Collection, ...] = ()
    palette: tuple[PaletteItem, ...] = ()
    #: Repairs a payload after edits (drop links to deleted elements, ...). In place.
    normalize: Callable[[dict[str, Any]], None] | None = None
    #: Human description of an element's origin in the snapshot, for the inspector.
    source: Callable[[dict[str, Any]], str] | None = None

    def collection(self, name: str) -> Collection | None:
        for c in self.collections:
            if c.field == name:
                return c
        return None


GENERIC = EditSpec()


class EditError(ValueError):
    """An operation that is malformed (not merely stale)."""


# ─── walking a payload ─────────────────────────────────────────────────


@dataclass
class Slot:
    """Where an element lives: the list holding it and its owner."""

    items: list[dict[str, Any]]
    field: str
    #: Owner element id (None: the payload's top level).
    parent: str | None
    parent_label: str


def _is_collection(v: Any) -> bool:
    return isinstance(v, list) and bool(v) and all(
        isinstance(x, dict) and isinstance(x.get("id"), str) for x in v)


def _label_of(d: dict[str, Any]) -> str:
    for k in ("name", "label", "title"):
        if isinstance(d.get(k), str) and d[k]:
            return str(d[k])
    return str(d.get("id", ""))


def walk(payload: dict[str, Any], spec: EditSpec) -> dict[str, Slot]:
    """Every element id in `payload` -> its slot. Known collections (the spec's) are
    walked even when empty; any other non-empty list of id'd objects is a collection
    too, so a type without a spec is still editable."""
    out: dict[str, Slot] = {}
    known = {c.field for c in spec.collections}

    def visit(node: dict[str, Any], owner: str | None, owner_label: str) -> None:
        for key in sorted(node):
            value = node[key]
            if key in known and isinstance(value, list) or _is_collection(value):
                if not all(isinstance(x, dict) for x in value):
                    continue
                for item in value:
                    ident = item.get("id")
                    if isinstance(ident, str) and ident not in out:
                        out[ident] = Slot(value, key, owner, owner_label)
                    visit(item, ident if isinstance(ident, str) else owner,
                          _label_of(item))
            elif isinstance(value, dict):
                visit(value, owner, owner_label)

    visit(payload, None, "diagram")
    return out


def _find(slots: dict[str, Slot], ident: str) -> tuple[Slot, int, dict[str, Any]] | None:
    slot = slots.get(ident)
    if slot is None:
        return None
    for i, item in enumerate(slot.items):
        if item.get("id") == ident:
            return slot, i, item
    return None


def _collection(spec: EditSpec, name: str) -> Collection:
    c = spec.collection(name)
    if c is not None:
        return c
    if spec.collections:  # a typed spec only edits what it declares
        return Collection(field=name, kind=name, label=None, reorder=False, delete=False)
    return Collection(field=name, kind=name.rstrip("s").replace("_", " ").capitalize())


# ─── validation ────────────────────────────────────────────────────────


def _text(v: Any, what: str, allow_none: bool = False) -> str | None:
    if v is None and allow_none:
        return None
    if not isinstance(v, str):
        raise EditError(f"{what} must be text")
    v = " ".join(v.split())
    if len(v) > MAX_TEXT:
        raise EditError(f"{what} is longer than {MAX_TEXT} characters")
    # House rule: no em dashes in any figure text.
    return v.replace(chr(0x2014), ",")


def clean_ops(raw: Any) -> list[dict[str, Any]]:
    """Shape-check a list of operations from outside (the UI, the store file); returns
    copies holding only the known keys. Raises `EditError`."""
    if not isinstance(raw, list):
        raise EditError("Edits must be a list")
    if len(raw) > MAX_OPS:
        raise EditError(f"At most {MAX_OPS:,} edits per diagram")
    out: list[dict[str, Any]] = []
    for i, op in enumerate(raw):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise EditError(f"Edit {i + 1} is not a known operation")
        kind = op["op"]
        if kind == "add":
            el = op.get("element")
            if not isinstance(el, dict):
                raise EditError(f"Edit {i + 1}: the new element is missing")
            ident = el.get("id")
            if not isinstance(ident, str) or not ident.startswith(USER_PREFIX) or \
                    len(ident) > 80:
                raise EditError(f"Edit {i + 1}: a new element id starts with {USER_PREFIX}")
            parent = op.get("parent")
            if parent is not None and not isinstance(parent, str):
                raise EditError(f"Edit {i + 1}: parent must be an id")
            if not isinstance(op.get("field"), str):
                raise EditError(f"Edit {i + 1}: field must be text")
            out.append({"op": "add", "parent": parent, "field": op["field"],
                        "index": _index(op.get("index"), i),
                        "element": {k: copy.deepcopy(v) for k, v in el.items()
                                    if k in ("id", "name", "note", "kind", "palette")}})
            continue
        ident = op.get("id")
        if not isinstance(ident, str) or not ident or len(ident) > 512:
            raise EditError(f"Edit {i + 1}: id must be text")
        if kind == "set":
            if op.get("field") not in ("label", "sublabel", "role"):
                raise EditError(f"Edit {i + 1}: only label, sublabel and role can be set")
            out.append({"op": "set", "id": ident, "field": op["field"],
                        "value": _text(op.get("value"), f"Edit {i + 1} value",
                                       allow_none=op["field"] != "label")})
        elif kind == "move":
            out.append({"op": "move", "id": ident, "index": _index(op.get("index"), i)})
        else:
            out.append({"op": "delete", "id": ident})
    return out


def _index(v: Any, i: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v < 0 or v > 100_000:
        raise EditError(f"Edit {i + 1}: index must be a whole number")
    return v


# ─── replay ────────────────────────────────────────────────────────────


@dataclass
class Problem:
    index: int  # position in the op list
    op: dict[str, Any]
    reason: str

    def to_doc(self) -> dict[str, Any]:
        return {"index": self.index, "op": self.op, "reason": self.reason}


@dataclass
class Applied:
    payload: dict[str, Any]
    problems: list[Problem] = field(default_factory=list)


def apply_ops(payload: dict[str, Any], ops: list[dict[str, Any]], spec: EditSpec
              ) -> Applied:
    """Replay `ops` (already `clean_ops`'d) on a deep copy of `payload`."""
    doc = copy.deepcopy(payload)
    problems: list[Problem] = []
    palette = {p.id: p for p in spec.palette}
    for i, op in enumerate(ops):
        slots = walk(doc, spec)
        reason = _apply_one(doc, op, slots, spec, palette)
        if reason:
            problems.append(Problem(i, op, reason))
    if spec.normalize is not None:
        spec.normalize(doc)
    return Applied(doc, problems)


def _apply_one(doc: dict[str, Any], op: dict[str, Any], slots: dict[str, Slot],
               spec: EditSpec, palette: dict[str, PaletteItem]) -> str | None:
    kind = op["op"]
    if kind == "add":
        el = op["element"]
        if el["id"] in slots:
            return "an element with this id is already in the diagram"
        item = palette.get(str(el.get("palette") or ""))
        if spec.palette and item is None:
            return "this kind of element cannot be added to this diagram"
        if item is not None and item.collection != op["field"]:
            return "this kind of element cannot go there"
        if op["parent"] is None:
            if item is not None and item.parents:
                return "this kind of element needs a container"
            items = doc.get(op["field"])
            if items is None:
                items = doc[op["field"]] = []
        else:
            found = _find(slots, op["parent"])
            if found is None:
                return "its container is not in the diagram"
            pslot, _, parent = found
            if item is not None and item.parents and pslot.field not in item.parents:
                return "this kind of element cannot go there"
            items = parent.get(op["field"])
            if items is None:
                items = parent[op["field"]] = []
        if not isinstance(items, list):
            return "its container is not a list"
        new = copy.deepcopy(item.template) if item is not None else {}
        new.update({k: v for k, v in el.items() if k not in ("palette", "kind")})
        new["id"] = el["id"]
        new.setdefault("data", {})
        new["data"] = {**new["data"], "origin": "user"}
        items.insert(min(op["index"], len(items)), new)
        return None

    found = _find(slots, op["id"])
    if found is None:
        return "the element is not in this snapshot"
    slot, pos, elem = found
    col = _collection(spec, slot.field)
    if kind == "delete":
        if not col.delete and not op["id"].startswith(USER_PREFIX):
            return "this element cannot be removed"
        del slot.items[pos]
        return None
    if kind == "move":
        if not col.reorder:
            return "this element cannot be reordered"
        slot.items.pop(pos)
        slot.items.insert(min(op["index"], len(slot.items)), elem)
        return None
    # set
    target = {"label": col.label, "sublabel": col.sublabel, "role": "role"}[op["field"]]
    if op["field"] == "role":
        if not col.roles or (col.has_role is not None and not col.has_role(elem)):
            return "this element has no role"
        if op["value"] is not None and op["value"] not in {v for v, _ in col.roles}:
            return "not a valid role"
    if target is None:
        return f"this element has no {op['field']}"
    if op["field"] == "label" and not op["value"]:
        return "a label cannot be empty"
    elem[target] = op["value"] if op["field"] == "label" else (op["value"] or None)
    return None


# ─── hashes and comparisons ────────────────────────────────────────────


def payload_hash(type_id: str, payload: dict[str, Any]) -> str:
    """sha256 of the canonical model document (the stale check)."""
    return hashlib.sha256(dumps(envelope(type_id, payload))).hexdigest()


def _flat(d: dict[str, Any], spec: EditSpec) -> dict[str, dict[str, Any]]:
    """Each element's own fields (nested collections left out), by id."""
    out: dict[str, dict[str, Any]] = {}
    for ident, slot in walk(d, spec).items():
        found = _find({ident: slot}, ident)
        if found is None:
            continue
        elem = found[2]
        out[ident] = {k: v for k, v in elem.items()
                      if not (isinstance(v, list) and v and all(
                          isinstance(x, dict) and "id" in x for x in v))}
    return out


@dataclass
class Change:
    id: str
    kind: str  # added | removed | changed
    label: str
    fields: list[str] = field(default_factory=list)
    #: [before, after] for each changed field holding a plain value (number, text).
    values: dict[str, list[Any]] = field(default_factory=dict)

    def to_doc(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "fields": self.fields, "values": self.values}


def _plain(v: Any) -> bool:
    return v is None or isinstance(v, (int, float, str, bool))


def diff(old: dict[str, Any], new: dict[str, Any], spec: EditSpec) -> list[Change]:
    """Elements added, removed or changed between two generated payloads (sorted)."""
    a, b = _flat(old, spec), _flat(new, spec)
    out: list[Change] = []
    for ident in sorted(set(a) | set(b)):
        if ident not in b:
            out.append(Change(ident, "removed", _label_of(a[ident])))
        elif ident not in a:
            out.append(Change(ident, "added", _label_of(b[ident])))
        elif a[ident] != b[ident]:
            keys = sorted(k for k in set(a[ident]) | set(b[ident])
                          if a[ident].get(k) != b[ident].get(k))
            values = {k: [a[ident].get(k), b[ident].get(k)] for k in keys
                      if _plain(a[ident].get(k)) and _plain(b[ident].get(k))}
            out.append(Change(ident, "changed", _label_of(b[ident]), keys, values))
    return out


def touched(op: dict[str, Any]) -> set[str]:
    """Element ids an operation depends on."""
    ids = {op[k] for k in ("id", "parent") if isinstance(op.get(k), str)}
    return ids


# ─── the editor's outline ──────────────────────────────────────────────


def outline(payload: dict[str, Any], generated: dict[str, Any], spec: EditSpec
            ) -> list[dict[str, Any]]:
    """What the inspector shows for each element of the (edited) payload: kind, origin,
    label, sublabel, role, container and position, and what may be done with it."""
    gen = _flat(generated, spec)
    out: list[dict[str, Any]] = []
    for ident, slot in walk(payload, spec).items():
        found = _find({ident: slot}, ident)
        if found is None:
            continue
        _, pos, elem = found
        col = _collection(spec, slot.field)
        user = ident.startswith(USER_PREFIX)
        collected = gen.get(ident, {})
        roles = col.roles if col.has_role is None or col.has_role(elem) else ()
        out.append({
            "id": ident,
            "kind": col.kind_of(elem) if col.kind_of else col.kind,
            "collection": slot.field,
            "parent": slot.parent,
            "parent_label": slot.parent_label,
            "index": pos,
            "count": len(slot.items),
            "label": elem.get(col.label) if col.label else
            (col.title_of(elem) if col.title_of else _label_of(elem)),
            "sublabel": elem.get(col.sublabel) if col.sublabel else None,
            "role": elem.get("role") if roles else None,
            "roles": [{"value": v, "label": lab} for v, lab in roles],
            "origin": "user" if user else "collected",
            "collected_label": collected.get(col.label) if col.label and not user else None,
            "source": (spec.source(elem) if spec.source else _source(elem)) if not user
            else ("Added on the Diagrams page. Exports mark it USER-ADDED; it never "
                  "appears in the tables."),
            "can_rename": col.label is not None,
            "can_sublabel": col.sublabel is not None,
            "can_reorder": col.reorder and len(slot.items) > 1,
            "can_delete": col.delete or user,
        })
    return out


def _source(elem: dict[str, Any]) -> str:
    data = elem.get("data") or {}
    if not isinstance(data, dict) or not data:
        return "Generated from the snapshot."
    parts = [f"{k.replace('_', ' ')} {v}" for k, v in sorted(data.items()) if v]
    return "From the snapshot: " + ", ".join(parts) + "."


def iter_user(payload: dict[str, Any], spec: EditSpec) -> Iterator[str]:
    """Ids of the user-added elements in a payload."""
    for ident in walk(payload, spec):
        if ident.startswith(USER_PREFIX):
            yield ident
