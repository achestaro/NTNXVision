"""The diagram model document: what a diagram shows and in which order, no coordinates.

`facts.py` builds it from an Environment (sorted, stable ids); a layout turns it into
scenes. It is also the document the Diagrams editor (P4) edits structurally: rename an
element, reorder cards within a site, add a user component, delete one, and then the
same layout runs again. Values are kept raw (counts, seconds, bytes) and formatted by the
layout, so an edited model lays out exactly like a generated one.

Optional data that later collectors add (external storage arrays, PC size) is optional
here too: present when collected, absent otherwise, never a placeholder.

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
    product: str  # display name mapped from the API enum ("Pure FlashArray", ...)
    name: str | None = None
    containers: int = 0


@dataclass
class Card:
    id: str
    name: str
    #: "cluster" (collected) or "user" (added in the Diagrams editor).
    kind: str = "cluster"
    hypervisor: str | None = None  # "AHV" / "ESXi" / "AHV, ESXi"
    nodes: int | None = None
    rf: int | None = None
    #: Block models, most common first.
    models: list[str] = field(default_factory=list)
    aos: str | None = None
    raw_bytes: int | None = None
    containers: int = 0
    #: Data services and management integrations present (Files, Objects, NDB, vCenter).
    services: list[str] = field(default_factory=list)
    hosts_pc: bool = False
    external: list[ExternalArray] = field(default_factory=list)
    note: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class Service:
    label: str  # "DR", "Flow", "VPCs", "X-Play"
    count: int


@dataclass
class PcBlock:
    id: str
    version: str | None = None
    vms: int | None = None  # 1 = single VM, 3 = scale-out
    vcpus: int | None = None  # total across PC VMs
    mem_gib: int | None = None  # total across PC VMs
    size: str | None = None  # "Small" / "Large" when collected
    services: list[Service] = field(default_factory=list)


@dataclass
class Site:
    """One availability zone: a Prism Central and its clusters (or, in a direct Prism
    Element run, the Prism Element and its cluster)."""

    id: str
    name: str
    kind: str = "pc"  # pc | pe
    pc: PcBlock | None = None
    cards: list[Card] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)


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
    type: str  # Sync | NearSync | Async | Metro
    rpo_seconds: int | None = None


@dataclass
class Link:
    id: str
    kind: str  # pairing | replication
    a: str  # site id (the source of a one-way replication)
    b: str  # site or remote id
    both: bool = False  # replication in both directions
    repl: list[Repl] = field(default_factory=list)
    policies: int = 0


@dataclass
class ConceptualModel:
    sites: list[Site] = field(default_factory=list)
    remotes: list[Remote] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    title: str = "Solution conceptual overview"
    type: str = "conceptual"
    schema: str = MODEL_SCHEMA

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
    if d.get("schema") != MODEL_SCHEMA:
        raise ValueError(f"Unsupported diagram model schema: {d.get('schema')!r}")
    if d.get("type") != "conceptual":
        raise ValueError(f"Not a conceptual diagram model: {d.get('type')!r}")

    def card(c: dict[str, Any]) -> Card:
        return Card(**{**c, "external": [ExternalArray(**x) for x in c.get("external", [])]})

    def site(s: dict[str, Any]) -> Site:
        pc = s.get("pc")
        return Site(id=s["id"], name=s["name"], kind=s.get("kind", "pc"),
                    pc=PcBlock(**{**pc, "services": [Service(**v) for v in pc["services"]]})
                    if pc else None,
                    cards=[card(c) for c in s.get("cards", [])], data=dict(s.get("data", {})))

    return ConceptualModel(
        sites=[site(s) for s in d.get("sites", [])],
        remotes=[Remote(**r) for r in d.get("remotes", [])],
        links=[Link(**{**lk, "repl": [Repl(**r) for r in lk.get("repl", [])]})
               for lk in d.get("links", [])],
        title=d.get("title", "Solution conceptual overview"),
    )


def model_json(m: ConceptualModel) -> bytes:
    return dumps(to_doc(m))


def model_hash(m: ConceptualModel) -> str:
    """sha256 of the canonical model document (the editor's stale check)."""
    return hashlib.sha256(model_json(m)).hexdigest()
