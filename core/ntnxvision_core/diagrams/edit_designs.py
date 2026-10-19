"""What the Diagrams editor may change in the design figures (A2 to A5).

Each spec names the collections an author may rename, annotate, reorder or remove,
what can be added from the palette, and a `normalize` step that drops what an edit left
dangling (a link or flow whose end was removed, an array whose cluster was removed), so
an edited model always lays out cleanly. Removing an element removes it from the figure
only: summaries and tables are always built from the generated model.

* Management plane (A3): Prism Centrals (rename, reorder, remove), registered
  clusters (rename, note, reorder, remove), remote zones and AZ pairings (remove); the
  author may add a component or a note to a Prism Central's column.
* Logical cluster design (A2): clusters (rename, note, reorder, remove), user
  containers and external arrays (remove); a component or a note may be added to a POD.
* Disaster recovery design (A5): zones, clusters, remote zones, replication flows and
  recovery plan chips; a component or a note may be added to a zone.
* Host network configuration (A4): virtual switches (rename, reorder, remove), traffic
  rows (rename, remove) and LLDP neighbours (remove). Nothing is added: a host network
  profile draws only what the hosts report.
"""

from __future__ import annotations

from typing import Any

from .edits import Collection, EditSpec, PaletteItem

NOT_COLLECTED = "Added by the author, not collected"


def _kind(c: dict[str, Any]) -> str:
    return {"cluster": "Cluster", "note": "Note"}.get(str(c.get("kind", "cluster")),
                                                      "Component")


def _palette(parent: str, where: str) -> tuple[PaletteItem, ...]:
    return (
        PaletteItem("cluster", "Cluster", "clusters",
                    {"name": "New cluster", "kind": "user", "note": NOT_COLLECTED},
                    parents=(parent,), hint=f"A cluster that is not in this snapshot, in {where}."),
        PaletteItem("component", "Component", "clusters",
                    {"name": "New component", "kind": "user",
                     "note": "Not collected"},
                    parents=(parent,), hint="An appliance, array or switch the APIs do not "
                                            "report."),
        PaletteItem("note", "Note", "clusters", {"name": "Note", "kind": "note"},
                    parents=(parent,), hint=f"Free text in {where}."),
    )


def _source(e: dict[str, Any]) -> str:
    if e.get("kind") in ("user", "note") or (e.get("data") or {}).get("origin") == "user":
        return "Added by the author in the Diagrams editor."
    data = e.get("data") or {}
    if data.get("cluster_uuid"):
        pc = data.get("prism_central")
        return f"Cluster {data['cluster_uuid']}" + (f" on {pc}" if pc else "") + \
            ", from the snapshot."
    if data.get("prism_central"):
        return f"Prism Central {data['prism_central']}, from the snapshot."
    return "Generated from the snapshot."


def _ends(lk: dict[str, Any]) -> str:
    def name(ident: Any) -> str:
        text = str(ident or "")
        return text.split(":", 1)[1] if ":" in text else text
    return f"{name(lk.get('a'))} and {name(lk.get('b'))}"


# ─── management plane ──────────────────────────────────────────────────


def _normalize_management(doc: dict[str, Any]) -> None:
    ends = {s.get("id") for s in doc.get("sites", [])} | \
        {r.get("id") for r in doc.get("remotes", [])}
    doc["links"] = [lk for lk in doc.get("links", [])
                    if lk.get("a") in ends and lk.get("b") in ends]


MANAGEMENT = EditSpec(
    collections=(
        Collection("sites", "Prism Central"),
        Collection("clusters", "Cluster", sublabel="note", kind_of=_kind),
        Collection("remotes", "Remote availability zone"),
        Collection("links", "AZ pairing", label=None, reorder=False,
                   title_of=lambda lk: f"AZ pairing: {_ends(lk)}"),
    ),
    palette=_palette("sites", "a Prism Central's column"),
    normalize=_normalize_management,
    source=_source,
)


# ─── disaster recovery design ──────────────────────────────────────────


def _normalize_dr(doc: dict[str, Any]) -> None:
    zones = {s.get("id") for s in doc.get("sites", [])} | \
        {r.get("id") for r in doc.get("remotes", [])}
    clusters = {c.get("id") for s in doc.get("sites", []) for c in s.get("clusters", [])}
    ends = zones | clusters
    doc["flows"] = [f for f in doc.get("flows", [])
                    if f.get("a") in ends and f.get("b") in ends
                    and f.get("site_a") in zones and f.get("site_b") in zones]
    doc["plans"] = [p for p in doc.get("plans", [])
                    if p.get("a") in zones and p.get("b") in zones]


def _flow_title(f: dict[str, Any]) -> str:
    return f"Replication: {_ends(f)}"


DR = EditSpec(
    collections=(
        Collection("sites", "Availability zone"),
        Collection("clusters", "Cluster", sublabel="note", kind_of=_kind),
        Collection("remotes", "Remote availability zone"),
        Collection("flows", "Replication", label=None, reorder=False, title_of=_flow_title),
        Collection("plans", "Recovery plans", label=None, reorder=False,
                   title_of=lambda p: f"Recovery plans: {_ends(p)}"),
    ),
    palette=_palette("sites", "a zone"),
    normalize=_normalize_dr,
    source=_source,
)


# ─── logical cluster design ────────────────────────────────────────────


def _normalize_logical(doc: dict[str, Any]) -> None:
    for s in doc.get("sites", []):
        clusters = [c for p in s.get("pods", []) for c in p.get("clusters", [])]
        ids = {c.get("id") for c in clusters}
        # An array is drawn under the cluster it serves: it goes with that cluster. A
        # container keeps its array id either way (a container on an array with no box
        # is still external storage).
        s["arrays"] = [a for a in s.get("arrays", []) if a.get("cluster") in ids]


LOGICAL = EditSpec(
    collections=(
        Collection("sites", "Availability zone", reorder=False, delete=False),
        Collection("pods", "POD", label=None, reorder=False, delete=False,
                   title_of=lambda p: f"POD {p['name']}" if p.get("name") else "Clusters"),
        Collection("clusters", "Cluster", sublabel="note", kind_of=_kind),
        Collection("containers", "Storage container", label=None, reorder=False,
                   title_of=lambda c: f"Container {c.get('name', '')}"),
        Collection("arrays", "External storage", label=None, reorder=False,
                   title_of=lambda a: str(a.get("name") or a.get("product") or "Array")),
    ),
    palette=_palette("pods", "a POD"),
    normalize=_normalize_logical,
    source=_source,
)


# ─── host network configuration ────────────────────────────────────────


def _normalize_network(doc: dict[str, Any]) -> None:
    for p in doc.get("profiles", []):
        keys = {n.get("key") for n in p.get("neighbours", [])}
        for v in p.get("switches", []):
            for u in v.get("uplinks", []):
                if u.get("switch") not in keys:
                    u["switch"] = None


NETWORK = EditSpec(
    collections=(
        Collection("profiles", "Host network profile", label=None, reorder=False,
                   title_of=lambda p: f"Profile {p.get('letter', '')}"),
        Collection("switches", "Virtual switch"),
        Collection("traffic", "Traffic", label="label", sublabel="sub", reorder=False),
        Collection("neighbours", "LLDP neighbour", label=None, reorder=False,
                   title_of=lambda n: f"Switch {n.get('key', '')}"),
    ),
    normalize=_normalize_network,
    source=_source,
)


__all__ = ["DR", "LOGICAL", "MANAGEMENT", "NETWORK", "NOT_COLLECTED"]
