"""What the Diagrams editor may change in a Network Segment Design (N1) model.

* Cluster cards (`clusters`): rename, a note line, reorder within the AZ, remove; the
  author may add a gateway or firewall card (a device the APIs do not report) or a note.
* Virtual switches (`switches`): a note only, reorder within the card (names are
  collected, never renamed).
* Segments (`segments`): rename, a note, reorder within the switch, remove; the author
  may add a planned segment, whose VLAN and range are free text, never parsed.

Removing an element removes it from this figure only: the segments table is always
built from the collected data. User-added elements are drawn amber and dashed with a
USER-ADDED chip and never reach a table.
"""

from __future__ import annotations

from typing import Any

from .edits import MAX_TEXT, Collection, EditSpec, PaletteItem


def _cluster_kind(c: dict[str, Any]) -> str:
    return {"cluster": "Cluster", "note": "Note"}.get(str(c.get("kind")), "Gateway / firewall")


def _segment_kind(s: dict[str, Any]) -> str:
    return {"portgroup": "Port group", "user": "Planned segment"}.get(str(s.get("kind")),
                                                                       "Segment")


def _clamp(v: Any) -> Any:
    return v[:MAX_TEXT] if isinstance(v, str) else v


def _normalize(doc: dict[str, Any]) -> None:
    for site in doc.get("sites", []):
        for c in site.get("clusters", []):
            names = [n for n in c.get("applies_to") or [] if isinstance(n, str)]
            c["applies_to"] = names if len(names) > 1 else []
            for key in ("name", "note"):
                c[key] = _clamp(c.get(key))
            for w in c.get("switches", []):
                w["note"] = _clamp(w.get("note"))
                for s in w.get("segments", []):
                    for key in ("name", "note", "vlan_text", "prefix_text"):
                        s[key] = _clamp(s.get(key))


def _source(e: dict[str, Any]) -> str:
    data = e.get("data") or {}
    if data.get("subnet_uuid"):
        return (f"Subnet {data['subnet_uuid']} on cluster {data.get('cluster', 'N/A')}, "
                "from the snapshot.")
    if data.get("port_group_uuid"):
        return (f"Port group {data['port_group_uuid']} on cluster {data.get('cluster', 'N/A')}"
                " (Prism Element v2.0), from the snapshot.")
    if data.get("virtual_switch"):
        return f"Virtual switch {data['virtual_switch']}, from the snapshot."
    if data.get("cluster_uuid"):
        return f"Cluster {data['cluster_uuid']} and its subnets, from the snapshot."
    if data.get("name"):
        return f"Availability zone {data['name']}, from the snapshot."
    return "Generated from the snapshot."


SPEC = EditSpec(
    collections=(
        Collection("sites", "Availability zone", label=None, reorder=False, delete=False,
                   title_of=lambda s: str(s.get("name", ""))),
        Collection("clusters", "Cluster", sublabel="note", kind_of=_cluster_kind),
        Collection("switches", "Virtual switch", label=None, sublabel="note", delete=False,
                   title_of=lambda w: str(w.get("name", ""))),
        Collection("segments", "Segment", sublabel="note", kind_of=_segment_kind),
    ),
    palette=(
        PaletteItem("segment", "Segment", "segments",
                    {"name": "Planned segment", "kind": "user", "vlan_text": "VLAN TBD",
                     "prefix_text": "Not collected"},
                    parents=("switches",), hint="A VLAN segment that is not collected."),
        PaletteItem("gateway", "Gateway / firewall", "clusters",
                    {"name": "Gateway / firewall", "kind": "user", "switches": [],
                     "note": "Not reported by the Nutanix APIs"},
                    parents=("sites",), hint="A ToR switch or firewall the APIs do not report."),
        PaletteItem("note", "Note", "clusters", {"name": "Note", "kind": "note",
                                                 "switches": []},
                    parents=("sites",), hint="Free text in an availability zone."),
    ),
    normalize=_normalize,
    source=_source,
)
