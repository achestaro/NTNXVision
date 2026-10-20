"""One report section per availability zone for a diagram type drawn per AZ (the
network and security figures N1 to N3): the AZ's figure (in parts when large) with its
design summary, then the AZ's tables, which list everything the figure summarises.

The figure draws the Diagrams page's saved edit (`figure_model`); the summary and the
tables come from the generated model, plus one bullet disclosing the edits inside that
AZ (an AZ no edit touches has no bullet and no "(edited)" caption). AZs past
`MAX_AZ_FIGURES` get their tables only, in one closing section with a note.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ntnxvision_core.diagrams import Refs
from ntnxvision_core.diagrams.netcommon import MAX_AZ_FIGURES
from ntnxvision_core.exporters.content import (
    Chapter,
    Paragraph,
    SceneFigure,
    Table,
    continued_notes,
    first_part_budget,
)


@dataclass
class PerAz:
    #: The AZs of a model (sites with something to draw).
    sites: Callable[[Any], list[Any]]
    bullets: Callable[[Any], list[str]]
    #: (site) -> tables of the AZ (None entries skipped); the first is the one the
    #: figure's "+N more" text points at.
    tables: Callable[[Any], list[Table | None]]
    #: Section title for AZs beyond the figure cap.
    rest_title: str = "Other availability zones"


def build(b: Any, ch: Chapter, dtype: Any, spec: PerAz) -> bool:
    model = dtype.build_model(b.env)
    drawn = b.figure_model(dtype, model)
    sites = spec.sites(model)
    if not sites:
        return False
    drawn_sites = {s.id: s for s in spec.sites(drawn)}
    many = len(sites) > 1
    for site in sites[:MAX_AZ_FIGURES]:
        bullets = spec.bullets(site)
        # Each AZ's section discloses only the edits inside that AZ.
        edit_note = b.edit_disclosure(dtype, model, drawn, {site.id})
        if edit_note:
            bullets = [*bullets[:4], edit_note]
        # The drawn (edited) model itself is laid out, one AZ at a time.
        src = drawn if site.id in drawn_sites else model
        scenes = dtype.layout(src, dtype.page, None, first_part_budget(bullets),
                              site=site.id)
        if len(scenes) > 1:
            scenes = dtype.layout(src, dtype.page, None, first_part_budget(bullets, True),
                                  site=site.id)
        figs = [SceneFigure(sc, sc.caption) for sc in scenes]
        b.scope_edit_marks(dtype, model, drawn, figs, lambda sc, sid=site.id: {sid})
        if figs:
            figs[0].summary = bullets
        tables = [t for t in spec.tables(site) if t is not None]
        title = f"{dtype.section}: {site.name}" if many else dtype.section
        ch.add(title, *figs, *tables)
        b.design_figures.setdefault(dtype.id, []).extend(figs)
        b.finalizers.append(lambda figs=figs, tables=tables, src=src, sid=site.id,
                            bullets=bullets: _finalize(dtype, figs, tables, src, sid, bullets))
    rest = sites[MAX_AZ_FIGURES:]
    if rest:
        tables = [t for s in rest for t in spec.tables(s) if t is not None]
        names = ", ".join(s.name for s in rest)
        ch.add(f"{dtype.section}: {spec.rest_title}", Paragraph(
            f"Figures are drawn for the first {MAX_AZ_FIGURES:,} availability zones; "
            f"the tables below list the others ({names}).", "note"), *tables)
    return True


def _finalize(dtype: Any, figs: list[SceneFigure], tables: list[Table], model: Any,
              site: str, bullets: list[str]) -> None:
    """Point the figure's "+N more" text at the numbered table, chain the parts."""
    if not figs or not figs[0].label:  # the chapter is left out of this report
        return
    if tables:
        ref = tables[0].label.rstrip(".")
        scenes = [f.scene for f in figs]
        again = dtype.layout(model, dtype.page, Refs(clusters_table=ref),
                             first_part_budget(bullets, len(scenes) > 1), site=site)
        if [sc.part for sc in again] == [sc.part for sc in scenes]:
            for f, sc in zip(figs, again, strict=True):
                f.scene = sc
                sc.caption = f.caption  # as scoped (scope_edit_marks)
        if any(f.scene.meta.get("summarised") for f in figs):
            figs[-1].note = f"Everything a \"+N more\" card stands for is listed in {ref}."
    continued_notes(figs)
