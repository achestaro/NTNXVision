"""Architecture diagrams generated from a collected snapshot.

Pipeline (each step deterministic, each document versioned)::

    Environment --facts--> model document --layout--> scene(s) --render--> SVG / PNG / PDF

* `facts`: Environment -> model (`model.py`), sorted, ids from object identity. The
  model is what the Diagrams editor edits; an edited model lays out like a generated one.
* `layout`: model -> `Scene` list (parts), text measured with the bundled Figtree.
* `render`: scene -> SVG (live text), PNG (Pillow), PDF (native fpdf2 drawing).

Every diagram type registers in `TYPES` with what the reports and the editor need: when
it applies, where it goes in the report, how its model is built, serialised and laid out.
Models travel in one envelope (`model.envelope`), so a store handles every type alike.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import estate as _estate
from . import network as _network
from .check import check_scene
from .edit_conceptual import SPEC as CONCEPTUAL_EDIT
from .edits import GENERIC, EditSpec
from .facts import conceptual_model
from .layout.conceptual import Page, Refs
from .layout.conceptual import layout as conceptual_layout
from .layout.estate import layout as estate_layout
from .layout.logical import layout as logical_layout
from .layout.network import layout as network_layout
from .logical import from_doc as logical_from_doc
from .logical import logical_model
from .logical import to_doc as logical_to_doc
from .model import ConceptualModel, envelope, open_envelope
from .model import from_doc as conceptual_from_doc
from .model import to_doc as conceptual_to_doc
from .scene import Scene, from_doc, scene_json, to_doc


@dataclass(frozen=True)
class DiagramType:
    id: str
    title: str
    #: Report placement: the chapter key (`content._ORDER`) and the section heading.
    chapter: str
    section: str
    #: Whether the snapshot has what this diagram draws.
    applies: Callable[[Any], bool]
    build_model: Callable[[Any], Any]
    #: (model, page, refs, first_budget) -> scenes
    layout: Callable[..., list[Scene]]
    to_doc: Callable[[Any], dict[str, Any]]
    from_doc: Callable[[dict[str, Any]], Any]
    #: The page the type is laid out for.
    page: Page = Page()
    #: What the Diagrams editor may change (None: rename, reorder and delete by id).
    edit: EditSpec | None = None
    #: Drawn inside another type's section (the estate map opens the conceptual
    #: overview), so `content.design_sections` does not give it a section of its own.
    embedded: bool = False
    #: File-name stem in the editable diagrams bundle ("logical-design").
    slug: str = ""

    @property
    def edit_spec(self) -> EditSpec:
        return self.edit or GENERIC


TYPES: dict[str, DiagramType] = {
    "conceptual": DiagramType(
        id="conceptual", title="Solution conceptual overview", chapter="logical_design",
        section="Solution Conceptual Overview", applies=lambda env: bool(env.clusters),
        build_model=conceptual_model, layout=conceptual_layout, to_doc=conceptual_to_doc,
        from_doc=conceptual_from_doc, edit=CONCEPTUAL_EDIT, slug="solution-conceptual-overview"),
    "estate": DiagramType(
        id="estate", title="Estate map", chapter="logical_design",
        section="Solution Conceptual Overview", applies=_estate.applies,
        build_model=_estate.estate_model, layout=estate_layout, to_doc=_estate.to_doc,
        from_doc=_estate.from_doc, embedded=True, slug="estate-map"),
    "logical": DiagramType(
        id="logical", title="Logical cluster design", chapter="logical_design",
        section="Logical Cluster Design", applies=lambda env: bool(env.clusters and env.hosts),
        build_model=logical_model, layout=logical_layout, to_doc=logical_to_doc,
        from_doc=logical_from_doc, slug="logical-design"),
    "network": DiagramType(
        id="network", title="Host network configuration", chapter="physical_design",
        section="Host Network Configuration", applies=_network.applies,
        build_model=_network.network_model, layout=network_layout,
        to_doc=_network.to_doc, from_doc=_network.from_doc, slug="host-network-profile"),
}


def model_envelope(type_id: str, model: Any) -> dict[str, Any]:
    """A model of any registered type in the common envelope."""
    return envelope(type_id, TYPES[type_id].to_doc(model))


def model_from_envelope(doc: dict[str, Any]) -> tuple[str, Any]:
    """(type id, model) from an envelope; unknown types raise ValueError."""
    type_id, payload = open_envelope(doc)
    if type_id not in TYPES:
        raise ValueError(f"Unknown diagram type: {type_id!r}")
    return type_id, TYPES[type_id].from_doc(payload)


def conceptual_scenes(env: Any, page: Page | None = None, refs: Refs | None = None
                      ) -> tuple[ConceptualModel, list[Scene]]:
    model = conceptual_model(env)
    return model, conceptual_layout(model, page, refs)


__all__ = [
    "TYPES", "ConceptualModel", "DiagramType", "EditSpec", "Page", "Refs", "Scene", "check_scene",
    "conceptual_layout", "conceptual_model", "conceptual_scenes", "from_doc",
    "model_envelope", "model_from_envelope", "scene_json", "to_doc",
]
