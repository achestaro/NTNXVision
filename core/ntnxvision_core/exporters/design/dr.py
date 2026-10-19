"""Report section: Disaster Recovery Design (A5), in the Data Protection chapter.

The figure (in parts for a large estate) with its design summary on the first part,
then the protection policies table: source and target zone and cluster, type from the
RPO, retention and recovery plans. It replaces the chapter's plain policy table.

The figure draws the author's saved edit (Diagrams page) when there is one; the summary
and the table always describe the collected data.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.diagrams import DiagramType
from ntnxvision_core.diagrams import dr as D
from ntnxvision_core.diagrams.facts import short_name
from ntnxvision_core.exporters.content import (
    Chapter,
    continued_notes,
    fit_layout,
    make_table,
    scene_figures,
)


def build(b: Any, ch: Chapter, dtype: DiagramType) -> bool:
    model = dtype.build_model(b.env)
    if not model.flows:
        return False
    drawn = b.figure_model(dtype, model)
    note = b.edit_disclosure(dtype, model, drawn)
    bullets = [*D.bullets(model), *([note] if note else [])]
    scenes = fit_layout(lambda budget: dtype.layout(drawn, dtype.page, None, budget), bullets)
    if not scenes:
        return False
    figs = scene_figures(scenes)
    figs[0].summary = bullets
    table = make_table(D.POLICY_HEADERS, D.policy_rows(b.env, model), "Protection policies")
    ch.add(dtype.section, *figs, table)
    b.design_figures[dtype.id] = figs
    b.finalizers.append(lambda: continued_notes(figs, lambda sc: ", ".join(
        short_name(drawn.name_of(sid)) for sid in sc.meta.get("sites", []))))
    return True
