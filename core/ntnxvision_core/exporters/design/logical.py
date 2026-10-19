"""Logical Cluster Design (A2) in the Logical Design chapter: one section per
availability zone with its figure (in parts when large), its design summary and the
user storage containers table."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.diagrams.layout.logical import layout_site, site_level
from ntnxvision_core.diagrams.logical import (
    CONTAINER_HEADERS,
    LogicalModel,
    container_rows,
    logical_bullets,
)
from ntnxvision_core.exporters.content import (
    Chapter,
    SceneFigure,
    continued_notes,
    first_part_budget,
    make_table,
)


def build(b: Any, ch: Chapter, dtype: Any) -> bool:
    model: LogicalModel = dtype.build_model(b.env)
    many = len(model.sites) > 1
    sites = [s for s in model.sites if s.clusters]
    budgets = {s.id: first_part_budget(logical_bullets(s)) for s in sites}
    # One detail level for the estate (the least detail any AZ needs), so like AZs
    # are drawn alike.
    level = max((site_level(s, dtype.page, budgets[s.id]) for s in sites), default=0)
    added = False
    for site in sites:
        bullets = logical_bullets(site)
        scenes = layout_site(site, dtype.page, budgets[site.id], level)
        figs = [SceneFigure(sc, sc.caption) for sc in scenes]
        figs[0].summary = bullets
        rows = container_rows(site)
        # Combined runs attribute every row to its Prism Central (the house rule).
        table = make_table(CONTAINER_HEADERS, rows, f"User storage containers, {site.name}",
                           [site.name] * len(rows) if b.env.is_combined else None)
        title = f"{dtype.section}: {site.name}" if many else dtype.section
        ch.add(title, *figs, table)
        b.design_figures.setdefault(dtype.id, []).extend(figs)
        added = True

        if len(figs) > 1:
            b.finalizers.append(lambda figs=figs: continued_notes(figs))
    return added
