"""Registry entries of the network and security figures, drawn per availability zone:
Network Segment Design (N1 `segments`), Flow Virtual Networking Design (N2 `vpc`) and
Microsegmentation Design (N3 `flow`). Kept in their own module so `diagrams.TYPES`
gains them with one line."""

from __future__ import annotations

from . import DiagramType
from . import flow as _flow
from . import segments as _segments
from . import vpc as _vpc
from .edit_flow import SPEC as FLOW_EDIT
from .edit_segments import SPEC as SEGMENTS_EDIT
from .edit_vpc import SPEC as VPC_EDIT
from .layout.flow import layout as flow_layout
from .layout.segments import layout as segments_layout
from .layout.vpc import layout as vpc_layout

TYPES: dict[str, DiagramType] = {
    "segments": DiagramType(
        id="segments", title="Network segment design", chapter="networking",
        section="Network Segment Design", applies=_segments.applies,
        build_model=_segments.segments_model, layout=segments_layout,
        to_doc=_segments.to_doc, from_doc=_segments.from_doc, edit=SEGMENTS_EDIT,
        slug="network-segments"),
    "vpc": DiagramType(
        id="vpc", title="Flow virtual networking design", chapter="networking",
        section="Virtual Networking Design", applies=_vpc.applies,
        build_model=_vpc.vpc_model, layout=vpc_layout, to_doc=_vpc.to_doc,
        from_doc=_vpc.from_doc, edit=VPC_EDIT, slug="virtual-networking"),
    "flow": DiagramType(
        id="flow", title="Microsegmentation design", chapter="security",
        section="Microsegmentation Design", applies=_flow.applies,
        build_model=_flow.flow_model, layout=flow_layout, to_doc=_flow.to_doc,
        from_doc=_flow.from_doc, edit=FLOW_EDIT, slug="microsegmentation"),
}
