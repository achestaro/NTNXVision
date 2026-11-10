"""What the Diagrams editor may change in the platform service designs (S1 to S4).

Every element can be renamed, reordered within its container and removed; the generic
overlays (connectors, placed icons, text edits) apply as on every diagram. A collected
element stays in the container the data puts it in. Removing a database also removes the
clone line to it (the clone stays, without its line).
"""

from __future__ import annotations

from typing import Any

from .edits import Collection, EditSpec


def _normalize(doc: dict[str, Any]) -> None:
    anchors = {a.get("id") for a in doc.get("anchors", [])}
    boxes = set()
    for c in doc.get("clusters", []):
        if c.get("anchor") not in anchors:
            c["anchor"] = None
        for b in c.get("boxes", []):
            boxes.add(b.get("id"))
            for lane in b.get("lanes", []):
                ids = {i.get("id") for i in lane.get("items", [])}
                for i in lane.get("items", []):
                    if i.get("of") and i["of"] not in ids:
                        i["of"] = None
    doc["links"] = [lk for lk in doc.get("links", [])
                    if lk.get("a") in boxes and lk.get("b") in boxes]


def _source(e: dict[str, Any]) -> str:
    data = e.get("data") or {}
    for key, what in (("cluster_uuid", "Cluster"), ("prism_central", "Prism Central"),
                      ("kubernetes_cluster", "Kubernetes cluster"),
                      ("object_store", "Object store"), ("file_server", "File server"),
                      ("database", "Database"), ("bucket", "Bucket"), ("share", "Share"),
                      ("engine", "Database engine")):
        if data.get(key):
            return f"{what} {data[key]}, from the snapshot."
    return "Generated from the snapshot."


def spec(plane: str, box_kind: str, item_kind: str) -> EditSpec:
    return EditSpec(
        collections=(
            Collection("anchors", plane, sublabel="sub", delete=False),
            Collection("clusters", "Cluster"),
            Collection("boxes", box_kind, sublabel="sub"),
            Collection("lanes", "Group", label="title", delete=False),
            Collection("items", item_kind, sublabel="sub"),
        ),
        normalize=_normalize,
        source=_source,
    )


NKP = spec("Prism Central", "NKP cluster", "Node or endpoint")
NDB = spec("NDB appliance", "Database engine", "Database")
OBJECTS = spec("Prism Central", "Object store", "Bucket or endpoint")
FILES = spec("Prism Central", "File server", "Share or FSVM")

__all__ = ["FILES", "NDB", "NKP", "OBJECTS"]
