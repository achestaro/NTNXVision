"""Estate map (A6): one tile per availability zone, for estates the conceptual overview
draws in parts (decision 7).

The map opens the Solution Conceptual Overview section: each tile summarises one AZ
(Prism Central, clusters and nodes, the hypervisor mix) and names the figure that draws
it in detail; tiles sit in one frame per part, in the parts' order, so paired AZs stay
together; replication to AZs outside the inventory runs to dashed boxes below.

The model is derived from the conceptual model and the parts its layout produced, so the
map always matches the figures it points at.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from . import fmt
from .facts import conceptual_model
from .layout.conceptual import layout as conceptual_layout
from .model import ConceptualModel, Link, Remote, Repl


@dataclass
class Tile:
    id: str  # the site id
    name: str
    pc_version: str | None = None
    deployment: str | None = None  # "Single VM, Small"
    clusters: int = 0
    nodes: int = 0
    #: Nodes per hypervisor, most first: [["AHV", 160], ["ESXi", 20]].
    hyp_nodes: list[list[Any]] = field(default_factory=list)
    #: Clusters per hypervisor, most first.
    hyp_clusters: list[list[Any]] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class EstateModel:
    tiles: list[Tile] = field(default_factory=list)
    #: Tile ids per part, in part order (part i is conceptual figure part i + 1).
    parts: list[list[str]] = field(default_factory=list)
    remotes: list[Remote] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    title: str = "Estate map"


@dataclass(frozen=True)
class EstateRefs:
    """The figure label of each conceptual part ("Figure 5-2"), once numbered."""

    part_labels: tuple[str, ...] = ()


def to_doc(m: EstateModel) -> dict[str, Any]:
    return asdict(m)


def from_doc(d: dict[str, Any]) -> EstateModel:
    return EstateModel(
        tiles=[Tile(**t) for t in d.get("tiles", [])],
        parts=[list(p) for p in d.get("parts", [])],
        remotes=[Remote(**r) for r in d.get("remotes", [])],
        links=[Link(**{**lk, "forward": [Repl(**r) for r in lk.get("forward", [])],
                       "backward": [Repl(**r) for r in lk.get("backward", [])]})
               for lk in d.get("links", [])],
        title=d.get("title", "Estate map"))


def _deployment(site: Any) -> str | None:
    pc = site.pc
    if pc is None:
        return None
    vms = pc.vms or 1
    text = "Single VM" if vms == 1 else f"Scale-out, {vms:,} PCVMs"
    return f"{text}, {pc.size}" if pc.size else text


def _ranked(c: Counter[str]) -> list[list[Any]]:
    return [[k, v] for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]


def estate_from(cm: ConceptualModel, parts: list[list[str]]) -> EstateModel:
    """The estate map of `cm`, drawn in `parts` (site ids per conceptual part)."""
    tiles = []
    for s in cm.sites:
        cards = [c for c in s.cards if c.kind == "cluster"]
        nodes: Counter[str] = Counter()
        clusters: Counter[str] = Counter()
        for c in cards:
            h = c.hypervisor or "N/A"
            nodes[h] += c.nodes or 0
            clusters[h] += 1
        tiles.append(Tile(id=s.id, name=s.name, pc_version=s.pc.version if s.pc else None,
                          deployment=_deployment(s), clusters=len(cards),
                          nodes=sum(c.nodes or 0 for c in cards), hyp_nodes=_ranked(nodes),
                          hyp_clusters=_ranked(clusters), data=dict(s.data)))
    return EstateModel(tiles=tiles, parts=[list(p) for p in parts],
                       remotes=list(cm.remotes), links=list(cm.links))


def estate_model(env: Any) -> EstateModel:
    cm = conceptual_model(env)
    scenes = conceptual_layout(cm)
    return estate_from(cm, [list(sc.meta.get("sites", [])) for sc in scenes])


#: The estate map opens the overview when it is drawn in at least this many parts
#: (with two, the parts themselves are the overview).
MIN_PARTS = 3


def applies(env: Any) -> bool:
    """Only when the conceptual overview needs `MIN_PARTS` parts or more."""
    if not env.clusters or env.source_type == "prism_element":
        return False
    return len(estate_model(env).parts) >= MIN_PARTS


def estate_bullets(m: EstateModel) -> list[str]:
    """The estate map's lead bullet; the conceptual overview's own bullets follow it
    (parts, the "+N more" cards and the clusters table, pairing and replication)."""
    nodes = sum(t.nodes for t in m.tiles)
    clusters = sum(t.clusters for t in m.tiles)
    return [f"{fmt.plural(len(m.tiles), 'availability zone')}, "
            f"{fmt.plural(clusters, 'cluster')} and {fmt.plural(nodes, 'node')}: one tile "
            "per availability zone here, drawn in detail in the parts that follow."]
