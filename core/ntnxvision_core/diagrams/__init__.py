"""Architecture diagrams generated from a collected snapshot.

Pipeline (each step deterministic, each document versioned)::

    Environment --facts--> model document --layout--> scene(s) --render--> SVG / PNG / PDF

* `facts`: Environment -> model document (`model.py`), sorted, ids from object identity.
  The model is what the Diagrams editor edits; an edited model lays out like a generated
  one.
* `layout`: model -> `Scene` list (parts), text measured with the bundled Figtree.
* `render`: scene -> SVG (live text), PNG (Pillow), PDF (native fpdf2 drawing).

Diagram types register in `TYPES`; new types (logical, management, network, DR) add a
facts function and a layout, and every renderer draws them unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .check import check_scene
from .facts import conceptual_model
from .layout.conceptual import Page, Refs
from .layout.conceptual import layout as conceptual_layout
from .model import ConceptualModel
from .scene import Scene, from_doc, scene_json, to_doc


@dataclass(frozen=True)
class DiagramType:
    id: str
    title: str
    build_model: Callable[[Any], Any]
    layout: Callable[..., list[Scene]]


TYPES: dict[str, DiagramType] = {
    "conceptual": DiagramType("conceptual", "Solution conceptual overview", conceptual_model,
                              conceptual_layout),
}


def conceptual_scenes(env: Any, page: Page | None = None, refs: Refs | None = None
                      ) -> tuple[ConceptualModel, list[Scene]]:
    model = conceptual_model(env)
    return model, conceptual_layout(model, page, refs)


__all__ = [
    "TYPES", "ConceptualModel", "DiagramType", "Page", "Refs", "Scene", "check_scene",
    "conceptual_layout", "conceptual_model", "conceptual_scenes", "from_doc", "scene_json",
    "to_doc",
]
