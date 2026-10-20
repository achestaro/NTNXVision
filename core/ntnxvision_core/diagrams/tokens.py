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

# ─── host network (A4) ─────────────────────────────────────────────────
#: Virtual switch bar, uplink bond, physical NIC, traffic (what uses a switch).
NET_SWITCH = Style(fill=IRIS_TINT, stroke=IRIS, stroke_w=0.75, radius=2.25)
NET_BOND = Style(fill=WHITE, stroke=INK_2, stroke_w=0.68, radius=2.25)
NET_NIC = Style(fill=WHITE, stroke=INK, stroke_w=0.75, radius=1.5)
NET_TRAFFIC = Style(fill=WHITE, stroke=RULE, stroke_w=0.68, radius=2.25)
NET_MTU = Style(fill=WHITE, stroke=INK_2, stroke_w=0.6, radius=1.5)
NET_LINK = Style(stroke=RULE_STRONG, stroke_w=0.68)
#: "LLDP NEIGHBOUR" chip; the dashed "not reported by Nutanix APIs" note.
TAG_NEIGHBOUR = Style(fill=INK_2, radius=1.9)
NOT_REPORTED = Style(fill=WHITE, stroke=RULE_STRONG, stroke_w=0.75, dash=(2.6, 1.9),
                     radius=3.75)

# ─── disaster recovery design (A5) ─────────────────────────────────────
#: A cluster no protection policy names (greyed).
CLUSTER_MUTED = Style(fill=SURFACE, stroke=RULE, stroke_w=0.7, radius=3.4)
#: "2 RECOVERY PLANS" chip on the gutter between two zones.
TAG_PLANS = Style(fill=WHITE, stroke=RULE_STRONG, stroke_w=0.6, radius=1.9)

# ─── management plane (A3) ─────────────────────────────────────────────
#: Status green ("connected").
GOOD = _h(T.OK)
#: "PCVM 1 of 3" chips inside the Prism Central block (a lighter iris).
PCVM_CHIP = Style(fill="#7A63E3", radius=1.5)
#: The vCenter Server box under an ESXi cluster.
VCENTER = Style(fill=WHITE, stroke=INK_2, stroke_w=0.6, radius=2.25)
#: The AZ pairing glyph between two Prism Centrals (a circle with two arrows).
PAIR_GLYPH = Style(stroke=IRIS, stroke_w=0.8, radius=7.5)
#: Version strip at the foot of a management-plane column ("LCM 3.1.2 · NCC 5.1.1").
FOOT_STRIP = Style(fill=WHITE, stroke=RULE_2, stroke_w=0.6, radius=1.5)

# ─── network segments (N1), virtual networking (N2), microsegmentation (N3) ────
#: A VLAN subnet or port group row, its VLAN chip and the IPAM pills.
SEGMENT = Style(fill=WHITE, stroke=RULE, stroke_w=0.6, radius=1.9)
VLAN_CHIP = Style(fill=INK_2, radius=1.5)
IPAM_PILL = Style(fill=IRIS_TINT, radius=1.5)
NOIPAM_PILL = Style(fill=WHITE, stroke=RULE, stroke_w=0.45, radius=1.5)
#: The vSphere port groups bar of an ESXi cluster (switch not reported).
PORTGROUP_BAR = Style(fill=SURFACE, stroke=INK_3, stroke_w=0.6, radius=2.25)
VPC = Style(fill=IRIS_TINT, stroke=IRIS, stroke_w=0.8, radius=3.0)
EXTERNAL_NET = Style(fill=WHITE, stroke=INK_2, stroke_w=0.75, radius=2.25)
OVERLAY = Style(fill=WHITE, stroke=IRIS, stroke_w=0.5, radius=1.9)
UPLINK = Style(stroke=INK_2, stroke_w=0.8)
NAT_LINK = Style(stroke=INK_2, stroke_w=0.8, dash=(2.0, 1.6))
LANE = Style(fill=WHITE, stroke=RULE_2, stroke_w=0.6, radius=3.0)
POLICY = Style(fill=WHITE, stroke=IRIS, stroke_w=0.7, radius=3.0)
POLICY_MONITOR = Style(fill=WHITE, stroke=IRIS, stroke_w=0.7, dash=(2.6, 1.9), radius=3.0)
POLICY_SAVED = Style(fill=SURFACE, stroke=RULE_STRONG, stroke_w=0.6, dash=(1.2, 1.5),
                     radius=3.0)
GROUP = Style(fill=IRIS_TINT, stroke=IRIS, stroke_w=0.45, radius=4.0)
STATE_ENFORCE = Style(fill=INK, radius=1.9)
STATE_MONITOR = Style(fill=WHITE, stroke=INK_2, stroke_w=0.6, radius=1.9)
RULE_IN = Style(stroke=IRIS, stroke_w=0.9)
ISOLATE = Style(stroke=INK, stroke_w=1.1)
