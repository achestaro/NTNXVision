"""PDF exporter (fpdf2).

Renders the shared report content (`content.build_report`) as a landscape A4 document in
the light Runway theme: a white cover with an iris band, a linked table of contents,
chapters that each open on a new page with an iris heading marker, and ruled tables whose
text wraps instead of being cut. Figtree, Montserrat Light and JetBrains Mono are embedded
(subset) from the vendored OFL fonts, so any Unicode text renders and the output looks the
same on every machine.

Accessibility and navigation: every chapter and section is a PDF bookmark (the viewer
opens with the bookmarks pane), the contents rows are links, figures carry alt text, and
the document title, language, author and subject are set. Page numbers are physical page
numbers (the unnumbered cover is page 1), so the contents, the footer and the viewer's
own page counter always agree. fpdf2 is pure Python (no native GTK/Pango), so it bundles
cleanly with PyInstaller on Windows and macOS.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, cast

from fpdf import FPDF
from fpdf.enums import PageMode
from fpdf.outline import OutlineSection
from fpdf.prefs import ViewerPreferences

from ntnxvision_core.diagrams.render.pdf import draw_scene
from ntnxvision_core.exporters import theme as T
from ntnxvision_core.exporters.content import (
    Bullets,
    Chapter,
    Figure,
    FigureRow,
    FigureStrip,
    Paragraph,
    Report,
    SceneFigure,
    Status,
    Table,
    build_report,
    cell_text,
)
from ntnxvision_core.exporters.content import versions as _shared_versions
from ntnxvision_core.models import Environment
from ntnxvision_core.numfmt import NA

SANS, LIGHT, MONO = "Figtree", "Montserrat", "JetBrainsMono"

MARGIN_X = 16.0
TOP = 21.0
BOTTOM = 17.0

# Table metrics (mm / pt).
PAD_X, PAD_Y = 1.7, 1.45
BODY_PT, MONO_PT, HEAD_PT = 8.0, 7.4, 7.8
LINE_H = 3.75
MAX_LINES = 4

# Table of contents row heights (mm).
TOC_H0, TOC_H1 = 7.4, 5.5
#: Height of the "Contents" heading block (marker heading cell + its 3 mm gap), exactly
#: what `_render_toc` draws, so the reserved page count never disagrees with it.
TOC_TITLE_BLOCK = 10.4 + 3.0


class _Pdf(FPDF):
    def __init__(self, report: Report) -> None:
        super().__init__(orientation="L", unit="mm", format="A4")
        self.report = report
        self.fresh_page = False  # the current page is blank and may host the next chapter
        self.set_margins(MARGIN_X, TOP, MARGIN_X)
        self.set_auto_page_break(auto=True, margin=BOTTOM)
        # Padding is applied explicitly (table cells, strip); fpdf2's implicit cell margin
        # would shift text off the grid and shrink wrap widths.
        self.c_margin = 0
        self.add_font(SANS, "", str(T.FIGTREE))
        self.add_font(SANS, "I", str(T.FIGTREE_ITALIC))
        self.add_font(SANS, "B", str(T.FIGTREE_SEMIBOLD))
        self.add_font(LIGHT, "", str(T.MONTSERRAT_LIGHT))
        self.add_font(MONO, "", str(T.JETBRAINS_MONO))
        # Characters Figtree lacks (box drawing, check marks, rare symbols) come from
        # JetBrains Mono rather than rendering as missing-glyph boxes.
        self.set_fallback_fonts([MONO], exact_match=False)

    # ─ running header / footer ─
    def header(self) -> None:
        if self.page_no() == 1:
            return
        y = 9.0
        self.set_xy(self.l_margin, y)
        self.set_font(SANS, "B", 7.8)
        self.set_text_color(*T.INK_RGB)
        word = "NTNXVision"
        self.cell(self.get_string_width(word), 5, word)
        self.set_font(SANS, "", 7.8)
        self.set_text_color(*T.INK_3_RGB)
        self.cell(0, 5, f"  ·  {self.report.running_title}")
        self.set_xy(self.l_margin, y)
        self.cell(0, 5, T.clean(self.report.scope), align="R")
        self.set_draw_color(*T.RULE_2_RGB)
        self.set_line_width(0.2)
        self.line(self.l_margin, y + 6.2, self.w - self.r_margin, y + 6.2)
        self.set_xy(self.l_margin, TOP)

    def footer(self) -> None:
        if self.page_no() == 1:
            return
        y = self.h - 11.5
        self.set_draw_color(*T.RULE_2_RGB)
        self.set_line_width(0.2)
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.set_xy(self.l_margin, y + 1.6)
        self.set_font(SANS, "", 7.3)
        self.set_text_color(*T.INK_3_RGB)
        left = "Confidential"
        if self.report.prepared_for:
            left += f" · Prepared for {T.clean(self.report.prepared_for)}"
        self.cell(0, 5, left)
        self.set_xy(self.l_margin, y + 1.6)
        self.cell(0, 5, f"Page {self.page_no()} of {{nb}}", align="R")


class PdfExporter:
    #: Pages in the last export (for the "48 pages" report detail).
    page_count: int = 0

    def export(self, env: Environment, path: str | Path, options: Any = None) -> Path:
        from ntnxvision_core.exporters import ExportOptions

        opts = ExportOptions.coerce(options)
        report = build_report(env, opts, eyebrow="Environment report")
        pdf = _Pdf(report)
        pdf.set_title(report.title)
        pdf.set_subject(report.subject)
        pdf.set_keywords(report.keywords)
        pdf.set_creator(f"NTNXVision {env.tool_version}")
        pdf.set_author(opts.author or "NTNXVision")
        pdf.set_lang("en-US")
        pdf.page_mode = PageMode.USE_OUTLINES
        pdf.viewer_preferences = ViewerPreferences(display_doc_title=True)

        self._cover(pdf, report)
        pdf.add_page()
        pdf.start_section("Contents", level=0)
        pdf.insert_toc_placeholder(self._render_toc, pages=self._toc_pages(pdf, report))
        pdf.fresh_page = True
        for chapter in report.chapters:
            self._chapter(pdf, chapter)

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        pdf.output(str(out))
        self.page_count = len(pdf.pages)
        return out

    # ─── cover ─────────────────────────────────────────────────────

    def _cover(self, pdf: _Pdf, report: Report) -> None:
        cover = report.cover
        pdf.add_page()
        pdf.set_auto_page_break(False)
        # Iris band across the top edge: the cover's one accent.
        pdf.set_fill_color(*T.IRIS_RGB)
        pdf.rect(0, 0, pdf.w, 4.2, style="F")
        x = 24.0
        pdf.set_xy(x, 20)
        pdf.set_font(SANS, "B", 10.5)
        pdf.set_text_color(*T.INK_RGB)
        pdf.cell(0, 6, "NTNXVision")

        pdf.set_xy(x, 52)
        pdf.set_font(SANS, "B", 8.5)
        pdf.set_text_color(*T.INK_3_RGB)
        pdf.set_char_spacing(0.8)
        pdf.cell(0, 5, cover.eyebrow.upper())
        pdf.set_char_spacing(0)
        pdf.set_xy(x, 60)
        pdf.set_font(LIGHT, "", 30)
        pdf.set_text_color(*T.INK_RGB)
        pdf.multi_cell(180, 12.5, T.clean(cover.title), align="L", new_x="LEFT", new_y="NEXT")
        pdf.set_x(x)
        pdf.ln(2)
        pdf.set_x(x)
        pdf.set_font(SANS, "", 13)
        pdf.set_text_color(*T.INK_2_RGB)
        pdf.multi_cell(230, 6.5, T.clean(cover.subtitle), align="L", new_x="LEFT", new_y="NEXT")
        y = pdf.get_y() + 5
        pdf.set_fill_color(*T.IRIS_RGB)
        pdf.rect(x, y, 22, 0.9, style="F")

        # Report facts: a quiet two-column list.
        y = max(y + 12, 122.0)
        label_w = 40.0
        for label, value in cover.meta:
            pdf.set_xy(x, y)
            pdf.set_font(SANS, "", 8.8)
            pdf.set_text_color(*T.INK_3_RGB)
            pdf.cell(label_w, 5.2, label)
            pdf.set_font(SANS, "", 9.4)
            pdf.set_text_color(*T.INK_RGB)
            pdf.set_xy(x + label_w, y)
            pdf.multi_cell(pdf.w - x - label_w - 24, 5.2, T.clean(value) or NA,
                           align="L", new_x="LMARGIN", new_y="NEXT")
            y = pdf.get_y() + 0.9
        pdf.set_draw_color(*T.RULE_2_RGB)
        pdf.set_line_width(0.2)
        pdf.line(x, pdf.h - 18, pdf.w - 24, pdf.h - 18)
        pdf.set_xy(x, pdf.h - 16)
        pdf.set_font(SANS, "I", 8)
        pdf.set_text_color(*T.INK_3_RGB)
        pdf.cell(0, 5, T.clean(cover.note))
        pdf.set_auto_page_break(True, margin=BOTTOM)

    # ─── table of contents ─────────────────────────────────────────

    @staticmethod
    def _toc_rows(report: Report) -> list[tuple[int, str, str]]:
        rows: list[tuple[int, str, str]] = []
        for ch in report.chapters:
            title = f"Appendix: {ch.title}" if ch.appendix else ch.title
            rows.append((0, ch.number, title))
            rows.extend((1, s.number, s.title) for s in ch.sections)
        return rows

    def _toc_pages(self, pdf: _Pdf, report: Report) -> int:
        """Pages the contents need, reserved up front so no page number moves after the
        footers are drawn (an overflowing contents shifted every later page by one)."""
        usable = pdf.h - BOTTOM - TOP - 4
        pages, y = 1, TOC_TITLE_BLOCK
        for level, _num, _title in self._toc_rows(report):
            h = TOC_H0 if level == 0 else TOC_H1
            if y + h > usable:
                pages, y = pages + 1, 0.0
            y += h
        return pages

    def _render_toc(self, pdf: FPDF, outline: list[OutlineSection]) -> None:
        assert isinstance(pdf, _Pdf)
        entries = [e for e in outline if e.name != "Contents"]
        rows = self._toc_rows(pdf.report)
        pdf.set_y(TOP)
        self._marker_heading(pdf, "Contents", size=18)
        pdf.ln(3)
        usable_bottom = pdf.h - BOTTOM - 4
        num_w, page_w = 11.0, 16.0
        for (level, num, title), entry in zip(rows, entries, strict=False):
            h = TOC_H0 if level == 0 else TOC_H1
            if pdf.get_y() + h > usable_bottom:
                pdf.add_page()
                pdf.set_y(TOP)
            x0, y0 = pdf.l_margin, pdf.get_y()
            indent = 0.0 if level == 0 else num_w
            pdf.set_x(x0 + indent)
            if level == 0:
                pdf.set_font(SANS, "B", 10.3)
                pdf.set_text_color(*T.IRIS_RGB)
                pdf.cell(num_w, h, num)
                pdf.set_text_color(*T.INK_RGB)
                pdf.cell(pdf.epw - num_w - page_w, h, T.clean(title))
                pdf.set_font(MONO, "", 9)
                pdf.set_text_color(*T.INK_2_RGB)
            else:
                pdf.set_font(SANS, "", 9)
                pdf.set_text_color(*T.INK_3_RGB)
                pdf.cell(num_w + 1, h, num)
                pdf.set_text_color(*T.INK_2_RGB)
                pdf.cell(pdf.epw - indent - num_w - 1 - page_w, h, T.clean(title))
                pdf.set_font(MONO, "", 8.2)
                pdf.set_text_color(*T.INK_3_RGB)
            pdf.cell(page_w, h, str(entry.page_number), align="R", new_x="LMARGIN",
                     new_y="NEXT")
            pdf.link(x0, y0, pdf.epw, h, pdf.add_link(page=entry.page_number))
            if level == 0:
                pdf.set_draw_color(*T.RULE_2_RGB)
                pdf.set_line_width(0.18)
                pdf.line(x0, y0 + h, x0 + pdf.epw, y0 + h)

    # ─── chapters ──────────────────────────────────────────────────

    def _chapter(self, pdf: _Pdf, ch: Chapter) -> None:
        if pdf.fresh_page:
            pdf.fresh_page = False
            pdf.set_y(TOP)
        else:
            pdf.add_page()
        pdf.start_section(T.clean(ch.heading), level=0)
        self._marker_heading(pdf, ch.heading)
        pdf.ln(3)
        self._blocks(pdf, ch.blocks)
        for section in ch.sections:
            self._h2(pdf, section.heading, self._need(pdf, section.blocks[0]))
            self._blocks(pdf, section.blocks)

    def _need(self, pdf: _Pdf, blk: Any) -> float:
        """Height (mm) the first block under a heading needs on the same page, so the
        heading is never stranded at the foot of a page."""
        if isinstance(blk, Table):
            return 26.0
        if isinstance(blk, Figure):
            d = blk.diagram
            w = min(pdf.epw, d.width_in * 25.4)
            return min(w * d.height / d.width, pdf.h - TOP - BOTTOM - 16) + 10
        if isinstance(blk, SceneFigure):
            return self._scene_need(pdf, blk)
        if isinstance(blk, FigureRow) and blk.figures:
            return max(min(80.0, f.diagram.width_in * 25.4) * f.diagram.height
                       / f.diagram.width for f in blk.figures) + 12
        return 18.0

    def _marker_heading(self, pdf: _Pdf, text: str, size: float = 18) -> None:
        """Heading 1: an iris marker bar beside ink text (the heading accent)."""
        y = pdf.get_y()
        pdf.set_fill_color(*T.IRIS_RGB)
        pdf.rect(pdf.l_margin, y + 1.6, 1.3, 7.2, style="F")
        pdf.set_xy(pdf.l_margin + 5, y)
        pdf.set_font(SANS, "B", size)
        pdf.set_text_color(*T.INK_RGB)
        pdf.cell(0, 10.4, T.clean(text), align="L", new_x="LMARGIN", new_y="NEXT")

    def _h2(self, pdf: _Pdf, text: str, need: float = 26.0) -> None:
        # Keep the heading with what follows: never strand it at the foot of a page.
        if pdf.get_y() + 13 + need > pdf.page_break_trigger:
            pdf.add_page()
        else:
            pdf.ln(2)
        pdf.start_section(T.clean(text), level=1)
        pdf.set_font(SANS, "B", 12.5)
        pdf.set_text_color(*T.INK_RGB)
        pdf.cell(0, 8, T.clean(text), align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)

    def _blocks(self, pdf: _Pdf, blocks: list[Any]) -> None:
        for blk in blocks:
            if isinstance(blk, Paragraph):
                self._paragraph(pdf, blk)
            elif isinstance(blk, Bullets):
                self._bullets(pdf, blk)
            elif isinstance(blk, FigureStrip):
                self._strip(pdf, blk)
            elif isinstance(blk, Table):
                self._table(pdf, blk)
            elif isinstance(blk, Figure):
                self._figure(pdf, blk)
            elif isinstance(blk, SceneFigure):
                self._scene_figure(pdf, blk)
            elif isinstance(blk, FigureRow):
                self._figure_row(pdf, blk)

    def _paragraph(self, pdf: _Pdf, p: Paragraph) -> None:
        if p.style == "note":
            pdf.set_font(SANS, "I", 9)
            pdf.set_text_color(*T.INK_3_RGB)
        else:
            pdf.set_font(SANS, "", 10)
            pdf.set_text_color(*T.INK_RGB)
        pdf.multi_cell(min(pdf.epw, 235), 5.3, T.clean(p.text), align="L", new_x="LMARGIN",
                       new_y="NEXT")
        pdf.ln(2.6)

    def _bullets(self, pdf: _Pdf, b: Bullets) -> None:
        if b.intro:
            if pdf.get_y() + 14 > pdf.page_break_trigger:
                pdf.add_page()
            pdf.set_font(SANS, "B", 10)
            pdf.set_text_color(*T.INK_RGB)
            pdf.cell(0, 6, T.clean(b.intro), align="L", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.6)
        width = min(pdf.epw, 235) - 7
        for item in b.items:
            pdf.set_font(SANS, "", 10)
            lines = cast(list[str], pdf.multi_cell(width, 5.2, T.clean(item), align="L",
                                                   dry_run=True, output="LINES"))
            if pdf.get_y() + 5.2 * len(lines) > pdf.page_break_trigger:
                pdf.add_page()
            y = pdf.get_y()
            pdf.set_fill_color(*T.INK_3_RGB)
            pdf.rect(pdf.l_margin + 1.6, y + 2.1, 1.1, 1.1, style="F")
            pdf.set_xy(pdf.l_margin + 7, y)
            pdf.set_text_color(*T.INK_RGB)
            pdf.multi_cell(width, 5.2, T.clean(item), align="L", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.8)
        pdf.ln(2.2)

    def _strip(self, pdf: _Pdf, strip: FigureStrip) -> None:
        """Headline figures in one ruled row: small label, light numeral, qualifier."""
        if not strip.items:
            return
        n = len(strip.items)
        cell_w = min(pdf.epw / n, 48.0)
        has_q = any(i.qualifier for i in strip.items)
        h = 29.0 if has_q else 24.0
        if pdf.get_y() + h + 6 > pdf.page_break_trigger:
            pdf.add_page()
        pdf.ln(1)
        y0, x0 = pdf.get_y(), pdf.l_margin
        pdf.set_draw_color(*T.RULE_RGB)
        pdf.set_line_width(0.2)
        pdf.line(x0, y0, x0 + cell_w * n, y0)
        pdf.line(x0, y0 + h, x0 + cell_w * n, y0 + h)
        for i, item in enumerate(strip.items):
            x = x0 + i * cell_w
            if i:
                pdf.set_draw_color(*T.RULE_2_RGB)
                pdf.line(x, y0 + 3.5, x, y0 + h - 3.5)
            pdf.set_xy(x + 4, y0 + 3.5)
            pdf.set_font(SANS, "", 8.4)
            pdf.set_text_color(*T.INK_3_RGB)
            pdf.cell(cell_w - 6, 4.5, T.clean(item.label))
            pdf.set_xy(x + 4, y0 + 9.5)
            pdf.set_font(LIGHT, "", 23)
            pdf.set_text_color(*T.INK_RGB)
            pdf.cell(cell_w - 6, 10, T.clean(item.value))
            if item.qualifier:
                pdf.set_xy(x + 4, y0 + 21)
                pdf.set_font(MONO, "", 7.6)
                rgb = T.STATUS_RGB.get(item.tone or "", T.INK_3_RGB)
                pdf.set_text_color(*rgb)
                pdf.cell(cell_w - 6, 4.5, T.clean(item.qualifier))
        pdf.set_xy(pdf.l_margin, y0 + h + 6)

    # ─── tables ────────────────────────────────────────────────────

    def _widths(self, pdf: _Pdf, t: Table) -> list[float]:
        """Column widths (mm): natural content width when it fits the page, otherwise
        shrunk toward each column's minimum so wide columns wrap rather than overflow."""
        natural, minimum = [], []
        sample = t.rows[:250]
        for i, header in enumerate(t.headers):
            pdf.set_font(SANS, "B", HEAD_PT)
            head_w = pdf.get_string_width(header) + 2 * PAD_X
            longest_word = max((pdf.get_string_width(w) for w in header.split()), default=0)
            if t.mono[i]:
                pdf.set_font(MONO, "", MONO_PT)
            else:
                pdf.set_font(SANS, "", BODY_PT)
            texts = [cell_text(r[i], header) for r in sample]
            widest = max((pdf.get_string_width(x) for x in texts), default=0.0)
            # The longest unbreakable run (a status word, a timestamp joined by a no-break
            # space) sets the floor, so shrinking wraps between words, never inside one.
            token = max((pdf.get_string_width(w) for x in texts for w in x.split(" ")),
                        default=0.0)
            glyph = 3.0 if any(isinstance(r[i], Status) for r in sample) else 0.0
            natural.append(min(max(head_w, widest + 2 * PAD_X + glyph + 0.6), 95.0))
            pdf.set_font(SANS, "", BODY_PT)
            na_w = pdf.get_string_width(NA) + 2 * PAD_X + 0.6
            minimum.append(max(longest_word + 2 * PAD_X, na_w,
                               min(token + 2 * PAD_X + glyph + 0.6, 40.0),
                               min(natural[-1], 16.0)))
        total = sum(natural)
        if total <= pdf.epw:
            # Fill the line when the table is already most of the way there.
            if total > pdf.epw * 0.62:
                return [w * pdf.epw / total for w in natural]
            return natural
        floor = sum(minimum)
        if floor >= pdf.epw:
            return [w * pdf.epw / floor for w in minimum]
        spare = pdf.epw - floor
        flex = total - floor
        return [m + (n - m) * spare / flex for n, m in zip(natural, minimum, strict=True)]

    def _lines(self, pdf: _Pdf, text: str, width: float) -> list[str]:
        lines = cast(list[str], pdf.multi_cell(max(width, 1.0), LINE_H, text, align="L",
                                               dry_run=True, output="LINES"))
        lines = [str(x) for x in lines] or [""]
        if len(lines) > MAX_LINES:
            lines = lines[:MAX_LINES]
            lines[-1] = lines[-1].rstrip()[:-1] + "…"
        return lines

    def _caption(self, pdf: _Pdf, t: Table, continued: bool = False) -> None:
        pdf.set_font(SANS, "B", 8.6)
        pdf.set_text_color(*T.INK_2_RGB)
        label = t.label + " "
        pdf.cell(pdf.get_string_width(label), 5, label)
        pdf.set_font(SANS, "I" if continued else "", 8.6)
        text = "(continued)" if continued else T.clean(t.caption)
        pdf.cell(0, 5, text, align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    def _header_row(self, pdf: _Pdf, t: Table, widths: list[float]) -> None:
        pdf.set_font(SANS, "B", HEAD_PT)
        wrapped = [self._lines(pdf, h, w - 2 * PAD_X) for h, w in zip(t.headers, widths,
                                                                      strict=True)]
        h = max(len(x) for x in wrapped) * LINE_H + 2 * PAD_Y
        y, x = pdf.get_y(), pdf.l_margin
        pdf.set_fill_color(*T.HEADER_FILL_RGB)
        pdf.rect(x, y, sum(widths), h, style="F")
        pdf.set_text_color(*T.INK_2_RGB)
        for lines, w, align in zip(wrapped, widths, t.align, strict=True):
            for k, line in enumerate(lines):
                pdf.set_xy(x + PAD_X, y + PAD_Y + k * LINE_H)
                pdf.cell(w - 2 * PAD_X, LINE_H, line, align=align)
            x += w
        pdf.set_draw_color(*T.IRIS_RGB)
        pdf.set_line_width(0.35)
        pdf.line(pdf.l_margin, y + h, pdf.l_margin + sum(widths), y + h)
        pdf.set_xy(pdf.l_margin, y + h)

    def _table(self, pdf: _Pdf, t: Table) -> None:
        widths = self._widths(pdf, t)
        # Caption, header and the first rows travel together.
        if pdf.get_y() + 24 > pdf.page_break_trigger:
            pdf.add_page()
        self._caption(pdf, t)
        self._header_row(pdf, t, widths)
        pdf.set_auto_page_break(False)
        for row in t.rows:
            cells = []
            for value, header, w, mono in zip(row, t.headers, widths, t.mono, strict=True):
                text = cell_text(value, header)
                glyph = 3.0 if isinstance(value, Status) else 0.0
                pdf.set_font(MONO if mono and text != NA else SANS, "",
                             MONO_PT if mono and text != NA else BODY_PT)
                cells.append((value, text, self._lines(pdf, text, w - 2 * PAD_X - glyph),
                              mono and text != NA))
            h = max(len(c[2]) for c in cells) * LINE_H + 2 * PAD_Y
            if pdf.get_y() + h > pdf.h - BOTTOM:
                pdf.add_page()
                self._caption(pdf, t, continued=True)
                self._header_row(pdf, t, widths)
            y, x = pdf.get_y(), pdf.l_margin
            for (value, text, lines, mono), w, align in zip(cells, widths, t.align,
                                                          strict=True):
                pdf.set_font(MONO if mono else SANS, "", MONO_PT if mono else BODY_PT)
                missing = text == NA
                pdf.set_text_color(*(T.INK_3_RGB if missing else T.INK_RGB))
                tx, tw = x + PAD_X, w - 2 * PAD_X
                if isinstance(value, Status):
                    pdf.set_fill_color(*T.STATUS_RGB[value.tone])
                    pdf.rect(tx, y + PAD_Y + 0.95, 1.85, 1.85, style="F")
                    tx, tw = tx + 3.0, tw - 3.0
                for k, line in enumerate(lines):
                    pdf.set_xy(tx, y + PAD_Y + k * LINE_H)
                    pdf.cell(tw, LINE_H, line, align=align)
                x += w
            pdf.set_draw_color(*T.RULE_2_RGB)
            pdf.set_line_width(0.15)
            pdf.line(pdf.l_margin, y + h, pdf.l_margin + sum(widths), y + h)
            pdf.set_xy(pdf.l_margin, y + h)
        pdf.set_auto_page_break(True, margin=BOTTOM)
        pdf.ln(6)

    # ─── figures ───────────────────────────────────────────────────

    def _figure(self, pdf: _Pdf, f: Figure) -> None:
        d = f.diagram
        w = min(pdf.epw, d.width_in * 25.4)
        h = w * d.height / d.width
        max_h = pdf.h - TOP - BOTTOM - 16
        if h > max_h:
            w, h = w * max_h / h, max_h
        if pdf.get_y() + h + 9 > pdf.page_break_trigger:
            pdf.add_page()
        pdf.image(io.BytesIO(d.png), x=pdf.l_margin, w=w, alt_text=T.clean(f.title))
        pdf.ln(1.2)
        self._figure_caption(pdf, f, pdf.epw)
        pdf.ln(4.5)

    # ─── architecture diagrams (vector) ────────────────────────────
    # One set of measurements serves `_scene_need` and `_scene_figure`, so the space
    # reserved for a figure (with its summary, caption and note) is exactly what it draws.

    SUMMARY_PT = 9.2
    SUMMARY_LH = 4.6
    SUMMARY_GAP = 1.4
    EYEBROW_H = 6.0
    SUMMARY_COL_GAP = 10.0
    BESIDE_GAP = 9.0
    CAP_GAP = 1.5
    CAP_LH, NOTE_LH = 4.4, 4.2
    AFTER = 4.5

    def _scene_size(self, pdf: _Pdf, f: SceneFigure) -> tuple[float, float, float]:
        """(width mm, height mm, mm per pt): 1:1 unless the page is narrower."""
        k = 25.4 / 72
        if f.scene.width * k > pdf.epw:
            k = pdf.epw / f.scene.width
        return f.scene.width * k, f.scene.height * k, k

    def _summary_cols(self, f: SceneFigure) -> list[list[str]]:
        if f.summary_beside or len(f.summary) <= 3:
            return [f.summary]
        half = (len(f.summary) + 1) // 2
        return [f.summary[:half], f.summary[half:]]

    def _lines_n(self, pdf: _Pdf, text: str, width: float, style: str, size: float) -> int:
        pdf.set_font(SANS, style, size)
        return len(cast(list[str], pdf.multi_cell(width, 4, T.clean(text), dry_run=True,
                                                  output="LINES")))

    def _bullets_h(self, pdf: _Pdf, items: list[str], width: float) -> float:
        return sum(self._lines_n(pdf, it, width - 4.5, "", self.SUMMARY_PT) * self.SUMMARY_LH
                   + self.SUMMARY_GAP for it in items)

    def _beside_w(self, pdf: _Pdf, f: SceneFigure) -> float:
        fw, _, _ = self._scene_size(pdf, f)
        return min(pdf.epw - fw - self.BESIDE_GAP, 110.0)

    def _summary_h(self, pdf: _Pdf, f: SceneFigure) -> float:
        """Height of the summary block (eyebrow + bullets) where it is drawn."""
        if not f.summary:
            return 0.0
        if f.summary_beside:
            return self.EYEBROW_H + self._bullets_h(pdf, f.summary, self._beside_w(pdf, f))
        cols = self._summary_cols(f)
        col_w = (pdf.epw - self.SUMMARY_COL_GAP * (len(cols) - 1)) / len(cols)
        return self.EYEBROW_H + max(self._bullets_h(pdf, c, col_w) for c in cols)

    def _caption_h(self, pdf: _Pdf, f: SceneFigure) -> float:
        h = self.CAP_GAP + self._lines_n(pdf, f.title, pdf.epw, "I", 8.4) * self.CAP_LH
        if f.note:
            h += self._lines_n(pdf, f.note, pdf.epw, "", 7.8) * self.NOTE_LH
        return h + self.AFTER

    def _scene_need(self, pdf: _Pdf, f: SceneFigure) -> float:
        _, fh, _ = self._scene_size(pdf, f)
        summary = self._summary_h(pdf, f)
        if f.new_page and not f.summary_beside:
            return summary  # the figure itself starts the next page
        body = max(fh, summary) if f.summary_beside else summary + 1.5 + fh
        return body + self._caption_h(pdf, f)

    def _summary_block(self, pdf: _Pdf, cols: list[list[str]], x: float, y: float,
                       col_w: float) -> float:
        pdf.set_xy(x, y)
        pdf.set_font(SANS, "B", 7.4)
        pdf.set_text_color(*T.INK_3_RGB)
        pdf.set_char_spacing(0.6)
        pdf.cell(0, 4, "DESIGN SUMMARY")
        pdf.set_char_spacing(0)
        bottom = y + self.EYEBROW_H
        for ci, items in enumerate(cols):
            cx = x + ci * (col_w + self.SUMMARY_COL_GAP)
            cy = y + self.EYEBROW_H
            for item in items:
                pdf.set_fill_color(*T.IRIS_RGB)
                pdf.rect(cx + 0.3, cy + 1.75, 1.3, 1.3, style="F")
                pdf.set_xy(cx + 4.5, cy)
                pdf.set_font(SANS, "", self.SUMMARY_PT)
                pdf.set_text_color(*T.INK_RGB)
                pdf.multi_cell(col_w - 4.5, self.SUMMARY_LH, T.clean(item), align="L",
                               new_x="LEFT", new_y="NEXT")
                cy = pdf.get_y() + self.SUMMARY_GAP
            bottom = max(bottom, cy)
        return bottom

    def _scene_figure(self, pdf: _Pdf, f: SceneFigure) -> None:
        fw, fh, k = self._scene_size(pdf, f)
        need = self._scene_need(pdf, f)
        # The summary, the figure and its caption share a page, unless the figure is
        # marked to start a page of its own (then the summary stays with its heading).
        no_room = pdf.get_y() + need > pdf.page_break_trigger and need <= pdf.h - TOP - BOTTOM
        if (f.new_page and f.summary_beside) or no_room:
            pdf.add_page()
        y = pdf.get_y()
        if f.summary and not f.summary_beside:
            cols = self._summary_cols(f)
            col_w = (pdf.epw - self.SUMMARY_COL_GAP * (len(cols) - 1)) / len(cols)
            y = self._summary_block(pdf, cols, pdf.l_margin, y, col_w) + 1.5
        if f.new_page and not f.summary_beside:
            pdf.add_page()
            y = pdf.get_y()
        # Drawn without fpdf2's automatic page break: the space was reserved above.
        pdf.set_auto_page_break(False)
        draw_scene(pdf, f.scene, pdf.l_margin, y, scale=k, family=SANS)
        bottom = y + fh
        if f.summary_beside and f.summary:
            bx = pdf.l_margin + fw + self.BESIDE_GAP
            bottom = max(bottom, self._summary_block(pdf, [f.summary], bx, y,
                                                     self._beside_w(pdf, f)))
        pdf.set_auto_page_break(True, margin=BOTTOM)
        pdf.set_xy(pdf.l_margin, bottom + self.CAP_GAP)
        self._figure_caption(pdf, f, pdf.epw)
        if f.note:
            pdf.set_font(SANS, "", 7.8)
            pdf.set_text_color(*T.INK_3_RGB)
            pdf.multi_cell(pdf.epw, self.NOTE_LH, T.clean(f.note), align="L",
                           new_x="LMARGIN", new_y="NEXT")
        pdf.ln(self.AFTER)

    def _figure_caption(self, pdf: _Pdf, f: Figure | SceneFigure, width: float) -> None:
        pdf.set_font(SANS, "I", 8.4)
        pdf.set_text_color(*T.INK_3_RGB)
        pdf.multi_cell(width, 4.4, T.clean(f.title), align="L", new_x="LMARGIN", new_y="NEXT")

    def _figure_row(self, pdf: _Pdf, row: FigureRow) -> None:
        figs = row.figures
        if not figs:
            return
        gap = 10.0
        col_w = (pdf.epw - gap * (len(figs) - 1)) / len(figs)
        sizes = []
        for f in figs:
            w = min(col_w, f.diagram.width_in * 25.4)
            sizes.append((w, w * f.diagram.height / f.diagram.width))
        tallest = max(h for _, h in sizes)
        if pdf.get_y() + tallest + 12 > pdf.page_break_trigger:
            pdf.add_page()
        y0 = pdf.get_y()
        bottom = y0
        for i, (f, (w, h)) in enumerate(zip(figs, sizes, strict=True)):
            x = pdf.l_margin + i * (col_w + gap)
            pdf.image(io.BytesIO(f.diagram.png), x=x, y=y0, w=w, alt_text=T.clean(f.title))
            pdf.set_xy(x, y0 + h + 1.2)
            pdf.set_font(SANS, "I", 8.4)
            pdf.set_text_color(*T.INK_3_RGB)
            pdf.multi_cell(col_w, 4.4, T.clean(f.title), align="L", new_x="LEFT", new_y="NEXT")
            bottom = max(bottom, pdf.get_y())
        pdf.set_xy(pdf.l_margin, bottom + 4.5)


def _versions(values: list[str], limit: int) -> str:
    """The `limit` newest versions LCM offers, plus a count of the rest (N/A when none)."""
    return _shared_versions(values, limit) or NA


__all__ = ["PdfExporter"]
