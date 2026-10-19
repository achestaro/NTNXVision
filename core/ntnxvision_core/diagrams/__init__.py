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

from .check import check_scene
from .facts import conceptual_model
from .layout.conceptual import Page, Refs
from .layout.conceptual import layout as conceptual_layout
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


TYPES: dict[str, DiagramType] = {
    "conceptual": DiagramType(
        id="conceptual", title="Solution conceptual overview", chapter="logical_design",
        section="Solution Conceptual Overview", applies=lambda env: bool(env.clusters),
        build_model=conceptual_model, layout=conceptual_layout, to_doc=conceptual_to_doc,
        from_doc=conceptual_from_doc),
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
    "TYPES", "ConceptualModel", "DiagramType", "Page", "Refs", "Scene", "check_scene",
    "conceptual_layout", "conceptual_model", "conceptual_scenes", "from_doc",
    "model_envelope", "model_from_envelope", "scene_json", "to_doc",
]
