"""Report section: Network Segment Design (N1), in the Networking chapter.

One section per availability zone: the figure (virtual switches, VLAN subnets, port
groups and IPAM per cluster), its design summary, then the AZ's network segments
table, which lists every segment (collapsed plans expanded, "+N more" rows included).
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.diagrams import segments as N1
from ntnxvision_core.diagrams.layout.segments import layout_site
from ntnxvision_core.exporters.content import Chapter, make_table
from ntnxvision_core.exporters.design.peraz import PerAz
from ntnxvision_core.exporters.design.peraz import build as build_per_az


def build(b: Any, ch: Chapter, dtype: Any) -> bool:
    env = b.env

    def tables(site: Any) -> list[Any]:
        rows = N1.table_rows(env, site)
        return [make_table(N1.HEADERS, rows, f"Network segments, {site.name}",
                           [site.name] * len(rows) if env.is_combined else None,
                           prune=("Gateway", "IPAM", "VMs"))]

    return build_per_az(b, ch, dtype, PerAz(
        sites=lambda m: [s for s in m.sites if s.clusters], bullets=N1.bullets,
        layout_site=layout_site, tables=tables))
