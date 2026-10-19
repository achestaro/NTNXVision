"""Column widths for Word tables, measured the way Word draws the text.

Word sets report tables in Calibri, which is not bundled, so text is measured with
Calibri's advance widths (per character, in em, from the font's hmtx table) plus a
small slack for Word's rounding. Rules:

* No column is narrower than its longest *value* word (a number, a hostname, a cluster
  name, "(default)"), so Word never breaks "1,280", "6.10.1" or "NYC-PROD-01".
* Header words may wrap at their natural break points: spaces, and after ":" and "/"
  (the header text carries a zero-width break opportunity there). A header token sets
  the minimum only when it is short; a long one wraps.
* When the minimums plus the natural widths do not fit, the shortfall comes from the
  columns that can wrap (free text, several words) before any other.
* When even the minimums do not fit: a smaller font for that table (down to 7 pt);
  then hostnames may break after their dots (a zero-width break opportunity after
  each "." in hostnames only, never at hyphens); only then are columns scaled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Calibri regular advance widths (em), from its hmtx table (2048 units per em).
_CALIBRI: dict[str, float] = {
    " ": 0.226, "\u00a0": 0.226,
    "!": 0.266, '"': 0.320, "#": 0.498, "$": 0.507, "%": 0.715, "&": 0.682,
    "'": 0.176, "(": 0.303, ")": 0.303, "*": 0.498, "+": 0.498, ",": 0.250, "-": 0.306,
    ".": 0.252, "/": 0.386, ":": 0.268, ";": 0.268, "<": 0.498, "=": 0.498, ">": 0.498,
    "?": 0.463, "@": 0.894, "[": 0.307, "\\": 0.386, "]": 0.307, "_": 0.498, "|": 0.460,
    "A": 0.579, "B": 0.544, "C": 0.533, "D": 0.615, "E": 0.488, "F": 0.459, "G": 0.631,
    "H": 0.623, "I": 0.252, "J": 0.319, "K": 0.520, "L": 0.420, "M": 0.855, "N": 0.646,
    "O": 0.662, "P": 0.517, "Q": 0.673, "R": 0.543, "S": 0.459, "T": 0.487, "U": 0.642,
    "V": 0.567, "W": 0.890, "X": 0.519, "Y": 0.487, "Z": 0.468,
    "a": 0.479, "b": 0.525, "c": 0.423, "d": 0.525, "e": 0.498, "f": 0.305, "g": 0.471,
    "h": 0.525, "i": 0.229, "j": 0.239, "k": 0.455, "l": 0.229, "m": 0.799, "n": 0.525,
    "o": 0.527, "p": 0.525, "q": 0.525, "r": 0.349, "s": 0.391, "t": 0.335, "u": 0.525,
    "v": 0.452, "w": 0.715, "x": 0.433, "y": 0.453, "z": 0.395,
}
_DIGIT = 0.507
#: Unknown characters (accents, symbols, CJK) measure as a wide letter.
_OTHER = 0.62
#: Calibri Bold is about 4% wider.
_BOLD = 1.04
#: Room for Word's own rounding, kerning and a substituted font.
SLACK = 1.06

#: A break opportunity Word honours without drawing anything.
ZWSP = "​"
MIN_FONT_PT = 7.0
#: Fonts a table may step down to only to keep its short values whole.
SOFT_FONT_STEPS = (8.0, 7.5)
#: A header token wider than this (pt) may wrap inside itself instead of widening the
#: column ("Provisioned:Usable" breaks after its colon anyway).
HEADER_TOKEN_MAX_PT = 48.0
#: The widest single word a column must hold whole (pt): about 26 capitals at 8.5 pt.
#: Longer ones (synthetic stress names) wrap rather than squeeze every other column.
MAX_WORD_PT = 120.0
#: A cell value this short (characters) is kept whole when the table can afford it:
#: "Local (en-pc01.demo.local)" or "4.0 : 1 (default)" does not wrap at all.
SHORT_VALUE_CHARS = 32
_LIST = re.compile(r"^[^ ,]+(, [^ ,]+)+$")
#: Words up to this width (pt) are never squeezed, even in the last resort.
SHORT_WORD_PT = 60.0
#: A column's natural width is capped (pt): long free text wraps.
MAX_NATURAL_PT = 300.0

_HOSTNAME = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_HEADER_BREAK = re.compile(r"(?<=[:/])")
#: Where a cell may break: spaces, line breaks and break hints, never a no-break space.
_BREAKS = re.compile("[ \t\n\u200b]+")


def text_pt(text: str, size: float, bold: bool = False) -> float:
    """Width (pt) of `text` set in Calibri at `size`, with Word's slack."""
    em = sum(_DIGIT if c.isdigit() else _CALIBRI.get(c, _OTHER) for c in text
             if c != ZWSP)
    return em * size * (_BOLD if bold else 1.0) * SLACK


def word_pt(word: str, size: float, bold: bool = False) -> float:
    """The width a column needs to hold `word` whole; a word wider than MAX_WORD_PT
    (a 40-character stress name) may wrap after its hyphens, so its widest part."""
    w = text_pt(word, size, bold)
    if w <= MAX_WORD_PT or "-" not in word:
        return min(w, MAX_WORD_PT)
    parts = word.split("-")
    return min(max(text_pt(x + ("-" if i < len(parts) - 1 else ""), size, bold)
                   for i, x in enumerate(parts)), MAX_WORD_PT)


def is_hostname(word: str) -> bool:
    return bool(_HOSTNAME.match(word))


def host_breaks(text: str) -> str:
    """`text` with a break opportunity after each dot of its hostnames."""
    return " ".join(w.replace(".", "." + ZWSP) if is_hostname(w) else w
                    for w in text.split(" "))


def header_breaks(text: str) -> str:
    """A header with break opportunities after ":" and "/" (between words already)."""
    return _HEADER_BREAK.sub(ZWSP, text)


def header_tokens(text: str) -> list[str]:
    return [t for w in text.split() for t in _HEADER_BREAK.split(w) if t]


def words(text: str) -> list[str]:
    """The unbreakable pieces of a cell value: split at ordinary spaces only, so a
    no-break space ("2026-09-01 04:00", "1 min") keeps its group whole."""
    return [w for w in _BREAKS.split(text) if w]


def _whole(text: str) -> bool:
    """A value that reads best on one line: short, or a list of identifiers
    ("NYC-PROD-01, NYC-PROD-02")."""
    return len(text) <= SHORT_VALUE_CHARS or bool(_LIST.match(text))


def _keep_part(text: str) -> str:
    """The part of a value its column keeps on one line: the whole value, or the first
    two items of a longer identifier list (it then wraps after an item, at a comma,
    never inside a name)."""
    if _LIST.match(text):
        items = text.split(", ")
        if len(items) > 2:
            return ", ".join(items[:2]) + ","
    return text


def list_lines(text: str, width: float, size: float) -> list[str]:
    """`text` as the lines it is drawn on: one, unless it is an identifier list wider
    than `width` (pt), which is split after whole items ("NYC-PROD-01, NYC-PROD-02," /
    "NYC-DB-01") so Word never breaks a name at its hyphen."""
    if not _LIST.match(text) or text_pt(text, size) <= width:
        return [text]
    items = text.split(", ")
    lines: list[str] = []
    cur = ""
    for i, item in enumerate(items):
        piece = item + ("," if i < len(items) - 1 else "")
        cand = f"{cur} {piece}" if cur else piece
        if cur and text_pt(cand, size) > width:
            lines.append(cur)
            cur = piece
        else:
            cur = cand
    lines.append(cur)
    return lines


@dataclass
class Plan:
    widths: list[float]  # pt, the cell padding included
    font_pt: float
    break_hosts: bool


def _measure(columns: list[tuple[str, list[str]]], size: float, pad: float,
             extras: list[float], break_hosts: bool
             ) -> tuple[list[float], list[float], list[float]]:
    """(natural, soft minimum, hard minimum) per column. Hard: the longest unbreakable
    word. Soft: every short value (and identifier list) whole, which the table gets
    before any free-text column is narrowed."""
    natural, soft, minimum = [], [], []
    for (header, texts), extra in zip(columns, extras, strict=True):
        widest = max([text_pt(x, size) for x in texts] + [text_pt(header, size, True)],
                     default=0.0)
        pieces = [w for x in texts for w in words(x)]
        if break_hosts:
            pieces = [p for w in pieces for p in (w.split(".") if is_hostname(w) else [w])]
        longest = max((word_pt(w, size) for w in pieces), default=0.0)
        head = [text_pt(t, size, True) for t in header_tokens(header)]
        head = [h for h in head if h <= HEADER_TOKEN_MAX_PT]
        floor = min(max([longest, *head], default=0.0), MAX_WORD_PT) + pad + extra
        whole = max((text_pt(_keep_part(x), size) for x in texts if _whole(x)),
                    default=0.0)
        nat = max(min(widest, MAX_NATURAL_PT) + pad + extra, floor)
        minimum.append(floor)
        soft.append(min(max(floor, min(whole, MAX_NATURAL_PT) + pad + extra), nat))
        natural.append(nat)
    return natural, soft, minimum


def plan(columns: list[tuple[str, list[str]]], width: float, *, size: float = 8.5,
         pad: float = 8.5, extras: list[float] | None = None) -> Plan:
    """Widths (pt) for a table `width` pt wide whose columns are (header, cell texts)."""
    extras = extras or [0.0] * len(columns)
    natural, soft, minimum = _measure(columns, size, pad, extras, False)
    font, hosts = size, False
    if sum(minimum) > width:
        # A smaller font for this table, down to 7 pt, before anything else.
        font = size
        while font > MIN_FONT_PT and sum(minimum) > width:
            font = max(MIN_FONT_PT, round(font - 0.5, 2))
            natural, soft, minimum = _measure(columns, font, pad, extras, False)
        if sum(minimum) > width:
            hosts = True
            natural, soft, minimum = _measure(columns, font, pad, extras, True)
    total = sum(natural)
    if total <= width:
        widths = natural if total < width * 0.62 else [n * width / total for n in natural]
        return Plan(widths, font, hosts)
    if sum(soft) > width and not hosts:
        # Up to two half-point steps (not below 7.5 pt) when that keeps every short
        # value whole: "Local (en-pc01.demo.local)" on one line reads better than a
        # larger font that wraps it at its hyphen.
        for step in SOFT_FONT_STEPS:
            if step >= font:
                continue
            n2, s2, m2 = _measure(columns, step, pad, extras, False)
            if sum(s2) <= width:
                natural, soft, minimum, font = n2, s2, m2, step
                total = sum(natural)
                break
    if sum(soft) <= width:
        # Short values and identifier lists stay whole; free text gives up the rest.
        spare, flex = width - sum(soft), total - sum(soft)
        return Plan([s + (n - s) * spare / flex if flex else s
                     for n, s in zip(natural, soft, strict=True)], font, hosts)
    floor = sum(minimum)
    if floor < width:
        # Not every short value fits whole: keep as many whole as possible, cheapest
        # first (an identifier list such as "NYC-PROD-01, NYC-PROD-02" before free
        # text), the rest wrap between words.
        widths = list(minimum)
        spare = width - floor
        for i in sorted(range(len(widths)), key=lambda k: soft[k] - minimum[k]):
            give = min(spare, soft[i] - minimum[i])
            widths[i] += give
            spare -= give
        if spare > 0:  # nothing left to keep whole: free text takes the rest
            flex = sum(n - w for n, w in zip(natural, widths, strict=True))
            if flex > 0:
                widths = [w + (n - w) * spare / flex
                          for n, w in zip(natural, widths, strict=True)]
        return Plan(widths, font, hosts)
    # Last resort (only very long single words left, e.g. synthetic stress names):
    # numbers, dates, versions and short names keep their minimum; the columns holding
    # the long words share what is left, and those words wrap.
    short = [m <= SHORT_WORD_PT + pad for m in minimum]
    keep = sum(m for m, s in zip(minimum, short, strict=True) if s)
    long_total = floor - keep
    room = width - keep
    if long_total and room > 0.25 * long_total:
        return Plan([m if s else m * room / long_total
                     for m, s in zip(minimum, short, strict=True)], font, hosts)
    return Plan([m * width / floor for m in minimum], font, hosts)
