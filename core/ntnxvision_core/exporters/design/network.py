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
    continued_notes,
    fit_layout,
    make_table,
    scene_figures,
)


def build(b: Any, ch: Chapter, dtype: DiagramType) -> bool:
    model = dtype.build_model(b.env)
    if not model.profiles:
        return False
    # The figures draw the saved edit (Diagrams page); summary and table the data.
    drawn = b.figure_model(dtype, model)
    note = b.edit_disclosure(dtype, model, drawn)
    bullets = [*N.bullets(model), *([note] if note else [])]
    scenes = fit_layout(lambda budget: dtype.layout(drawn, dtype.page, None, budget), bullets)
    if not scenes:
        return False
    figs = scene_figures(scenes)
    figs[0].summary = bullets
    # The summary covers every profile; a profile's caption says "(edited)" only when
    # an edit touches that profile.
    b.scope_edit_marks(dtype, model, drawn, figs, lambda sc: {sc.meta.get("group")})
    headers, rows = N.table(model)
    table = make_table(headers, rows, "Host network profiles")
    ch.add(dtype.section, *figs, table)

    def finalize() -> None:
        if not figs[0].label:  # the chapter is left out of this report
            return
        if table is None:
            return
        ref = table.label.rstrip(".")
        shown = {sc.meta.get("profile") for sc in scenes}
        rest = [p.letter for p in model.profiles if p.letter not in shown]
        if rest:
            others = (f"Profile {rest[0]} is" if len(rest) == 1
                      else f"Profiles {rest[0]} to {rest[-1]} are")
            figs[-1].note = f"{others} listed in {ref}."
        # Parts of one profile chain; separate profiles do not.
        by_profile: dict[Any, list[SceneFigure]] = {}
        for f in figs:
            by_profile.setdefault(f.scene.meta.get("profile"), []).append(f)
        for group in by_profile.values():
            continued_notes(group)

    b.finalizers.append(finalize)
    return True
