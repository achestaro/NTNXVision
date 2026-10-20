"""Logical Cluster Design (A2) in the Logical Design chapter: one section per
availability zone with its figure (in parts when large), its design summary and the
user storage containers table.

The figures draw the author's saved edit (Diagrams page) when there is one; summaries
and tables always describe the collected data. A zone removed in the editor keeps its
section and table, with a note instead of the figure.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.diagrams.layout.logical import site_level
from ntnxvision_core.diagrams.logical import (
    CONTAINER_HEADERS,
    LogicalModel,
    container_rows,
    logical_bullets,
)
from ntnxvision_core.exporters.content import (
    Chapter,
    Paragraph,
    continued_notes,
    first_part_budget,
    fit_layout,
    make_table,
    scene_figures,
)


def build(b: Any, ch: Chapter, dtype: Any) -> bool:
    model: LogicalModel = dtype.build_model(b.env)
    drawn: LogicalModel = b.figure_model(dtype, model)
    shown = {s.id: s for s in drawn.sites}
    many = len(model.sites) > 1
    sites = [s for s in model.sites if s.clusters]

    def bullets_of(site: Any) -> list[str]:
        # Each zone's section discloses only the edits inside that zone.
        note = b.edit_disclosure(dtype, model, drawn, {site.id})
        return [*logical_bullets(site), *([note] if note else [])]

    # One detail level for the estate (the least detail any drawn AZ needs), so like
    # AZs are drawn alike.
    level = max((site_level(shown[s.id], dtype.page, first_part_budget(bullets_of(s)))
                 for s in sites if s.id in shown and shown[s.id].clusters), default=0)
    added = False
    for site in sites:
        bullets = bullets_of(site)
        title = f"{dtype.section}: {site.name}" if many else dtype.section
        rows = container_rows(site)
        # Combined runs attribute every row to its Prism Central (the house rule).
        table = make_table(CONTAINER_HEADERS, rows, f"User storage containers, {site.name}",
                           [site.name] * len(rows) if b.env.is_combined else None)

        def run(budget: Any, sid: str = site.id) -> list[Any]:
            return list(dtype.layout(drawn, dtype.page, None, budget, site=sid, level=level))

        scenes = fit_layout(run, bullets) if site.id in shown else []
        if not scenes:
            ch.add(title, Paragraph(
                f"The logical cluster design of {site.name} was removed from the figure in "
                "the Diagrams editor. The table lists its collected containers.", "note"),
                table)
            added = True
            continue
        figs = scene_figures(scenes)
        figs[0].summary = bullets
        b.scope_edit_marks(dtype, model, drawn, figs, lambda sc, sid=site.id: {sid})
        ch.add(title, *figs, table)
        b.design_figures.setdefault(dtype.id, []).extend(figs)
        added = True
        if len(figs) > 1:
            b.finalizers.append(lambda figs=figs: continued_notes(figs))
    return added
