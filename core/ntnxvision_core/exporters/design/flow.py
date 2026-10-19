"""Report section: Microsegmentation Design (N3), first in the Security Posture (Flow)
chapter.

One section per availability zone (AZs with identical policy sets share one): the
figure (policies by type, state, secured groups, rule counts and, when collected,
peers), its design summary and the security policies table of those AZs, in the
figure's order.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.diagrams import flow as N3
from ntnxvision_core.exporters.content import Chapter, make_table
from ntnxvision_core.exporters.design.peraz import PerAz
from ntnxvision_core.exporters.design.peraz import build as build_per_az


def build(b: Any, ch: Chapter, dtype: Any) -> bool:
    env = b.env

    def tables(site: Any) -> list[Any]:
        rows, srcs = N3.table_rows(env, site)
        return [make_table(N3.HEADERS, rows,
                           f"Flow network security policies, {N3.site_title(site)}",
                           srcs if env.is_combined else None,
                           prune=("Inbound peers", "Outbound peers"))]

    return build_per_az(b, ch, dtype, PerAz(
        sites=lambda m: [s for s in m.sites if s.lanes], bullets=N3.bullets,
        tables=tables))
