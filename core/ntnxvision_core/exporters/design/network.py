"""Report section: Host Network Configuration (A4), in the Physical Design chapter.

One figure per drawn host network profile (A to C), the design summary on the first,
then the profiles table that lists every profile and the ESXi clusters whose host
networking the Nutanix APIs do not report.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.diagrams import DiagramType
from ntnxvision_core.diagrams import network as N
from ntnxvision_core.exporters.content import (
    Chapter,
    SceneFigure,
    first_part_budget,
    make_table,
)


def build(b: Any, ch: Chapter, dtype: DiagramType) -> bool:
    model = dtype.build_model(b.env)
    if not model.profiles:
        return False
    bullets = N.bullets(model)
    scenes = dtype.layout(model, dtype.page, None, first_part_budget(bullets))
    figs = [SceneFigure(sc, sc.caption) for sc in scenes]
    figs[0].summary = bullets
    headers, rows = N.table(model)
    table = make_table(headers, rows, "Host network profiles")
    ch.add(dtype.section, *figs, table)

    def finalize() -> None:
        if table is None:
            return
        ref = table.label.rstrip(".")
        drawn = {sc.meta.get("profile") for sc in scenes}
        rest = [p.letter for p in model.profiles if p.letter not in drawn]
        if rest:
            others = (f"Profile {rest[0]} is" if len(rest) == 1
                      else f"Profiles {rest[0]} to {rest[-1]} are")
            figs[-1].note = f"{others} listed in {ref}."
        for f in figs:
            if f.scene.part[1] > 1 and f.scene.part[0] < f.scene.part[1]:
                nxt = figs[figs.index(f) + 1]
                f.note = (f"Continued in {nxt.label.rstrip('.')}. {f.note}").strip()

    b.finalizers.append(finalize)
    return True
