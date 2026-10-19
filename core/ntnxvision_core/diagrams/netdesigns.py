"""Registry entries of the network and security figures, drawn per availability zone:
Network Segment Design (N1 `segments`), Flow Virtual Networking Design (N2 `vpc`) and
Microsegmentation Design (N3 `flow`). Kept in their own module so `diagrams.TYPES`
gains them with one line."""

from __future__ import annotations

from . import DiagramType
from . import segments as _segments
from .edit_segments import SPEC as SEGMENTS_EDIT
from .layout.segments import layout as segments_layout

TYPES: dict[str, DiagramType] = {
    "segments": DiagramType(
        id="segments", title="Network segment design", chapter="networking",
        section="Network Segment Design", applies=_segments.applies,
        build_model=_segments.segments_model, layout=segments_layout,
        to_doc=_segments.to_doc, from_doc=_segments.from_doc, edit=SEGMENTS_EDIT,
        slug="network-segments"),
}
