"""Diagram style tokens, derived from the report theme (Runway, light).

Only these tokens reach a scene, so the reports, the editable assets and the Diagrams
page draw with one palette. Sizes are points at 1:1 on the page; nothing is smaller
than `text.MIN_SIZE` (6 pt).
"""

from __future__ import annotations

from ntnxvision_core.exporters import theme as T

from .scene import Style


def _h(x: str) -> str:
    return "#" + x.upper()


INK, INK_2, INK_3 = _h(T.INK), _h(T.INK_2), _h(T.INK_3)
RULE, RULE_2 = _h(T.RULE), _h(T.RULE_2)
SURFACE = _h(T.SURFACE)
IRIS, IRIS_TINT = _h(T.IRIS), _h(T.IRIS_TINT)
LIVE = _h(T.LIVE)
LIVE_TINT = "#E7F3F5"
WHITE = "#FFFFFF"
#: Runway rule-strong: dashed "outside this inventory" and the manages arrow.
RULE_STRONG = "#8E99A4"
#: Amber for user-added elements (Diagrams editor).
AMBER = _h(T.WARN)

# ─── type scale (pt) ───────────────────────────────────────────────────
SIZE_SITE = 9.0
SIZE_PC_TITLE = 8.0
SIZE_CARD_TITLE = 7.2
SIZE_REMOTE_TITLE = 7.8
SIZE_BODY = 6.2
SIZE_SMALL = 6.0
SIZE_CHIP = 6.0

# ─── boxes ─────────────────────────────────────────────────────────────
SITE = Style(fill=SURFACE, stroke=RULE, stroke_w=0.75, radius=5.25)
PC = Style(fill=IRIS, radius=3.75)
CLUSTER = Style(fill=WHITE, stroke=IRIS, stroke_w=0.7, radius=3.4)
USER = Style(fill=WHITE, stroke=AMBER, stroke_w=0.75, dash=(1.2, 1.5), radius=3.4)
#: A user-added availability zone (container) and a user note.
USER_SITE = Style(fill=SURFACE, stroke=AMBER, stroke_w=0.75, dash=(1.2, 1.5), radius=5.25)
NOTE = Style(fill="#FFFBEF", stroke=AMBER, stroke_w=0.75, dash=(1.2, 1.5), radius=2.25)
MORE = Style(fill=WHITE, stroke=RULE_STRONG, stroke_w=0.75, dash=(2.6, 1.9), radius=3.4)
REMOTE = Style(fill=WHITE, stroke=RULE_STRONG, stroke_w=0.75, dash=(2.6, 1.9), radius=4.5)
STORAGE = Style(fill=LIVE_TINT, stroke=LIVE, stroke_w=0.45, radius=1.5)
EXTERNAL = Style(fill=LIVE, radius=1.5)
PILL = Style(fill=IRIS_TINT, radius=4.1)
TAG = Style(fill=IRIS, radius=1.9)
TAG_ROLE = Style(fill=INK, radius=1.9)
TAG_MUTED = Style(fill=INK_3, radius=1.9)
TAG_USER = Style(fill=AMBER, radius=1.9)
LABEL = Style()

# ─── edges ─────────────────────────────────────────────────────────────
MANAGES = Style(stroke=RULE_STRONG, stroke_w=0.7)
PAIRING = Style(stroke=IRIS, stroke_w=0.75, dash=(3.0, 2.25))
#: Replication stroke by the strongest type on the link.
REPL_SYNC = Style(stroke=IRIS, stroke_w=1.8)
REPL_NEARSYNC = Style(stroke=IRIS, stroke_w=1.1)
REPL_ASYNC = Style(stroke=IRIS, stroke_w=0.7)

#: Legend swatches (line samples use the edge styles above).
LEGEND_REPL = REPL_NEARSYNC

#: A site of this inventory drawn in another part (a reference box).
REF = Style(fill=WHITE, stroke=IRIS, stroke_w=0.75, dash=(2.6, 1.9), radius=4.5)
#: AZ pairing whose connection is reported down.
PAIRING_DOWN = Style(stroke=RULE_STRONG, stroke_w=0.75, dash=(1.5, 2.0))

# ─── logical cluster design (A2) ───────────────────────────────────────
POD_FILL = "#F3F4F6"
POD = Style(fill=POD_FILL, stroke=RULE, stroke_w=0.7, radius=4.5)
TAG_POD = Style(fill=INK_2, radius=1.9)
#: The NODES box inside a cluster card.
NODES_BOX = Style(fill=SURFACE, stroke=RULE_2, stroke_w=0.6, radius=2.25)
#: "Runs Prism Central" strip.
ROLE_STRIP = Style(fill=IRIS_TINT, radius=1.5)
#: "+N more" container chip.
MORE_CHIP = Style(fill=WHITE, stroke=RULE, stroke_w=0.45, radius=1.5)
#: "EXTERNAL STORAGE" chip on an array box: white with a teal outline (teal text).
TAG_EXTERNAL = Style(fill=WHITE, stroke=LIVE, stroke_w=0.6, radius=1.9)
#: A container to the array it lives on.
ARRAY_LINK = Style(stroke=LIVE, stroke_w=0.9)

# ─── estate map (A6) ───────────────────────────────────────────────────
#: ESXi share of an AZ's nodes (the hypervisor bar; AHV is iris).
ESXI = "#B38C15"
#: A part of the conceptual overview: a dashed frame around its AZ tiles.
FRAME = Style(stroke=RULE_STRONG, stroke_w=0.75, dash=(3.0, 2.5), radius=5.25)

