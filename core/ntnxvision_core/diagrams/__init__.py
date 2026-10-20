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

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import connectors as _connectors
from . import dr as _dr
from . import edit_designs as _edits
from . import estate as _estate
from . import management as _management
from . import network as _network
from .check import check_scene
from .edit_conceptual import SPEC as CONCEPTUAL_EDIT
from .edits import CONNECTORS, EDGE_EDITS, GENERIC, EditSpec
from .facts import conceptual_model
from .layout.conceptual import Page, Refs
from .layout.conceptual import layout as conceptual_layout
from .layout.dr import layout as dr_layout
from .layout.estate import layout as estate_layout
from .layout.logical import layout as logical_layout
from .layout.management import layout as management_layout
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

    def __post_init__(self) -> None:
        # Connector edits (Diagrams editor: user connectors, changed generated ones) live
        # beside the type's own model: `from_doc` keeps them on the model object,
        # `to_doc` writes them back, and `layout` draws them over the type's scenes. So
        # every caller (editor, report sections, per-AZ figures, bundle) draws them.
        object.__setattr__(self, "to_doc", _with_extra_doc(self.to_doc))
        object.__setattr__(self, "from_doc", _with_extra_model(self.from_doc))
        object.__setattr__(self, "layout", _with_overlay(self.layout))


#: Attribute holding a model's connector edits (`connectors.Extra`).
_EXTRA = "_ntnx_connectors"


def connector_extra(model: Any) -> _connectors.Extra | None:
    """The connector edits a model carries (None: none)."""
    return getattr(model, _EXTRA, None)


def _with_extra_doc(fn: Callable[[Any], dict[str, Any]]) -> Callable[[Any], dict[str, Any]]:
    if getattr(fn, "_ntnx_wrapped", False):
        return fn

    def to_doc(model: Any) -> dict[str, Any]:
        doc = fn(model)
        extra = connector_extra(model)
        if extra:
            if extra.connectors:
                doc[CONNECTORS] = copy.deepcopy(extra.connectors)
            if extra.edits:
                doc[EDGE_EDITS] = copy.deepcopy(extra.edits)
        return doc

    to_doc._ntnx_wrapped = True  # type: ignore[attr-defined]
    return to_doc


def _with_extra_model(fn: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
    if getattr(fn, "_ntnx_wrapped", False):
        return fn

    def from_doc(doc: dict[str, Any]) -> Any:
        if not any(k in doc for k in (CONNECTORS, EDGE_EDITS)):
            return fn(doc)
        model = fn({k: v for k, v in doc.items() if k not in (CONNECTORS, EDGE_EDITS)})
        extra = _connectors.Extra(copy.deepcopy(list(doc.get(CONNECTORS) or [])),
                                  copy.deepcopy(dict(doc.get(EDGE_EDITS) or {})))
        if extra:
            object.__setattr__(model, _EXTRA, extra)
        return model

    from_doc._ntnx_wrapped = True  # type: ignore[attr-defined]
    return from_doc


def _with_overlay(fn: Callable[..., list[Scene]]) -> Callable[..., list[Scene]]:
    if getattr(fn, "_ntnx_wrapped", False):
        return fn

    def layout(model: Any, *args: Any, **kwargs: Any) -> list[Scene]:
        scenes = fn(model, *args, **kwargs)
        extra = connector_extra(model)
        if extra:
            _connectors.overlay(scenes, extra)
        return scenes

    layout._ntnx_wrapped = True  # type: ignore[attr-defined]
    return layout


TYPES: dict[str, DiagramType] = {
    "conceptual": DiagramType(
        id="conceptual", title="Solution conceptual overview", chapter="logical_design",
        section="Solution Conceptual Overview", applies=lambda env: bool(env.clusters),
        build_model=conceptual_model, layout=conceptual_layout, to_doc=conceptual_to_doc,
        from_doc=conceptual_from_doc, edit=CONCEPTUAL_EDIT,
        slug="solution-conceptual-overview"),
    "estate": DiagramType(
        id="estate", title="Estate map", chapter="logical_design",
        section="Solution Conceptual Overview", applies=_estate.applies,
        build_model=_estate.estate_model, layout=estate_layout, to_doc=_estate.to_doc,
        from_doc=_estate.from_doc, embedded=True, slug="estate-map"),
    "logical": DiagramType(
        id="logical", title="Logical cluster design", chapter="logical_design",
        section="Logical Cluster Design", applies=lambda env: bool(env.clusters and env.hosts),
        build_model=logical_model, layout=logical_layout, to_doc=logical_to_doc,
        from_doc=logical_from_doc, edit=_edits.LOGICAL, slug="logical-design"),
    "management": DiagramType(
        id="management", title="Management plane design", chapter="prism_central",
        section="Management Plane Design",
        applies=lambda env: env.source_type != "prism_element" and bool(env.clusters),
        build_model=_management.management_model, layout=management_layout,
        to_doc=_management.to_doc, from_doc=_management.from_doc, edit=_edits.MANAGEMENT,
        slug="management-plane"),
    "network": DiagramType(
        id="network", title="Host network configuration", chapter="physical_design",
        section="Host Network Configuration", applies=_network.applies,
        build_model=_network.network_model, layout=network_layout,
        to_doc=_network.to_doc, from_doc=_network.from_doc, edit=_edits.NETWORK,
        slug="host-network-profile"),
    "dr": DiagramType(
        id="dr", title="Disaster recovery design", chapter="data_protection",
        section="Disaster Recovery Design", applies=_dr.applies, build_model=_dr.dr_model,
        layout=dr_layout, to_doc=_dr.to_doc, from_doc=_dr.from_doc, edit=_edits.DR,
        slug="data-protection"),
}

# Network and security figures (N1 to N3), registered from their own module.
from .netdesigns import TYPES as _NET_TYPES  # noqa: E402

TYPES.update(_NET_TYPES)


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
