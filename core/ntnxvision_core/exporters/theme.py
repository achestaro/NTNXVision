"""Report theme shared by the PDF, Word and Excel exporters: a light Runway treatment.

The reports follow the app's Runway design system, applied conservatively: white pages and
dark ink, cool grey rules, and the iris accent only on small touches (the cover band, the
heading marker, the table header rule, links and the table of contents). Colour otherwise
means one of three statuses (ok / warn / error), shown as a small square glyph beside an
ink label, never as a fill. Charts use the Runway chart palette in its fixed order.

Fonts: the PDF embeds Figtree (body), Montserrat Light (figures) and JetBrains Mono
(numbers, versions, addresses), vendored under ``fonts/`` with their OFL licenses. Word
and Excel cannot embed fonts reliably, so they use Calibri (falling back to the platform
sans-serif), which sits close to Figtree's proportions.

This module also owns the small text rules every format shares: no em dashes, "N/A" for a
missing value, UTC timestamps as ``yyyy-mm-dd hh:mm``, and the status vocabulary.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ntnxvision_core.numfmt import NA

RGB = tuple[int, int, int]


def _rgb(hex_color: str) -> RGB:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ─── palette (hex without '#', as openpyxl / python-docx want it) ──────────────

INK = "0F1A24"
INK_2 = "3E4C59"
INK_3 = "5B6976"
RULE = "C9D0D6"
RULE_2 = "DEE3E7"
SURFACE = "FAFBFB"
BODY = "ECEEF0"
#: Table header fill: a very light neutral, so the iris rule beneath it is the only colour.
HEADER_FILL = "F3F4F6"
IRIS = "5B3FD9"
IRIS_TINT = "EAE8F8"
PRISM = "1A68BD"

OK = "197746"
WARN = "995800"
ERROR = "B3263A"
LIVE = "0A7283"

#: Runway chart series, in order. Never reassigned per render.
CHART = ("3370C7", "B38C15", "B54879", "8A4700", "894B9C", "C65D26", "3899C5", "648828")

STATUS_HEX = {"ok": OK, "warn": WARN, "error": ERROR}

INK_RGB, INK_2_RGB, INK_3_RGB = _rgb(INK), _rgb(INK_2), _rgb(INK_3)
RULE_RGB, RULE_2_RGB = _rgb(RULE), _rgb(RULE_2)
SURFACE_RGB, BODY_RGB, HEADER_FILL_RGB = _rgb(SURFACE), _rgb(BODY), _rgb(HEADER_FILL)
IRIS_RGB, IRIS_TINT_RGB = _rgb(IRIS), _rgb(IRIS_TINT)
STATUS_RGB = {k: _rgb(v) for k, v in STATUS_HEX.items()}
CHART_RGB = tuple(_rgb(c) for c in CHART)

# ─── fonts ─────────────────────────────────────────────────────────────────────

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FIGTREE = FONT_DIR / "Figtree-Regular.ttf"
FIGTREE_ITALIC = FONT_DIR / "Figtree-Italic.ttf"
FIGTREE_SEMIBOLD = FONT_DIR / "Figtree-SemiBold.ttf"
MONTSERRAT_LIGHT = FONT_DIR / "Montserrat-Light.ttf"
JETBRAINS_MONO = FONT_DIR / "JetBrainsMono-Regular.ttf"

#: Word / Excel body font. Calibri ships with every Office install and LibreOffice maps it
#: to the metric-compatible Carlito; both read close to Figtree.
OFFICE_FONT = "Calibri"
#: Word / Excel figures and titles also use Calibri: "Calibri Light" has no metric twin
#: in LibreOffice and falls back to a serif face there.

# ─── text rules ────────────────────────────────────────────────────────────────

# Built with chr() so the sanitiser itself is not a string literal the em-dash guard flags.
_EM_DASH = chr(0x2014)
_ISO_DT = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")


def clean(text: Any) -> str:
    """A string safe for a report: never an em dash (data from older snapshots or the API
    can carry one), never the literal "None"."""
    if text is None:
        return ""
    s = str(text)
    if _EM_DASH in s:
        s = s.replace(f" {_EM_DASH} ", ", ").replace(_EM_DASH, "-")
    return s


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def parse_datetime(value: Any) -> datetime | None:
    """An ISO 8601 timestamp string as a naive UTC datetime, else None."""
    if not isinstance(value, str):
        return None
    m = _ISO_DT.match(value.strip())
    if not m:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def fmt_datetime(value: Any, suffix: bool = False) -> str | None:
    """``2026-10-01 09:00`` (UTC) for a timestamp, ``2027-11-05`` for a midnight date.

    Returns None when `value` is not a timestamp, so callers can fall through.
    """
    dt = parse_datetime(value)
    if dt is None:
        return None
    if (dt.hour, dt.minute, dt.second) == (0, 0, 0):
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M") + (" UTC" if suffix else "")


def collected_label(env: Any) -> str:
    """When the inventory was collected, for covers and summaries."""
    return fmt_datetime(env.collected_at, suffix=True) or clean(env.collected_at) or NA


def report_date() -> str:
    """The date the report file is produced (local), distinct from the collection time."""
    return datetime.now().strftime("%Y-%m-%d")


def power_label(value: Any) -> str | None:
    """VM power state as a sentence-case label ("On" / "Off"); APIs mix ON, on, POWERED_ON."""
    if is_missing(value):
        return None
    v = str(value).strip().upper()
    if v in ("ON", "POWERED_ON"):
        return "On"
    if v in ("OFF", "POWERED_OFF"):
        return "Off"
    return str(value).replace("_", " ").capitalize()


def on_off(value: Any) -> str:
    """Normalise mixed-case feature switches (NONE / off / on / POST_PROCESS)."""
    if is_missing(value):
        return "Off"
    v = str(value).strip()
    if v.upper() in ("NONE", "OFF", "FALSE", "DISABLED"):
        return "Off"
    if v.upper() in ("ON", "TRUE", "ENABLED", "INLINE"):
        return "On"
    return v.replace("_", " ").capitalize()


def plural(n: int, singular: str, plural_form: str | None = None) -> str:
    return f"{n:,} {singular if n == 1 else (plural_form or singular + 's')}"


# ─── status vocabulary ─────────────────────────────────────────────────────────
# Only real status cells carry a colour: alert severity, licence compliance, LCM update
# availability, availability-zone connection, collection outcome and the v3 sunset verdict.

_TONES: dict[str, dict[str, str]] = {
    "severity": {"CRITICAL": "error", "WARNING": "warn"},
    "compliance": {"COMPLIANT": "ok", "EXPIRED": "error", "NON_COMPLIANT": "error",
                   "NONCOMPLIANT": "error", "EXPIRING": "warn", "EXPIRING_SOON": "warn"},
    "update available": {"YES": "warn", "NO": "ok"},
    "update": {"YES": "warn", "NO": "ok"},
    "connection": {"CONNECTED": "ok", "REACHABLE": "ok", "DISCONNECTED": "error",
                   "UNREACHABLE": "error"},
    "outcome": {"COLLECTED": "ok", "COLLECTED WITH NOTES": "warn", "FAILED": "error"},
    "after q4 2026 v3 sunset": {"UNAFFECTED": "ok", "MOVES TO V4 AFTER A PC UPGRADE": "ok",
                                "STOPS COLLECTING": "warn"},
}


def tone_for(header: str | None, value: Any) -> str | None:
    """The status tone ("ok" / "warn" / "error") of a cell, or None for a plain cell."""
    if header is None or is_missing(value) or isinstance(value, (int, float)):
        return None
    table = _TONES.get(header.strip().lower())
    if not table:
        return None
    return table.get(str(value).strip().upper())


__all__ = [
    "CHART", "CHART_RGB", "HEADER_FILL", "INK", "INK_2", "INK_3", "IRIS", "OFFICE_FONT",
    "OK", "RULE", "RULE_2", "STATUS_HEX", "WARN", "ERROR", "clean", "collected_label",
    "fmt_datetime", "is_missing", "on_off", "parse_datetime", "plural", "power_label",
    "report_date", "tone_for",
]
