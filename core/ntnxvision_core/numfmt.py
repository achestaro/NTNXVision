"""Number formatting for everything the core shows to users.

Counts and sizes use comma thousands grouping ("12,000", "1,214.5") everywhere: UI
detail strings, report narrative and PDF/Word table cells. Identifier-like numbers
(VLAN IDs, ports, years, version numbers) are never grouped: callers keep those out of
`fmt_count`, and table renderers skip the columns named in `PLAIN_HEADERS`.
"""

from __future__ import annotations

from typing import Any

#: The placeholder for a missing or not-applicable value, everywhere the core shows
#: one: UI detail strings, compare/inventory cells, and PDF/Word/Excel reports.
NA = "N/A"

#: Table column headers whose numeric values are identifiers, not quantities.
PLAIN_HEADERS = frozenset({"VLAN", "VLAN ID", "Port", "Ports", "Year"})

#: openpyxl number formats for quantity cells.
EXCEL_INT_FORMAT = "#,##0"
EXCEL_DECIMAL_FORMAT = "#,##0.00"


def fmt_count(n: Any) -> str:
    """A quantity with comma thousands grouping: 1214 → "1,214", 1234.5 → "1,234.5".

    None renders as `NA` ("N/A"); anything that is not a number (already-formatted strings,
    placeholders) passes through unchanged as a string.
    """
    if n is None:
        return NA
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        return str(n)
    return f"{n:,}"


def cell_text(value: Any, header: str | None = None) -> str:
    """A table cell as text: quantities grouped, identifier columns left alone."""
    if value is None:
        return ""
    if header in PLAIN_HEADERS:
        return str(value)
    return fmt_count(value)


def excel_number_format(value: Any) -> str | None:
    """The `#,##0` / `#,##0.00` format for a numeric quantity cell, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return EXCEL_INT_FORMAT
    if isinstance(value, float):
        return EXCEL_DECIMAL_FORMAT
    return None


__all__ = ["NA", "PLAIN_HEADERS", "cell_text", "excel_number_format", "fmt_count"]
