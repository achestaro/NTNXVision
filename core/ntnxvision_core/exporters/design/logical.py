"""Logical Cluster Design (A2) in the Logical Design chapter: one section per
availability zone with its figure (in parts when large), its design summary and the
user storage containers table."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.diagrams.layout.logical import layout_site
from ntnxvision_core.diagrams.logical import (
    CONTAINER_HEADERS,
    LogicalModel,
    container_rows,
    logical_bullets,
)
from ntnxvision_core.exporters.content import (
    Chapter,
    SceneFigure,
    first_part_budget,
    make_table,
)


def build(b: Any, ch: Chapter, dtype: Any) -> bool:
    model: LogicalModel = dtype.build_model(b.env)
    many = len(model.sites) > 1
    added = False
    for site in model.sites:
        if not site.clusters:
            continue
        bullets = logical_bullets(site)
        scenes = layout_site(site, dtype.page, first_part_budget(bullets))
        figs = [SceneFigure(sc, sc.caption) for sc in scenes]
        figs[0].summary = bullets
        table = make_table(CONTAINER_HEADERS, container_rows(site),
                           f"User storage containers, {site.name}")
        title = f"{dtype.section}: {site.name}" if many else dtype.section
        ch.add(title, *figs, table)
        b.design_figures.setdefault(dtype.id, []).extend(figs)
        added = True

        def finalize(figs: list[SceneFigure] = figs) -> None:
            for i, f in enumerate(figs[:-1]):
                nxt = figs[i + 1]
                f.note = (f"Continued in {nxt.label.rstrip('.')} (part {nxt.scene.part[0]} "
                          f"of {nxt.scene.part[1]}).")

        if len(figs) > 1:
            b.finalizers.append(finalize)
    return added
