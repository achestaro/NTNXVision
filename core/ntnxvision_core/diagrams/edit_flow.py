"""What the Diagrams editor may change in a Microsegmentation Design (N3) model.

* Lanes (`lanes`, one per policy type): reorder only.
* Security policies (`policies`): rename, a note, reorder within the lane, remove; the
  author may add a planned policy (drawn amber with a USER-ADDED chip, no state) or a
  note.

Tables always list the collected policies; user-added policies never reach them.
"""

from __future__ import annotations

from typing import Any

from .edits import MAX_TEXT, Collection, EditSpec, PaletteItem
from .flow import LANE_TITLES


def _normalize(doc: dict[str, Any]) -> None:
    for site in doc.get("sites", []):
        for lane in site.get("lanes", []):
            for p in lane.get("policies", []):
                p["groups"] = [g[:MAX_TEXT] for g in p.get("groups") or []
                               if isinstance(g, str)][:8]
                for key in ("name", "note"):
                    if isinstance(p.get(key), str):
                        p[key] = p[key][:MAX_TEXT]


def _kind(p: dict[str, Any]) -> str:
    return {"user": "Planned policy", "note": "Note"}.get(str(p.get("kind")),
                                                          "Security policy")


def _source(e: dict[str, Any]) -> str:
    data = e.get("data") or {}
    if data.get("policy_uuid"):
        return (f"Security policy {data['policy_uuid']} on Prism Central "
                f"{data.get('prism_central', 'N/A')}, from the snapshot.")
    if data.get("name"):
        return f"Availability zone {data['name']}, from the snapshot."
    return "Generated from the snapshot."


SPEC = EditSpec(
    collections=(
        Collection("sites", "Availability zone", label=None, reorder=False, delete=False,
                   title_of=lambda s: str(s.get("name", ""))),
        Collection("lanes", "Policy lane", label=None, delete=False,
                   title_of=lambda x: LANE_TITLES.get(str(x.get("type")), "Policies")
                   .capitalize()),
        Collection("policies", "Security policy", sublabel="note", kind_of=_kind),
    ),
    palette=(
        PaletteItem("policy", "Planned policy", "policies",
                    {"name": "Planned policy", "kind": "user", "groups": ["Category TBD"],
                     "state": None},
                    parents=("lanes",), hint="A policy that is planned, not collected."),
        PaletteItem("note", "Note", "policies", {"name": "Note", "kind": "note"},
                    parents=("lanes",), hint="Free text in a policy lane."),
    ),
    normalize=_normalize,
    source=_source,
)
