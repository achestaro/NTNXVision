"""What the Diagrams editor may change in a Flow Virtual Networking Design (N2) model.

* External subnets (`externals`): a note, reorder; the author may add an external
  network (an upstream router or transit the APIs do not report).
* VPCs (`vpcs`): rename, a note, reorder, remove; the author may add a note card.
* Overlay subnets (`overlays`): rename, a note, reorder within the VPC, remove; the
  author may add a planned overlay subnet.

A VPC whose external subnets were all removed draws without a connector and reads
"Not connected to an external subnet". Tables always list the collected data.
"""

from __future__ import annotations

from typing import Any

from .edits import MAX_TEXT, Collection, EditSpec, PaletteItem


def _normalize(doc: dict[str, Any]) -> None:
    for site in doc.get("sites", []):
        ids = {e.get("id") for e in site.get("externals", [])}
        for e in site.get("externals", []):
            e["note"] = e["note"][:MAX_TEXT] if isinstance(e.get("note"), str) else e.get("note")
        for v in site.get("vpcs", []):
            v["externals"] = [x for x in v.get("externals") or [] if x in ids]


def _source(e: dict[str, Any]) -> str:
    data = e.get("data") or {}
    pc = data.get("prism_central")
    where = f" on Prism Central {pc}" if pc else ""
    if data.get("vpc_uuid"):
        return f"VPC {data['vpc_uuid']}{where}, from the snapshot."
    if data.get("subnet_uuid") and data.get("vpc"):
        return f"Overlay subnet {data['subnet_uuid']} of VPC {data['vpc']}, from the snapshot."
    if e.get("id", "").startswith("ext:"):
        found = " (subnet collected)" if data.get("subnet_uuid") else \
            " (named by its VPCs; the subnet itself was not collected)"
        return f"External subnet {data.get('name', '')}{where}{found}."
    if data.get("name"):
        return f"Availability zone {data['name']}, from the snapshot."
    return "Generated from the snapshot."


SPEC = EditSpec(
    collections=(
        Collection("sites", "Availability zone", label=None, reorder=False, delete=False,
                   title_of=lambda s: str(s.get("name", ""))),
        Collection("externals", "External subnet", label=None, sublabel="note",
                   delete=False, title_of=lambda e: str(e.get("name", "")),
                   kind_of=lambda e: "External network" if e.get("kind") == "user"
                   else "External subnet"),
        Collection("vpcs", "VPC", sublabel="note",
                   kind_of=lambda v: "Note" if v.get("kind") == "note" else "VPC"),
        Collection("overlays", "Overlay subnet", sublabel="note"),
    ),
    palette=(
        PaletteItem("overlay", "Overlay subnet", "overlays",
                    {"name": "Planned overlay subnet", "kind": "user"},
                    parents=("vpcs",), hint="An overlay subnet that is not collected."),
        PaletteItem("external", "External network", "externals",
                    {"name": "Upstream network", "kind": "user", "fips": [],
                     "note": "Not reported by the Nutanix APIs"},
                    parents=("sites",), hint="An upstream router or transit network."),
        PaletteItem("note", "Note", "vpcs", {"name": "Note", "kind": "note", "overlays": [],
                                             "externals": []},
                    parents=("sites",), hint="Free text in an availability zone."),
    ),
    normalize=_normalize,
    source=_source,
)
