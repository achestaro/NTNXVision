"""What the Diagrams editor may change in a Solution Conceptual Overview model.

* Availability zones (`sites`): rename, add a note line, reorder, remove; the author may
  add a zone of their own (drawn dashed amber with a USER-ADDED chip).
* Cluster cards (`cards`): rename, add a note line, set the role chip, reorder within
  the zone, remove; the author may add a component (cluster, storage array, switch) or a
  note to a zone.
* Remote zones (`remotes`): rename, reorder, remove. Links (`links`): remove.

Removing an element removes it from this figure only: tables are always built from the
generated model, so they keep listing it. Links that lose an end are dropped.
"""

from __future__ import annotations

from typing import Any

from .edits import Collection, EditSpec, PaletteItem

ROLES = (("workload", "Workload cluster"), ("hosts_pc", "Hosts Prism Central"),
         ("mgmt", "Management cluster"), ("dr_target", "DR target"))

_NOT_COLLECTED = "Added by the author, not collected"


def _card_kind(c: dict[str, Any]) -> str:
    return {"cluster": "Cluster", "note": "Note"}.get(str(c.get("kind")), "Component")


def _site_kind(s: dict[str, Any]) -> str:
    return "Prism Element" if s.get("kind") == "pe" else "Availability zone"


def _end(ident: Any) -> str:
    text = str(ident or "")
    return text.split(":", 1)[1] if text.startswith("site:") else text


def _link_title(lk: dict[str, Any]) -> str:
    what = "AZ pairing" if lk.get("kind") == "pairing" else "Replication"
    return f"{what}: {_end(lk.get('a'))} and {_end(lk.get('b'))}"


def _normalize(doc: dict[str, Any]) -> None:
    ends = {s.get("id") for s in doc.get("sites", [])} | \
        {r.get("id") for r in doc.get("remotes", [])}
    doc["links"] = [lk for lk in doc.get("links", [])
                    if lk.get("a") in ends and lk.get("b") in ends]


def _source(e: dict[str, Any]) -> str:
    data = e.get("data") or {}
    if data.get("cluster_uuid"):
        pc = data.get("prism_central")
        where = f" on Prism Central {pc}" if pc else ""
        return f"Cluster {data['cluster_uuid']}{where}, from the snapshot."
    if data.get("prism_central"):
        return f"Prism Central {data['prism_central']} and its clusters, from the snapshot."
    if data.get("prism_element"):
        return f"Prism Element {data['prism_element']}, from the snapshot."
    if data.get("availability_zone"):
        return (f"Availability zone {data['availability_zone']}, a pairing or replication "
                "target outside this inventory.")
    if e.get("kind") in ("pairing", "replication"):
        return f"{str(e['kind']).capitalize()} between availability zones, from the snapshot."
    return "Generated from the snapshot."


SPEC = EditSpec(
    collections=(
        Collection("sites", "Availability zone", sublabel="note", kind_of=_site_kind),
        Collection("cards", "Cluster", sublabel="note", roles=ROLES, kind_of=_card_kind,
                   has_role=lambda c: c.get("kind") == "cluster"),
        Collection("remotes", "Remote availability zone"),
        Collection("links", "Link", label=None, reorder=False, title_of=_link_title),
    ),
    palette=(
        PaletteItem("cluster", "Cluster", "cards",
                    {"name": "New cluster", "kind": "user", "note": _NOT_COLLECTED},
                    parents=("sites",), hint="A cluster that is not in this snapshot."),
        PaletteItem("az", "AZ", "sites",
                    {"name": "New availability zone", "kind": "user", "cards": [],
                     "note": _NOT_COLLECTED},
                    hint="An availability zone outside this inventory."),
        PaletteItem("storage", "Storage array", "cards",
                    {"name": "Storage array", "kind": "user",
                     "note": "External storage, not collected"},
                    parents=("sites",), hint="An array the APIs do not report."),
        PaletteItem("switch", "Switch", "cards",
                    {"name": "Network switch", "kind": "user",
                     "note": "Network, not collected"},
                    parents=("sites",), hint="A switch or network device."),
        PaletteItem("note", "Note", "cards", {"name": "Note", "kind": "note"},
                    parents=("sites",), hint="Free text in an availability zone."),
    ),
    normalize=_normalize,
    source=_source,
)
