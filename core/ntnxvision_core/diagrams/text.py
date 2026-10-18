"""Deterministic text measurement from the bundled Figtree fonts (fontTools, no FreeType).

Widths are the fonts' ``hmtx`` advances, identical on every platform and independent of
hinting or DPI. Kerning is ignored on purpose: every renderer that places glyphs itself
(fpdf2 without text shaping, Pillow's BASIC layout) ignores it too, so measurement and
drawing agree. Fitting reserves a small safety margin (`SAFETY`) so a viewer that
substitutes its own font in an editable copy (Calibri, Segoe UI) still fits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

from ntnxvision_core.exporters import theme as T

from .scene import q

ELLIPSIS = "…"
#: Extra width reserved when fitting, for a substituted font in editable copies.
SAFETY = 1.06
#: Line height as a multiple of the font size.
LINE_GAP = 1.28
#: The smallest text anywhere in a diagram (pt). Content splits into parts instead.
MIN_SIZE = 6.0


@dataclass(frozen=True)
class FontMetrics:
    upm: int
    ascent: float  # fraction of the em
    descent: float  # fraction of the em (positive)
    advances: dict[int, int]
    default_adv: int

    def width(self, text: str, size: float) -> float:
        adv = self.advances
        return sum(adv.get(ord(ch), self.default_adv) for ch in text) * size / self.upm


@lru_cache(maxsize=2)
def metrics(bold: bool) -> FontMetrics:
    f = TTFont(str(T.FIGTREE_SEMIBOLD if bold else T.FIGTREE), lazy=True)
    hmtx = f["hmtx"].metrics
    cmap = f.getBestCmap()
    upm = f["head"].unitsPerEm
    hhea = f["hhea"]
    return FontMetrics(upm=upm, ascent=hhea.ascent / upm, descent=-hhea.descent / upm,
                       advances={cp: hmtx[g][0] for cp, g in cmap.items()},
                       default_adv=hmtx[".notdef"][0])


def natural(text: str, size: float, bold: bool = False) -> float:
    """The drawn width of `text` (no safety margin)."""
    return q(metrics(bold).width(text, size))


def width(text: str, size: float, bold: bool = False) -> float:
    """The width `text` needs when fitting (with the safety margin)."""
    return q(metrics(bold).width(text, size) * SAFETY)


def line_height(size: float) -> float:
    return q(size * LINE_GAP)


def baseline(size: float) -> float:
    """Baseline offset inside a line box of `line_height(size)` (text centred)."""
    m = metrics(False)
    lh = size * LINE_GAP
    return q((lh - (m.ascent + m.descent) * size) / 2 + m.ascent * size)


def _break_word(word: str, size: float, bold: bool, max_w: float) -> list[str]:
    parts: list[str] = []
    cur = ""
    for ch in word:
        if cur and width(cur + ch, size, bold) > max_w:
            parts.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


def ellipsize(text: str, size: float, bold: bool, max_w: float) -> str:
    if width(text, size, bold) <= max_w:
        return text
    while text and width(text.rstrip() + ELLIPSIS, size, bold) > max_w:
        text = text[:-1]
    return text.rstrip() + ELLIPSIS


def wrap(text: str, size: float, bold: bool, max_w: float, max_lines: int) -> list[str]:
    """Greedy word wrap. Lines break at spaces, then after hyphens (long cluster names
    such as ``EMEA-FRANKFURT-DC2-PROD-01`` break between their parts), and only then
    inside a part; the last kept line is ellipsised."""
    # (separator before the piece, piece)
    pieces: list[tuple[str, str]] = []
    for wi, w in enumerate(text.split()):
        parts = re.findall(r"[^-]+-*|-+", w) if width(w, size, bold) > max_w else [w]
        for pi, part in enumerate(parts):
            sep = (" " if wi else "") if pi == 0 else ""
            chunks = ([part] if width(part, size, bold) <= max_w
                      else _break_word(part, size, bold, max_w))
            for ci, chunk in enumerate(chunks):
                pieces.append((sep if ci == 0 else "", chunk))
    lines: list[str] = []
    joins: list[str] = []  # the separator dropped at each line break
    cur = ""
    for sep, piece in pieces:
        cand = f"{cur}{sep}{piece}" if cur else piece
        if width(cand, size, bold) <= max_w:
            cur = cand
        else:
            if cur:
                lines.append(cur)
                joins.append(sep)
            cur = piece
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        rest = lines[max_lines - 1]
        for j, ln in zip(joins[max_lines - 1:], lines[max_lines:], strict=False):
            rest += j + ln
        lines = lines[: max_lines - 1] + [ellipsize(rest, size, bold, max_w)]
    return lines or [""]


def fit(text: str, size: float, bold: bool, max_w: float, max_lines: int = 1,
        min_size: float | None = None) -> tuple[float, list[str]]:
    """Shrink in 0.25 pt steps (never below `min_size`, never below `MIN_SIZE`) before
    wrapping past `max_lines`; ellipsise what still does not fit. Returns (size, lines)."""
    floor = max(MIN_SIZE, min_size if min_size is not None else size)
    s = size
    while True:
        lines = wrap(text, s, bold, max_w, max_lines=10_000)
        if len(lines) <= max_lines or s - 0.25 < floor - 1e-9:
            break
        s = q(s - 0.25)
    return s, wrap(text, s, bold, max_w, max_lines)
