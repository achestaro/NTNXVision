"""Diagram edits: a list of operations replayed on a generated model, by stable id.

The Diagrams editor never stores a laid-out picture. It stores what the author did, as
operations against the element ids of the generated model (`model.py`), and every
consumer (the editor, the Word and PDF reports, the editable assets) replays them on the
model generated from the snapshot, then lays the result out with the same engine:

* ``{"op": "set", "id", "field", "value"}``: change an element's label, sublabel or role;
* ``{"op": "move", "id", "index"}``: reorder an element within its container;
* ``{"op": "add", "parent", "field", "index", "element"}``: add a user element (its id
  starts with ``user:``) to a container (``parent`` None: the model's top level);
* ``{"op": "delete", "id"}``: remove an element (or a user connector) from this diagram;
* ``{"op": "move", "id", "index", "parent"}``: with ``parent``, move into another
  container of the same kind where the type allows it (`Collection.cross`);
* ``{"op": "connect", "id", "a", "b", "label"}``: a user-added connector between two
  elements (stored in ``payload["connectors"]``);
* ``{"op": "edge", "id", "ends", "field", "value"}``: change a connector, user-added or
  generated (by its stable scene edge id; stored in ``payload["edge_edits"]``): reconnect
  an end, relabel, choose a side, offset its lane, set arrowheads, or remove it. The
  router (`connectors.py`) turns these into geometry;
* ``{"op": "place", "id", "icon", "scene", "x", "y", "w", "h", "label", "sub"}``: put an
  icon of the catalog (`icons.CATALOG`) on one figure scene, at a position (stored in
  ``payload["placed"]``); ``{"op": "position", "id", "x", "y", "w"?, "h"?}`` moves or
  resizes it, ``set`` relabels it, ``delete`` removes it, connectors may end on it;
* ``{"op": "text", "id", "line", "value"}``: rewrite one text line of the figure (a
  scene node id, or ``<edge id>#<label index>`` for an edge label; ``value`` None: as
  generated), stored in ``payload["text_edits"]``.

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
from collections.abc import Collection as Collection_
from dataclasses import dataclass, field
from typing import Any

from .model import envelope
from .scene import dumps

#: Upper bounds that keep a stored or relayed edit list sane.
MAX_OPS = 2000
MAX_TEXT = 200
USER_PREFIX = "user:"
OPS = ("set", "move", "add", "delete", "connect", "edge", "place", "position", "text")
#: Payload keys holding connector edits (never collections of model elements).
CONNECTORS = "connectors"
EDGE_EDITS = "edge_edits"
#: Payload keys holding placed icons and text edits (drawn over the layout, `icons.py`).
PLACED = "placed"
TEXT_EDITS = "text_edits"
CONNECTOR_KEYS = (CONNECTORS, EDGE_EDITS, PLACED, TEXT_EDITS)
#: Coordinates and sizes of a placed icon (pt).
MAX_COORD = 20000.0
MAX_SIZE = 8000.0
#: Most collected frames one placed frame may wrap.
MAX_WRAPS = 16
#: Most text lines a single node carries that a text edit may name.
MAX_LINE = 64
#: What an ``edge`` operation may change.
EDGE_FIELDS = ("a", "b", "label", "a_side", "b_side", "bend", "arrow", "hidden", "route")
SIDES = ("auto", "top", "right", "bottom", "left")
ARROWS = ("none", "end", "start", "both")
#: Largest lane offset (pt) a connector may carry.
MAX_BEND = 600.0


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
    #: Moving to another container of the same kind (drag across, "Container" select):
    #: "none" (never), "user" (user-added elements and notes only), "sibling" (also a
    #: collected element, within the same grandparent: a cluster to another POD of its
    #: availability zone).
    cross: str = "none"
    #: Why a collected element cannot move there (shown on refusal).
    cross_reason: str = "A collected element stays in the container the data puts it in."


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
    #: Offered on the palette (False: kept so older saved edits still replay).
    listed: bool = True


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
            if node is payload and key in CONNECTOR_KEYS:
                continue  # connector edits, not model elements (see `connectors.py`)
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
            if parent is not None and (not isinstance(parent, str) or len(parent) > 512):
                raise EditError(f"Edit {i + 1}: parent must be an id")
            field_name = op.get("field")
            if not isinstance(field_name, str) or not field_name or len(field_name) > 64:
                raise EditError(f"Edit {i + 1}: field must be text of at most 64 characters")
            palette = el.get("palette")
            if palette is not None and (not isinstance(palette, str) or len(palette) > 64):
                raise EditError(f"Edit {i + 1}: palette must be text of at most 64 characters")
            element: dict[str, Any] = {"id": ident}
            if palette is not None:
                element["palette"] = palette
            for key in ("name", "note"):
                if key in el:
                    element[key] = _text(el[key], f"Edit {i + 1} {key}", allow_none=True)
            out.append({"op": "add", "parent": parent, "field": field_name,
                        "index": _index(op.get("index"), i), "element": element})
            continue
        ident = op.get("id")
        if not isinstance(ident, str) or not ident or len(ident) > 512:
            raise EditError(f"Edit {i + 1}: id must be text")
        if kind == "connect":
            if not ident.startswith(USER_PREFIX) or len(ident) > 80:
                raise EditError(f"Edit {i + 1}: a new connector id starts with {USER_PREFIX}")
            a, b = _ref(op.get("a"), i, "a"), _ref(op.get("b"), i, "b")
            if a == b:
                raise EditError(f"Edit {i + 1}: a connector needs two different ends")
            conn: dict[str, Any] = {"op": "connect", "id": ident, "a": a, "b": b,
                                    "label": _text(op.get("label"), f"Edit {i + 1} label",
                                                   allow_none=True)}
            for side in ("a_side", "b_side"):  # the port it was drawn from (optional)
                if op.get(side) not in (None, "auto"):
                    if op[side] not in SIDES:
                        raise EditError(f"Edit {i + 1}: a side is one of {', '.join(SIDES)}")
                    conn[side] = op[side]
            out.append(conn)
            continue
        if kind == "edge":
            out.append(_clean_edge(op, ident, i))
            continue
        if kind in ("place", "position"):
            out.append(_clean_place(op, ident, i))
            continue
        if kind == "text":
            line = op.get("line")
            if not isinstance(line, int) or isinstance(line, bool) or not 0 <= line < MAX_LINE:
                raise EditError(f"Edit {i + 1}: line must be a whole number below {MAX_LINE}")
            out.append({"op": "text", "id": ident, "line": line,
                        "value": _text(op.get("value"), f"Edit {i + 1} value",
                                       allow_none=True)})
            continue
        if kind == "set":
            if op.get("field") not in ("label", "sublabel", "role"):
                raise EditError(f"Edit {i + 1}: only label, sublabel and role can be set")
            out.append({"op": "set", "id": ident, "field": op["field"],
                        "value": _text(op.get("value"), f"Edit {i + 1} value",
                                       allow_none=op["field"] != "label")})
        elif kind == "move":
            mv: dict[str, Any] = {"op": "move", "id": ident, "index": _index(op.get("index"), i)}
            if op.get("parent") is not None:
                mv["parent"] = _ref(op.get("parent"), i, "parent")
            out.append(mv)
        else:
            out.append({"op": "delete", "id": ident})
    return out


def _ref(v: Any, i: int, what: str) -> str:
    if not isinstance(v, str) or not v or len(v) > 512:
        raise EditError(f"Edit {i + 1}: {what} must be an element id")
    return v


def _clean_edge(op: dict[str, Any], ident: str, i: int) -> dict[str, Any]:
    ends = op.get("ends")
    if not isinstance(ends, list) or len(ends) != 2:
        raise EditError(f"Edit {i + 1}: ends must name the two elements the connector joins")
    clean_ends = [_ref(e, i, "ends") for e in ends]
    fld = op.get("field")
    if fld not in EDGE_FIELDS:
        raise EditError(f"Edit {i + 1}: a connector's {fld!r} cannot be changed")
    v = op.get("value")
    value: Any
    if fld in ("a", "b"):
        value = _ref(v, i, "value")
    elif fld == "label":
        value = _text(v, f"Edit {i + 1} label", allow_none=True)
    elif fld in ("a_side", "b_side"):
        if v not in SIDES:
            raise EditError(f"Edit {i + 1}: a side is one of {', '.join(SIDES)}")
        value = v
    elif fld == "arrow":
        if v not in ARROWS:
            raise EditError(f"Edit {i + 1}: arrowheads are one of {', '.join(ARROWS)}")
        value = v
    elif fld == "bend":
        if isinstance(v, bool) or not isinstance(v, (int, float)) or abs(v) > MAX_BEND:
            raise EditError(f"Edit {i + 1}: a lane offset is a number of points up to "
                            f"{MAX_BEND:,.0f}")
        value = round(float(v) * 2) / 2
        value = int(value) if value == int(value) else value
    elif fld == "route":  # back to Auto sides and no lane offset, in one edit
        if v != "auto":
            raise EditError(f"Edit {i + 1}: a route resets to auto")
        value = v
    else:  # hidden
        if not isinstance(v, bool):
            raise EditError(f"Edit {i + 1}: hidden is true or false")
        value = v
    out: dict[str, Any] = {"op": "edge", "id": ident, "ends": clean_ends, "field": fld,
                           "value": value}
    side = op.get("side")
    if fld in ("a", "b") and side not in (None, "auto"):
        if side not in SIDES:
            raise EditError(f"Edit {i + 1}: a side is one of {', '.join(SIDES)}")
        out["side"] = side  # the side of the new end it was dropped on
    return out


def _num(v: Any, i: int, what: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise EditError(f"Edit {i + 1}: {what} must be a number from {lo:,.0f} to {hi:,.0f}")
    r = round(float(v) * 2) / 2
    return int(r) if r == int(r) else r


def _clean_place(op: dict[str, Any], ident: str, i: int) -> dict[str, Any]:
    if not ident.startswith(USER_PREFIX) or len(ident) > 80:
        raise EditError(f"Edit {i + 1}: a placed icon id starts with {USER_PREFIX}")
    out: dict[str, Any] = {"op": op["op"], "id": ident,
                           "x": _num(op.get("x"), i, "x", 0, MAX_COORD),
                           "y": _num(op.get("y"), i, "y", 0, MAX_COORD)}
    for k in ("w", "h"):
        if op.get(k) is not None:
            out[k] = _num(op.get(k), i, k, 4, MAX_SIZE)
    # Nesting (`nesting.py`): the icon or collected element it goes into ("" / None: free
    # on the figure again), and the collected top-level frames a placed frame wraps.
    if "parent" in op:
        p = op.get("parent")
        if p is not None and (not isinstance(p, str) or len(p) > 512):
            raise EditError(f"Edit {i + 1}: parent must be an element id")
        out["parent"] = p or None
    if "wraps" in op:
        w = op.get("wraps")
        if not isinstance(w, list) or len(w) > MAX_WRAPS or \
                any(not isinstance(v, str) or not v or len(v) > 512 for v in w):
            raise EditError(f"Edit {i + 1}: wraps must list at most {MAX_WRAPS} element ids")
        out["wraps"] = list(dict.fromkeys(w))
    if op["op"] == "place":
        for k in ("icon", "scene"):
            v = op.get(k)
            if not isinstance(v, str) or not v or len(v) > 200:
                raise EditError(f"Edit {i + 1}: {k} must be text of at most 200 characters")
            out[k] = v
        for k in ("label", "sub"):
            if k in op:
                out[k] = _text(op[k], f"Edit {i + 1} {k}", allow_none=True)
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
    normalize_connectors(doc, spec)
    # A model type with an `edited` flag learns that an edit was replayed (captions).
    if "edited" in doc and len(problems) < len(ops):
        doc["edited"] = True
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
        new["data"] = {**new["data"], "origin": "user",
                       **({"icon": item.id} if item is not None else {})}
        items.insert(min(op["index"], len(items)), new)
        return None

    if kind == "connect":
        return _connect(doc, op, slots)
    if kind == "edge":
        return _edge(doc, op, slots)
    if kind in ("place", "position"):
        return _place(doc, op, slots)
    if kind == "text":
        return _text_edit(doc, op)
    icon_ = _placed(doc, op["id"])
    if icon_ is not None and kind in ("set", "delete"):
        if kind == "delete":
            # A container goes with what it holds (like a zone with its clusters).
            gone = _descendants(doc, op["id"])
            doc[PLACED] = [p for p in doc[PLACED] if p is not icon_ and
                           p.get("id") not in gone]
            if not doc[PLACED]:
                del doc[PLACED]
            return None
        if op["field"] == "role":
            return "this element has no role"
        if op["field"] == "label":
            if not op["value"]:
                return "a label cannot be empty"
            icon_["label"] = op["value"]
        else:
            icon_["sub"] = op["value"] or None
        return None
    if kind == "delete" and op["id"].startswith(USER_PREFIX):
        links = doc.get(CONNECTORS)
        if isinstance(links, list) and any(c.get("id") == op["id"] for c in links):
            doc[CONNECTORS] = [c for c in links if c.get("id") != op["id"]]
            if not doc[CONNECTORS]:
                del doc[CONNECTORS]
            return None
    found = _find(slots, op["id"])
    if found is None:
        return "the element is not in this snapshot"
    slot, pos, elem = found
    col = _collection(spec, slot.field)
    if kind == "move" and op.get("parent") is not None and op["parent"] != slot.parent:
        return _move_across(op, slots, slot, pos, elem, col)
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


# ─── placed icons and text edits ───────────────────────────────────────


def _placed(doc: dict[str, Any], ident: str) -> dict[str, Any] | None:
    for p in doc.get(PLACED) or []:
        if isinstance(p, dict) and p.get("id") == ident:
            return p
    return None


def placed_ids(doc: dict[str, Any]) -> set[str]:
    return {str(p.get("id")) for p in doc.get(PLACED) or [] if isinstance(p, dict)}


def _place(doc: dict[str, Any], op: dict[str, Any], slots: dict[str, Slot]) -> str | None:
    from .icons import ICONS

    item = _placed(doc, op["id"])
    if op.get("parent") and op["parent"] in _descendants(doc, op["id"]):
        return "an icon cannot go inside itself"
    if op["op"] == "position":
        if item is None:
            return "the icon is not in this diagram"
        for k in ("x", "y", "w", "h"):
            if k in op:
                item[k] = op[k]
        moved = "parent" in op and (op["parent"] or None) != item.get("parent")
        _nest_fields(item, op)
        if moved:  # last among its new siblings
            doc[PLACED] = [p for p in doc[PLACED] if p is not item] + [item]
        return None
    if item is not None or op["id"] in slots or op["id"] in _connector_ids(doc):
        return "an element with this id is already in the diagram"
    icon = ICONS.get(op["icon"])
    if icon is None:
        return "this kind of icon is not known"
    new = {"id": op["id"], "icon": icon.id, "scene": op["scene"], "x": op["x"], "y": op["y"],
           "w": op.get("w", icon.w), "h": op.get("h", icon.h),
           "label": op["label"] if op.get("label") else icon.name,
           "sub": op.get("sub", icon.sub or None) or None}
    _nest_fields(new, op)
    items = doc.get(PLACED)
    if not isinstance(items, list):
        items = doc[PLACED] = []
    items.append(new)
    return None


def _nest_fields(item: dict[str, Any], op: dict[str, Any]) -> None:
    """Set (or clear) what a placed icon sits in and what collected frames it wraps.
    Joining a new parent puts it last among that parent's children."""
    if "parent" in op:
        if op["parent"]:
            item["parent"] = op["parent"]
        else:
            item.pop("parent", None)
    if "wraps" in op:
        if op["wraps"]:
            item["wraps"] = list(op["wraps"])
        else:
            item.pop("wraps", None)


def _descendants(doc: dict[str, Any], ident: str) -> set[str]:
    """`ident` and every placed icon nested in it, at any depth."""
    items = [p for p in doc.get(PLACED) or [] if isinstance(p, dict)]
    out = {ident}
    grew = True
    while grew:
        grew = False
        for p in items:
            if p.get("parent") in out and p.get("id") not in out:
                out.add(str(p.get("id")))
                grew = True
    return out


def _text_edit(doc: dict[str, Any], op: dict[str, Any]) -> str | None:
    edits = doc.get(TEXT_EDITS)
    if not isinstance(edits, dict):
        edits = doc[TEXT_EDITS] = {}
    entry = dict(edits.get(op["id"]) or {})
    if op["value"] is None:
        entry.pop(str(op["line"]), None)
    else:
        entry[str(op["line"])] = op["value"]
    if entry:
        edits[op["id"]] = dict(sorted(entry.items(), key=lambda kv: int(kv[0])))
    else:
        edits.pop(op["id"], None)
    if not edits:
        del doc[TEXT_EDITS]
    return None


def _ends(doc: dict[str, Any], slots: dict[str, Slot]) -> set[str]:
    """What a connector may end on: the model's elements and the placed icons."""
    return set(slots) | placed_ids(doc)


# ─── connectors and moves across containers ────────────────────────────


def owner_of(end: str, slots: Collection_[str]) -> str | None:
    """The element a connector end belongs to: the end itself, or for a box drawn in
    an element (``site:pc01:pc``, a zone's Prism Central) the element its id starts
    with; None when neither is in the diagram."""
    cur = end
    while cur:
        if cur in slots:
            return cur
        if ":" not in cur:
            return None
        cur = cur.rsplit(":", 1)[0]
    return None


#: Why a connector cannot join an element to one it holds (a zone and its cluster).
NESTED = "a connector cannot join an element to one it holds"


def nested(slots: dict[str, Slot], a: str, b: str) -> bool:
    """Whether one end holds the other (a zone and a cluster in it): such a line would
    have to cross its own end, so it is refused."""
    oa, ob = owner_of(a, slots), owner_of(b, slots)
    if oa is None or ob is None:
        return False
    if a in slots and b in slots:
        return a == b or _inside(slots, a, b) or _inside(slots, b, a)
    if a in slots:  # b is a box drawn in element ob (a zone's Prism Central)
        return _inside(slots, ob, a)
    if b in slots:
        return _inside(slots, oa, b)
    return a == b


def _connector_ids(doc: dict[str, Any]) -> set[str]:
    return {str(c.get("id")) for c in doc.get(CONNECTORS) or [] if isinstance(c, dict)}


def _connect(doc: dict[str, Any], op: dict[str, Any], slots: dict[str, Slot]) -> str | None:
    if op["id"] in slots or op["id"] in _connector_ids(doc):
        return "an element with this id is already in the diagram"
    ends = _ends(doc, slots)
    for end in (op["a"], op["b"]):
        if owner_of(end, ends) is None:
            return "an end of the connector is not in this snapshot"
    if nested(slots, op["a"], op["b"]):
        return NESTED
    links = doc.get(CONNECTORS)
    if not isinstance(links, list):
        links = doc[CONNECTORS] = []
    links.append({"id": op["id"], "a": op["a"], "b": op["b"], "label": op.get("label"),
                  "a_side": op.get("a_side", "auto"), "b_side": op.get("b_side", "auto"),
                  "bend": 0, "arrow": "end",
                  "data": {"origin": "user"}})
    return None


def _edge(doc: dict[str, Any], op: dict[str, Any], slots: dict[str, Slot]) -> str | None:
    fld, value = op["field"], op["value"]
    ends_ok = _ends(doc, slots)
    if fld in ("a", "b") and owner_of(value, ends_ok) is None:
        return "the new end is not in this snapshot"
    if op["id"].startswith(USER_PREFIX):
        conn = next((c for c in doc.get(CONNECTORS) or [] if c.get("id") == op["id"]), None)
        if conn is None:
            return "the connector is not in this diagram"
        if fld == "hidden":
            if value:
                doc[CONNECTORS] = [c for c in doc[CONNECTORS] if c is not conn]
                if not doc[CONNECTORS]:
                    del doc[CONNECTORS]
            return None
        if fld in ("a", "b") and value == conn["b" if fld == "a" else "a"]:
            return "a connector needs two different ends"
        if fld in ("a", "b") and nested(slots, value, conn["b" if fld == "a" else "a"]):
            return NESTED
        if fld == "route":
            conn.update(a_side="auto", b_side="auto", bend=0)
            return None
        conn[fld] = value
        if fld in ("a", "b") and op.get("side"):
            conn[f"{fld}_side"] = op["side"]
        return None
    for end in op["ends"]:
        if owner_of(end, ends_ok) is None:
            return "an end of the connector is not in this snapshot"
    edits = doc.get(EDGE_EDITS)
    if not isinstance(edits, dict):
        edits = doc[EDGE_EDITS] = {}
    entry = dict(edits.get(op["id"]) or {})
    entry["ends"] = list(op["ends"])
    if fld in ("a", "b"):
        other = entry.get("b" if fld == "a" else "a") or op["ends"][1 if fld == "a" else 0]
        if value == other:
            return "a connector needs two different ends"
        if nested(slots, value, other):
            return NESTED
    if fld == "route":
        for k in ("a_side", "b_side", "bend"):
            entry.pop(k, None)
    elif (fld == "label" and value is None) or (fld == "hidden" and not value) or \
            (fld in ("a_side", "b_side") and value == "auto") or (fld == "bend" and not value):
        entry.pop(fld, None)
    else:
        entry[fld] = value
        if fld in ("a", "b") and op.get("side"):
            entry[f"{fld}_side"] = op["side"]
    if set(entry) == {"ends"}:
        edits.pop(op["id"], None)
    else:
        edits[op["id"]] = entry
    if not edits:
        del doc[EDGE_EDITS]
    return None


def normalize_connectors(doc: dict[str, Any], spec: EditSpec) -> None:
    """Drop connector edits an edit left dangling (an end removed later in the list)."""
    if not any(k in doc for k in CONNECTOR_KEYS):
        return
    ids = set(walk(doc, spec)) | placed_ids(doc)

    def live(end: Any) -> bool:
        return isinstance(end, str) and owner_of(end, ids) is not None

    links = [c for c in doc.get(CONNECTORS) or []
             if isinstance(c, dict) and live(c.get("a")) and live(c.get("b"))]
    if links:
        doc[CONNECTORS] = links
    else:
        doc.pop(CONNECTORS, None)
    edits = {k: v for k, v in sorted((doc.get(EDGE_EDITS) or {}).items())
             if isinstance(v, dict) and all(live(e) for e in v.get("ends", []))
             and all(live(v[f]) for f in ("a", "b") if f in v)}
    if edits:
        doc[EDGE_EDITS] = edits
    else:
        doc.pop(EDGE_EDITS, None)


def _is_user(ident: str, elem: dict[str, Any]) -> bool:
    return ident.startswith(USER_PREFIX) or elem.get("kind") == "note"


def cross_refusal(slots: dict[str, Slot], ident: str, elem: dict[str, Any],
                  col: Collection, target: str) -> str | None:
    """Why `ident` may not move into container `target` (None: it may)."""
    slot = slots.get(ident)
    tslot = slots.get(target)
    if slot is None or tslot is None:
        return "its new container is not in the diagram"
    if target == ident or _inside(slots, target, ident):
        return "an element cannot go inside itself"
    pslot = slots.get(slot.parent or "")
    if pslot is None or tslot.field != pslot.field:
        return "it cannot go into that kind of container"
    if col.cross == "none" or not col.reorder:
        return f"{col.kind.lower()}s keep their container"
    if _is_user(ident, elem):
        return None
    if col.cross == "sibling" and tslot.parent == pslot.parent:
        return None
    return col.cross_reason


def _inside(slots: dict[str, Slot], ident: str, owner: str) -> bool:
    seen: set[str] = set()
    cur: str | None = ident
    while cur is not None and cur not in seen:
        if cur == owner:
            return True
        seen.add(cur)
        s = slots.get(cur)
        cur = s.parent if s is not None else None
    return False


def _move_across(op: dict[str, Any], slots: dict[str, Slot], slot: Slot, pos: int,
                 elem: dict[str, Any], col: Collection) -> str | None:
    reason = cross_refusal(slots, op["id"], elem, col, op["parent"])
    if reason:
        return reason.rstrip(".")[0].lower() + reason.rstrip(".")[1:]
    found = _find(slots, op["parent"])
    assert found is not None
    parent = found[2]
    items = parent.get(slot.field)
    if items is None:
        items = parent[slot.field] = []
    if not isinstance(items, list):
        return "its new container is not a list"
    slot.items.pop(pos)
    items.insert(min(op["index"], len(items)), elem)
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
        else:
            # Only fields the old model had: a field a newer app version added to the
            # model is not a change in the collected data.
            keys = sorted(k for k in a[ident] if a[ident].get(k) != b[ident].get(k))
            if not keys:
                continue
            values = {k: [a[ident].get(k), b[ident].get(k)] for k in keys
                      if _plain(a[ident].get(k)) and _plain(b[ident].get(k))}
            out.append(Change(ident, "changed", _label_of(b[ident]), keys, values))
    return out


def _elements(payload: dict[str, Any], spec: EditSpec
              ) -> dict[str, tuple[dict[str, Any], Collection]]:
    out = {}
    for ident, slot in walk(payload, spec).items():
        found = _find({ident: slot}, ident)
        if found is not None:
            out[ident] = (found[2], _collection(spec, slot.field))
    return out


def _named(items: list[str], limit: int = 3) -> str:
    shown = items[:limit]
    more = len(items) - len(shown)
    return "; ".join(shown) + (f"; and {more:,} more" if more > 0 else "")


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


#: Fields that tie an element to others it links (a replication flow, an AZ pairing).
LINK_FIELDS = ("a", "b", "site_a", "site_b")


def _scoped(payload: dict[str, Any], spec: EditSpec, scope: Collection_[str]
            ) -> dict[str, tuple[dict[str, Any], Collection]]:
    """The elements of `payload` inside `scope`: an element whose id, an owner's id or
    a linked end (`LINK_FIELDS`) is in it, in walk order."""
    slots = walk(payload, spec)
    elems = _elements(payload, spec)

    def inside(ident: str | None) -> bool:
        seen: set[str] = set()
        while ident is not None and ident not in seen:
            if ident in scope:
                return True
            seen.add(ident)
            slot = slots.get(ident)
            ident = slot.parent if slot is not None else None
        return False

    return {i: ec for i, ec in elems.items()
            if inside(i) or any(isinstance(ec[0].get(k), str) and inside(ec[0][k])
                                for k in LINK_FIELDS)}


def disclosure(generated: dict[str, Any], edited: dict[str, Any], spec: EditSpec,
               carried_from: str | None = None,
               scope: Collection_[str] | None = None) -> str | None:
    """One design-summary bullet saying how a saved edit differs from the data:
    elements not drawn, user-added items, renames, roles and notes set by the author,
    and the snapshot the edits were carried from automatically (`carried_from`).
    None when the two models are the same. With `scope` (element ids: an availability
    zone, a profile), only the edits inside it count (`_scoped`): a figure of one zone
    discloses that zone's edits, and None when it has none."""
    if generated == edited:
        return None
    links = _connector_parts(edited, spec, scope)
    if scope is None:
        gen, ed = _elements(generated, spec), _elements(edited, spec)
    else:
        gen, ed = _scoped(generated, spec, scope), _scoped(edited, spec, scope)
        if [(i, e) for i, (e, _) in gen.items()] == [(i, e) for i, (e, _) in ed.items()] \
                and not links:
            return None
    parts: list[str] = []
    removed: dict[str, list[str]] = {}
    owner = {i: s.parent for i, s in walk(generated, spec).items()}
    for ident, (elem, col) in gen.items():
        if ident in ed or ident.startswith(USER_PREFIX) or not col.delete:
            continue
        up = owner.get(ident)
        if up is not None and up not in ed:  # inside a removed container: named there
            continue
        kind = (col.kind_of(elem) if col.kind_of else col.kind).lower()
        name = str(elem.get(col.label) if col.label else
                   (col.title_of(elem) if col.title_of else _label_of(elem)))
        nodes = elem.get("nodes")
        removed.setdefault(kind, []).append(
            f"{name}, {_count(nodes, 'node')}" if isinstance(nodes, int) else name)
    for kind, names in sorted(removed.items()):
        many = kind[:-1] + "ies" if kind.endswith("y") else kind + "s"
        parts.append(f"{_count(len(names), kind, many)} ({_named(names)}) not drawn")
    user = [i for i in ed if i.startswith(USER_PREFIX)]
    if scope is None:
        user += sorted(placed_ids(edited))
    if user:
        parts.append(_count(len(user), "user-added item"))
    renamed, roles, notes = [], 0, 0
    for ident, (elem, col) in ed.items():
        if ident not in gen:
            continue
        before = gen[ident][0]
        if col.label and elem.get(col.label) != before.get(col.label):
            renamed.append(f"{before.get(col.label)} renamed to \u201c{elem.get(col.label)}\u201d")
        if col.roles and elem.get("role") != before.get("role"):
            roles += 1
        if col.sublabel and elem.get(col.sublabel) != before.get(col.sublabel):
            notes += 1
    moved = _moved_across(generated, edited, spec, gen, ed)
    if moved:
        parts.append(", ".join(moved) if len(moved) <= 3 else
                     f"{len(moved):,} moved to another container, including "
                     f"{', '.join(moved[:2])}")
    if renamed:
        parts.append(", ".join(renamed) if len(renamed) <= 3 else
                     f"{len(renamed):,} renamed, including {', '.join(renamed[:2])}")
    if roles:
        parts.append(f"{_count(roles, 'role')} set by the author")
    if notes:
        parts.append(f"{_count(notes, 'note')} added")
    parts += links
    texts = edited.get(TEXT_EDITS) or {}
    if scope is None and texts:
        parts.append(f"{_count(sum(len(v) for v in texts.values()), 'text line')} rewritten")
    if not parts:
        parts.append("elements reordered")
    origin = f" (edits carried from {carried_from})" if carried_from else ""
    return (f"Figure edited on the Diagrams page{origin}: {', '.join(parts)}. "
            "Counts are as collected.")


def _moved_across(generated: dict[str, Any], edited: dict[str, Any], spec: EditSpec,
                  gen: dict[str, Any], ed: dict[str, Any]) -> list[str]:
    """"VDI-01 moved to POD B" for each collected element now in another container."""
    before = walk(generated, spec)
    after = walk(edited, spec)
    if not any(i in before and i in after and before[i].parent != after[i].parent
               for i in ed if i in gen):
        return []
    labels = {i: _label_of(e) for i, (e, _) in ed.items()}
    titles = {o["id"]: o["label"] for o in outline(edited, edited, spec)}
    out = []
    for ident, (elem, col) in ed.items():
        if ident not in gen or ident not in before or ident not in after:
            continue
        if before[ident].parent != after[ident].parent:
            where = after[ident].parent or ""
            name = elem.get(col.label) if col.label else labels.get(ident)
            out.append(f"{name} moved to {titles.get(where) or after[ident].parent_label}")
    return out


def _connector_parts(edited: dict[str, Any], spec: EditSpec,
                     scope: Collection_[str] | None) -> list[str]:
    """The connector edits of `edited` in words. With `scope`, only those whose ends
    are all inside it: a figure of one zone or part draws a connector only when both
    its ends are there, so only those mark it edited (the report says separately which
    connectors no figure part could draw)."""
    slots = walk(edited, spec)

    def inside(ident: Any) -> bool:
        if scope is None:
            return True
        own = owner_of(ident, slots) if isinstance(ident, str) else None
        return own is not None and any(_inside(slots, own, s) for s in scope)

    added = [c for c in edited.get(CONNECTORS) or []
             if inside(c.get("a")) and inside(c.get("b"))]
    counts = {"removed": 0, "reconnected": 0, "relabeled": 0, "with the label removed": 0,
              "rerouted": 0, "with new arrowheads": 0}
    for entry in (edited.get(EDGE_EDITS) or {}).values():
        ends = list(entry.get("ends", []))
        for i, f in enumerate(("a", "b")):
            if f in entry and i < len(ends):
                ends[i] = entry[f]
        if entry.get("hidden"):
            ends = list(entry.get("ends", []))  # drawn as removed where it was
        if not ends or not all(inside(e) for e in ends):
            continue
        if entry.get("hidden"):
            counts["removed"] += 1
            continue
        if "a" in entry or "b" in entry:
            counts["reconnected"] += 1
        if "label" in entry:
            counts["with the label removed" if not entry["label"] else "relabeled"] += 1
        if any(k in entry for k in ("a_side", "b_side", "bend")):
            counts["rerouted"] += 1
        if "arrow" in entry:
            counts["with new arrowheads"] += 1
    parts = []
    if added:
        parts.append(f"{_count(len(added), 'connector')} added by the author")
    for word, n in counts.items():
        if n:
            parts.append(f"{_count(n, 'connector')} {word}")
    return parts


def touched(op: dict[str, Any]) -> set[str]:
    """Element ids an operation depends on (its ends, for a connector edit; for an end
    on a box inside an element, that element too)."""
    ids = {op[k] for k in ("id", "parent", "a", "b") if isinstance(op.get(k), str)}
    ids |= {e for e in op.get("ends") or [] if isinstance(e, str)}
    if op.get("op") == "edge" and op.get("field") in ("a", "b") and \
            isinstance(op.get("value"), str):
        ids.add(op["value"])
    for ident in list(ids):
        while ":" in ident:
            ident = ident.rsplit(":", 1)[0]
            ids.add(ident)
    return ids


# ─── the editor's outline ──────────────────────────────────────────────


def outline(payload: dict[str, Any], generated: dict[str, Any], spec: EditSpec
            ) -> list[dict[str, Any]]:
    """What the inspector shows for each element of the (edited) payload: kind, origin,
    label, sublabel, role, container and position, and what may be done with it."""
    gen = _flat(generated, spec)
    out: list[dict[str, Any]] = []
    slots = walk(payload, spec)
    titles: dict[str, str] = {}
    for ident, slot in slots.items():
        found = _find({ident: slot}, ident)
        if found is not None:
            col = _collection(spec, slot.field)
            elem = found[2]
            titles[ident] = str(elem.get(col.label) if col.label and elem.get(col.label)
                                else (col.title_of(elem) if col.title_of
                                      else _label_of(elem)))
    for ident, slot in slots.items():
        found = _find({ident: slot}, ident)
        if found is None:
            continue
        _, pos, elem = found
        col = _collection(spec, slot.field)
        targets: list[dict[str, str]] = []
        refusal: str | None = None
        pslot = slots.get(slot.parent or "")
        if pslot is not None and col.reorder and col.cross != "none":
            for other, oslot in slots.items():
                if other == slot.parent or oslot.field != pslot.field:
                    continue
                why = cross_refusal(slots, ident, elem, col, other)
                if why is None:
                    targets.append({"id": other, "label": titles.get(other, other)})
                elif refusal is None:
                    refusal = why
        elif pslot is not None:
            refusal = cross_refusal(slots, ident, elem, col, slot.parent or "") \
                if col.cross == "none" else None
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
            else ("Added on the Diagrams page and drawn like a collected element. It never "
                  "appears in the tables; the report's figure summary says the figure was "
                  "edited."),
            "can_rename": col.label is not None,
            "can_sublabel": col.sublabel is not None,
            "can_reorder": col.reorder and len(slot.items) > 1,
            "can_delete": col.delete or user,
            "move_targets": targets,
            "move_refusal": refusal,
        })
    out += placed_outline(payload)
    return out


def placed_outline(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The inspector entries of the placed icons (free: dragged anywhere on the figure)."""
    from .icons import ICONS

    items = [p for p in payload.get(PLACED) or [] if isinstance(p, dict)
             and str(p.get("icon")) in ICONS]
    out = []
    names = {str(p.get("id")): str(p.get("label") or ICONS[str(p["icon"])].name)
             for p in items}
    for i, p in enumerate(items):
        icon = ICONS[str(p["icon"])]
        holder = p.get("parent") if isinstance(p.get("parent"), str) else None
        out.append({
            "id": p["id"], "kind": icon.label, "collection": PLACED, "parent": None,
            "parent_label": names.get(holder or "", "its container" if holder else
                                      "this figure"),
            # Nesting (`nesting.py`): what it sits in, the collected frames it wraps.
            "nest_parent": holder, "wraps": list(p.get("wraps") or []),
            "index": i, "count": len(items),
            "label": p.get("label") or icon.name, "sublabel": p.get("sub"), "role": None,
            "roles": [], "origin": "user", "collected_label": None,
            "source": "Added on the Diagrams page and drawn like a collected "
                      f"{icon.label.lower()}. It never appears in the tables; the report's "
                      "figure summary says the figure was edited.",
            "can_rename": True, "can_sublabel": icon.id != "text", "can_reorder": False,
            "can_delete": True, "move_targets": [], "move_refusal": None, "free": True,
            "icon": icon.id, "scene": p.get("scene"),
            "x": p.get("x"), "y": p.get("y"), "w": p.get("w"), "h": p.get("h"),
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
