"""One rule for text outputs that a spreadsheet may open (CSV, TSV).

A string starting with ``= + - @``, TAB, CR, LF or the fullwidth forms ``＝ ＋ － ＠`` can be
read as a formula (or smuggle one past a naive check), so it is prefixed with an
apostrophe. Numbers (int / float) are never touched: only strings. The renderer mirrors
this in ``app/src/renderer/lib/csv.ts``; change both together.
"""

from __future__ import annotations

from typing import Any

#: Leading characters that make a spreadsheet treat a cell as a formula.
TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")


def neutralize(value: Any) -> Any:
    """Prefix a hostile string with an apostrophe; leave numbers and other types alone."""
    if isinstance(value, str) and value.startswith(TRIGGERS):
        return "'" + value
    return value
