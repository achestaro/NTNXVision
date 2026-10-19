"""Diagram model documents: what a diagram shows and in which order, no coordinates.

`facts.py` builds a model from an Environment (sorted, stable ids); a layout turns it into
scenes. The model is also what the Diagrams editor (P4) edits structurally (rename,
reorder cards within a site, add a user component, delete), after which the same layout
runs again. Values stay raw (counts, seconds, bytes) and are formatted by the layout, so
an edited model lays out exactly like a generated one.

Every diagram type stores its model in one envelope, ``{"schema", "type", "payload"}``
(`envelope` / `from_envelope`), so a store can save any type without knowing it.

Ids come from object identity, never from positions or counters:
``site:<pc host>``, ``site:<pc host>:pc``, ``cluster:<uuid>``, ``az:<name>``,
``link:<kind>:<a>-><b>``, ``user:<uuid4 minted by the editor>``.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from .scene import dumps

MODEL_SCHEMA = "ntnxvision.diagram.model/1"


@dataclass
class ExternalArray:
    """An external storage array a cluster's containers live on (e.g. Pure FlashArray)."""

    id: str
    product: str  # "Pure FlashArray", "Dell PowerStore", ...
    name: str | None = None
    containers: int = 0


@dataclass
class Card:
    id: str
    name: str
    #: "cluster" (collected), "user" (a component added in the Diagrams editor) or
    #: "note" (a free-text note added in the editor).
    kind: str = "cluster"
    hypervisor: str | None = None  # "AHV" / "ESXi" / "AHV, ESXi"
    nodes: int | None = None
    rf: int | None = None
    #: Block models, most common first.
    models: list[str] = field(default_factory=list)
    aos: str | None = None
    raw_bytes: int | None = None
    #: Containers on the cluster's own storage (DSF), internal ones left out.
    containers: int = 0
    #: Data services and management integrations present (Files, Objects, NDB, vCenter).
    services: list[str] = field(default_factory=list)
    hosts_pc: bool = False
    compute_only: bool = False
    external: list[ExternalArray] = field(default_factory=list)
    note: str | None = None
    #: Role set in the Diagrams editor: "workload" (no chip), "hosts_pc", "mgmt" or
    #: "dr_target". None: from the data (HOSTS PC when the cluster runs Prism Central).
    role: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class Service:
    label: str  # "DR", "Flow", "VPCs", "X-Play"
    count: int  # 0: enabled with nothing configured yet


@dataclass
class PcBlock:
    id: str
    version: str | None = None
    vms: int | None = None  # 1 = single VM, 3 = scale-out
    #: One PC VM's configured size, when the snapshot has it.
    vm_vcpus: int | None = None
    vm_mem_gib: int | None = None
    #: Totals across the PC VMs (older snapshots).
    vcpus: int | None = None
    mem_gib: int | None = None
    size: str | None = None  # "Small", "Large", "X-Large" when collected
    services: list[Service] = field(default_factory=list)


@dataclass
class Site:
    """One availability zone: a Prism Central and its clusters (or, in a direct Prism
    Element run, the Prism Element and its cluster)."""

    id: str
    name: str
    kind: str = "pc"  # pc | pe | user (an availability zone added in the editor)
    pc: PcBlock | None = None
    cards: list[Card] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)
    #: Second header line of a user-added zone.
    note: str | None = None


@dataclass
class Remote:
    """An availability zone outside this inventory (a paired PC, Nutanix Cloud)."""

    id: str
    name: str
    kind: str = "pc"  # pc | xi
    region: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class Repl:
    type: str  # Sync | NearSync | Async
    rpo_seconds: int | None = None


@dataclass
class Link:
    """AZ pairing (`kind` pairing, `connected` / `state`) or replication between `a` and `b`
    (`kind` replication: `forward` is a to b, `backward` b to a, per (type, RPO))."""

    id: str
    kind: str  # pairing | replication
    a: str
    b: str
    connected: bool = True
    #: Why a pairing is not connected, as shown: "not connected", "pending".
    state: str = ""
    forward: list[Repl] = field(default_factory=list)
    backward: list[Repl] = field(default_factory=list)
    policies: int = 0

    @property
    def both(self) -> bool:
        return bool(self.forward) and bool(self.backward)

    @property
    def repl(self) -> list[Repl]:
        """Every (type, RPO) on the link, either direction, shortest RPO first."""
        seen = {(r.type, r.rpo_seconds): r for r in [*self.forward, *self.backward]}
        return sorted(seen.values(), key=_repl_key)


def _repl_key(r: Repl) -> tuple[int, str]:
    return (r.rpo_seconds if r.rpo_seconds is not None else 10**12, r.type)


@dataclass
class ConceptualModel:
    sites: list[Site] = field(default_factory=list)
    remotes: list[Remote] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    title: str = "Solution conceptual overview"

    # ─ helpers ─
    def site(self, site_id: str) -> Site | None:
        return next((s for s in self.sites if s.id == site_id), None)

    def remote(self, remote_id: str) -> Remote | None:
        return next((r for r in self.remotes if r.id == remote_id), None)

    def name_of(self, ident: str) -> str:
        s = self.site(ident)
        if s:
            return s.name
        r = self.remote(ident)
        return r.name if r else ident

    def links_between(self, x: str, y: str) -> list[Link]:
        return [lk for lk in self.links if {lk.a, lk.b} == {x, y}]


# ─── documents ─────────────────────────────────────────────────────────


def to_doc(m: ConceptualModel) -> dict[str, Any]:
    return asdict(m)


def from_doc(d: dict[str, Any]) -> ConceptualModel:
    def card(c: dict[str, Any]) -> Card:
        return Card(**{**c, "external": [ExternalArray(**x) for x in c.get("external", [])]})

    def site(s: dict[str, Any]) -> Site:
        pc = s.get("pc")
        return Site(id=s["id"], name=s["name"], kind=s.get("kind", "pc"),
                    pc=PcBlock(**{**pc, "services": [Service(**v) for v in pc["services"]]})
                    if pc else None,
                    cards=[card(c) for c in s.get("cards", [])], data=dict(s.get("data", {})),
                    note=s.get("note"))

    return ConceptualModel(
        sites=[site(s) for s in d.get("sites", [])],
        remotes=[Remote(**r) for r in d.get("remotes", [])],
        links=[Link(**{**lk, "forward": [Repl(**r) for r in lk.get("forward", [])],
                       "backward": [Repl(**r) for r in lk.get("backward", [])]})
               for lk in d.get("links", [])],
        title=d.get("title", "Solution conceptual overview"),
    )


def envelope(type_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"schema": MODEL_SCHEMA, "type": type_id, "payload": payload}


def open_envelope(doc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(type id, payload) of a model envelope; raises on a foreign schema."""
    if doc.get("schema") != MODEL_SCHEMA:
        raise ValueError(f"Unsupported diagram model schema: {doc.get('schema')!r}")
    type_id = doc.get("type")
    if not isinstance(type_id, str) or not isinstance(doc.get("payload"), dict):
        raise ValueError("A diagram model needs a type and a payload")
    return type_id, doc["payload"]


def model_json(m: ConceptualModel) -> bytes:
    return dumps(envelope("conceptual", to_doc(m)))


def model_from_json(data: bytes) -> ConceptualModel:
    import json

    type_id, payload = open_envelope(json.loads(data))
    if type_id != "conceptual":
        raise ValueError(f"Not a conceptual diagram model: {type_id!r}")
    return from_doc(payload)


def model_hash(m: ConceptualModel) -> str:
    """sha256 of the canonical model document (the editor's stale check)."""
    return hashlib.sha256(model_json(m)).hexdigest()
