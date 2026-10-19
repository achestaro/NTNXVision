"""Report section: Management Plane Design (A3), in the Prism Central chapter.

The figure (in parts for many Prism Centrals), its design summary, the Prism Central
deployment table and, when the figure summarises registered clusters in a "+N more"
card, the registered-clusters table that lists every one of them.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.diagrams import DiagramType, Refs
from ntnxvision_core.diagrams import management as M
from ntnxvision_core.diagrams.facts import short_name
from ntnxvision_core.exporters.content import (
    Chapter,
    Table,
    continued_notes,
    fit_layout,
    make_table,
    scene_figures,
)


def _drawn(scenes: list[Any], labels: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sc, label in zip(scenes, labels, strict=True):
        for cid in sc.meta.get("drawn", []):
            out[cid] = label
        for cid in sc.meta.get("summarised", []):
            out[cid] = f"Summary card, {label}"
    return out


def build(b: Any, ch: Chapter, dtype: DiagramType) -> bool:
    env = b.env
    model = dtype.build_model(env)
    if not any(s.pc for s in model.sites):
        return False
    page = dtype.page
    # The figure draws the saved edit (Diagrams page); summary and tables the data.
    drawn = b.figure_model(dtype, model)
    note = b.edit_disclosure(dtype, model, drawn)
    bullets = [*M.bullets(model), *([note] if note else [])]
    scenes = fit_layout(lambda budget: dtype.layout(drawn, page, None, budget), bullets)
    if not scenes:
        return False
    figs = scene_figures(scenes)
    figs[0].summary = bullets
    # The summary covers the whole figure; a part's caption says "(edited)" only when
    # an edit touches what that part draws.
    b.scope_edit_marks(dtype, model, drawn, figs, lambda sc: b.part_scope(model, sc))
    pcs = make_table(M.PC_HEADERS, M.pc_rows(model), "Prism Central deployment",
                     prune=("Paired with", "PCVM IP addresses", "Per PCVM"))
    clusters: Table | None = None
    if any(sc.meta.get("summarised") for sc in scenes):
        rows, srcs = M.cluster_rows(model, _drawn(scenes, [f"Figure {i}" for i in
                                                           range(len(scenes))]))
        clusters = make_table(M.CLUSTER_HEADERS, rows, "Clusters registered per Prism Central",
                              srcs if env.is_combined else None,
                              prune=("DSIP", "vCenter", "Connectivity"))
    ch.add(dtype.section, *figs, pcs, clusters)

    def finalize() -> None:
        labels = [f.label.rstrip(".") for f in figs]
        if clusters is not None:
            ref = clusters.label.rstrip(".")
            again = fit_layout(lambda budget: dtype.layout(
                drawn, page, Refs(clusters_table=ref), budget), bullets)
            if [sc.part for sc in again] == [sc.part for sc in scenes]:
                for f, sc in zip(figs, again, strict=True):
                    f.scene = sc
                    sc.caption = f.caption  # as scoped (scope_edit_marks)
                    f.new_page = bool(sc.meta.get("own_page"))
                scenes[:] = again
            rows, _ = M.cluster_rows(model, _drawn(scenes, labels))
            col = clusters.headers.index("In figure")
            for row, new in zip(clusters.rows, rows, strict=True):
                row[col] = new[-1]
        if clusters is not None:
            ref = clusters.label.rstrip(".")
            for f in figs:
                if f.scene.meta.get("summarised"):
                    f.note = f"Clusters in a \"+N more\" card are listed in {ref}."
        continued_notes(figs, lambda sc: ", ".join(
            short_name(drawn.name_of(sid)) for sid in sc.meta.get("sites", [])))

    b.finalizers.append(finalize)
    return True
