"""Every architecture figure fits its page in the PDF and in Word: the caption is on the
figure's page, nothing runs into the footer, and part 1 either fits under its section's
headings and summary or starts a page of its own (`diagrams.layout.fit`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ntnxvision_core.exporters import ExportOptions, export_environment
from ntnxvision_core.exporters import pdf as pdf_mod
from ntnxvision_core.exporters.content import SceneFigure, build_report, first_part_budget

ESTATES = ("small", "medium", "enterprise", "xlarge", "pc01", "multi3")


@pytest.mark.parametrize("name", ESTATES)
def test_pdf_caption_on_its_figure_page_and_clear_of_the_footer(
        design_envs: dict[str, Any], name: str, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    drawn: list[tuple[str, int, float, float]] = []  # scene id, page, top, bottom
    page_h: list[float] = []
    captions: list[tuple[str, int, float, float]] = []
    real_draw, real_cap = pdf_mod.draw_scene, pdf_mod.PdfExporter._figure_caption

    def draw(pdf: Any, scene: Any, x: float, y: float, *, scale: float = 1.0,
             **k: Any) -> Any:
        drawn.append((scene.id, pdf.page_no(), y, y + scene.height * scale))
        page_h.append(pdf.h)
        return real_draw(pdf, scene, x, y, scale=scale, **k)

    def caption(self: Any, pdf: Any, f: Any, width: float) -> None:
        top, page = pdf.get_y(), pdf.page_no()
        real_cap(self, pdf, f, width)
        if isinstance(f, SceneFigure):
            captions.append((f.scene.id, page, top, pdf.get_y()))

    monkeypatch.setattr(pdf_mod, "draw_scene", draw)
    monkeypatch.setattr(pdf_mod.PdfExporter, "_figure_caption", caption)
    export_environment(design_envs[name], "pdf", tmp_path, name)
    assert drawn and len(drawn) == len(captions)
    for (sid, page, _top, bottom), (cid, cpage, ctop, cbottom), h in zip(
            drawn, captions, page_h, strict=True):
        assert sid == cid
        assert cpage == page, f"{name}: the caption of {sid} is not on its page"
        assert ctop >= bottom - 0.01
        foot = h - pdf_mod.BOTTOM
        assert bottom <= foot + 0.01 and cbottom <= foot + 0.01, \
            f"{name}: {sid} runs into the footer ({bottom:.1f}, {cbottom:.1f} > {foot:.1f})"


@pytest.mark.parametrize("name", ("enterprise", "xlarge"))
def test_part_one_fits_under_its_summary_or_starts_a_page(design_envs: dict[str, Any],
                                                         name: str) -> None:
    report = build_report(design_envs[name], ExportOptions(), "x")
    figs = [b for ch in report.chapters for s in ch.sections for b in s.blocks
            if isinstance(b, SceneFigure) and b.summary]
    assert figs
    for f in figs:
        budget = first_part_budget(f.summary, True)(f.scene.width)
        assert f.new_page or f.scene.height <= budget + 12.01, \
            f"{f.scene.id}: {f.scene.height:.1f} under a {budget:.1f} pt budget"
