"""Report section: the platform service designs (Kubernetes, Database Service, Objects,
Files), first in their chapter (NKP, NDB, NUS).

The figure (in parts when its clusters do not fit one page width) with its design
summary on the first part. The chapter's own tables follow it and list every member
the figure folds into a "+N more" card, with the sizes and settings the figure leaves
out. The figure draws the author's saved edit (Diagrams page) when there is one; the
summary always describes the collected data.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.diagrams import DiagramType
from ntnxvision_core.diagrams import services as S
from ntnxvision_core.exporters.content import (
    Chapter,
    continued_notes,
    fit_layout,
    scene_figures,
)


def build(b: Any, ch: Chapter, dtype: DiagramType) -> bool:
    model = dtype.build_model(b.env)
    if not model.clusters:
        return False
    drawn = b.figure_model(dtype, model)
    note = b.edit_disclosure(dtype, model, drawn)
    bullets = [*S.bullets(model), *([note] if note else [])]
    scenes = fit_layout(lambda budget: dtype.layout(drawn, dtype.page, None, budget), bullets)
    if not scenes:
        return False
    figs = scene_figures(scenes)
    figs[0].summary = bullets
    b.scope_edit_marks(dtype, model, drawn, figs, lambda sc: set(sc.meta.get("sites", []))
                       | set(sc.meta.get("anchors", [])))
    ch.add(dtype.section, *figs)
    b.design_figures[dtype.id] = figs
    names = {c.id: c.name for c in drawn.clusters}
    b.finalizers.append(lambda: continued_notes(figs, lambda sc: ", ".join(
        names.get(cid, cid) for cid in sc.meta.get("sites", []))))
    return True
