"""Report section: Flow Virtual Networking Design (N2), in the Networking chapter.

One section per availability zone: the figure (external subnets, VPCs, overlay subnets,
floating IPs), its design summary, the AZ's VPC table and its overlay subnets table.
VPCs and overlays are scoped by Prism Central, never merged by name across PCs.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.diagrams import vpc as N2
from ntnxvision_core.diagrams.layout.vpc import layout_site
from ntnxvision_core.exporters.content import Chapter, make_table
from ntnxvision_core.exporters.design.peraz import PerAz
from ntnxvision_core.exporters.design.peraz import build as build_per_az


def build(b: Any, ch: Chapter, dtype: Any) -> bool:
    env = b.env

    def tables(site: Any) -> list[Any]:
        def srcs(rows: list[Any]) -> list[str] | None:
            return [site.name] * len(rows) if env.is_combined else None

        vpcs, ovs = N2.vpc_rows(site), N2.overlay_rows(site)
        return [make_table(N2.VPC_HEADERS, vpcs, f"Flow virtual networking VPCs, {site.name}",
                           srcs(vpcs), prune=("Routable prefixes", "DNS", "Floating IPs")),
                make_table(N2.OVERLAY_HEADERS, ovs, f"Overlay subnets, {site.name}",
                           srcs(ovs), prune=("Gateway", "IPAM"))]

    return build_per_az(b, ch, dtype, PerAz(
        sites=lambda m: [s for s in m.sites if s.vpcs], bullets=N2.bullets,
        layout_site=layout_site, tables=tables))
