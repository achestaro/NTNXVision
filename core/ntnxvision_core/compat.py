"""Version compatibility — supported-range detection for Prism Central & AOS.

NTNXVision officially supports:
  * Prism Central : pc.2024.1  →  7.5.1.6  (the newest builds moved to a 7.x scheme)
  * AOS (cluster) : 6.5        →  7.5      (direct Prism Element mode)

Nutanix ships two version schemes in this window:
  * Legacy calendar builds: ``pc.2024.1``, ``pc.2024.3`` …
  * New semantic builds:    ``7.0``, ``7.3``, ``7.5.1.6`` …

Both are parsed into a comparable tuple. The policy is **warn but proceed**: versions
outside the supported window are flagged with a human-readable warning but never block
collection (older/newer builds usually still work, just with possible field gaps).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Supported windows as comparable numeric tuples.
PC_MIN = (2024, 1)  # pc.2024.1
PC_MAX = (7, 5, 1, 6)  # 7.5.1.6
AOS_MIN = (6, 5)  # AOS 6.5
AOS_MAX = (7, 5)  # AOS 7.5 (family; patch levels above are fine)

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")


@dataclass
class CompatResult:
    raw: str | None
    parsed: tuple[int, ...] | None
    scheme: str  # "calendar" | "semantic" | "unknown"
    in_range: bool
    warning: str | None = None


def parse_version(raw: str | None) -> tuple[tuple[int, ...] | None, str]:
    """Parse a PC/AOS version string into a comparable tuple and its scheme.

    Examples:
      'pc.2024.3'   -> ((2024, 3), 'calendar')
      '2024.1.0.1'  -> ((2024, 1, 0, 1), 'calendar')
      '7.5.1.6'     -> ((7, 5, 1, 6), 'semantic')
      'el8.nutanix' -> (None, 'unknown')
    """
    if not raw:
        return None, "unknown"
    match = _VERSION_RE.search(raw)
    if not match:
        return None, "unknown"
    parts = tuple(int(p) for p in match.group(1).split("."))
    if not parts:
        return None, "unknown"
    scheme = "calendar" if parts[0] >= 2000 else "semantic"
    return parts, scheme


def _is_calendar(parsed: tuple[int, ...]) -> bool:
    return bool(parsed) and parsed[0] >= 2000


def check_prism_central(raw: str | None) -> CompatResult:
    """Assess a Prism Central version against the supported window."""
    parsed, scheme = parse_version(raw)
    if parsed is None:
        return CompatResult(raw, None, scheme, in_range=True,
                            warning=f"Could not parse Prism Central version '{raw}'; "
                                    "proceeding without a compatibility check.")

    # Below the calendar floor (pre-2024.1) → below range.
    if _is_calendar(parsed):
        if parsed < PC_MIN:
            return CompatResult(
                raw, parsed, scheme, in_range=False,
                warning=f"Prism Central {raw} is below the supported floor "
                        "(pc.2024.1). Collection will proceed but some fields may be "
                        "missing or shaped differently.",
            )
        # Calendar builds 2024.1+ are in range (they predate the 7.x switch).
        return CompatResult(raw, parsed, scheme, in_range=True)

    # Semantic builds (7.x). Anything 7.0+ is in range; above the tested
    # ceiling we warn (forward-compatible) but still proceed.
    if parsed > PC_MAX:
        return CompatResult(
            raw, parsed, scheme, in_range=True,
            warning=f"Prism Central {raw} is newer than the tested ceiling "
                    "(7.5.1.6). Collection will proceed; newer builds are usually "
                    "backward-compatible with the v3 API used here.",
        )
    if parsed < (7, 0):
        # A semantic build below 7.0 shouldn't occur in this window, but handle it.
        return CompatResult(
            raw, parsed, scheme, in_range=False,
            warning=f"Prism Central {raw} is below the supported range. Proceeding "
                    "with best effort.",
        )
    return CompatResult(raw, parsed, scheme, in_range=True)


def check_aos(raw: str | None) -> CompatResult:
    """Assess an AOS (cluster) version against the supported window (6.5–7.5)."""
    parsed, scheme = parse_version(raw)
    if parsed is None:
        return CompatResult(raw, None, scheme, in_range=True,
                            warning=f"Could not parse AOS version '{raw}'; proceeding "
                                    "without a compatibility check.")
    major_minor = parsed[:2]
    if major_minor < AOS_MIN:
        return CompatResult(
            raw, parsed, scheme, in_range=False,
            warning=f"AOS {raw} is below the supported floor (6.5). Collection will "
                    "proceed but some fields may be missing.",
        )
    if major_minor > AOS_MAX:
        return CompatResult(
            raw, parsed, scheme, in_range=True,
            warning=f"AOS {raw} is newer than the tested ceiling (7.5). Collection "
                    "will proceed; newer builds are usually backward-compatible.",
        )
    return CompatResult(raw, parsed, scheme, in_range=True)
