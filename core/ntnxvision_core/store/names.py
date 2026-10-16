"""Snapshot names that are safe as file names on Windows and macOS.

A snapshot's name is its file stem (``<name>.ntnxsnap.json``), so a name that arrives in an
imported snapshot file, written on another OS, must be made safe for this one:

* Windows forbids ``<>:"/\\|?*`` and control characters, reserves device names (``CON``,
  ``NUL``, ``COM1``, ``LPT9``, … with or without an extension), drops trailing dots and
  spaces, and limits a full path to ``MAX_PATH`` (260) characters unless long paths are
  enabled.
* macOS stores names in Unicode NFD on some volumes and Finder shows ``:`` as ``/``.
* Both default to case-insensitive file systems, so ``PC01`` and ``pc01`` collide.

`safe_name` produces a portable stem; `unique_name` adds a ``-2``, ``-3``, … suffix until
it no longer collides, case-insensitively, with the names already taken.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

#: The longest file name suffix the store appends to a stem (``.ntnxsnap.json.tmp``).
_LONGEST_SUFFIX = len(".ntnxsnap.json.tmp")
#: Windows MAX_PATH less the terminating NUL.
_MAX_PATH = 259
#: A stem never exceeds this, whatever the directory (file names cap at 255 on both OSes).
MAX_STEM = 120
#: ...and never drops below this when the directory is deep.
MIN_STEM = 16

_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{dev}{n}" for dev in ("COM", "LPT") for n in [*"123456789", "¹", "²", "³"]
}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


def is_reserved(stem: str) -> bool:
    """True for a Windows device name, which is reserved even with an extension."""
    base = stem.split(".", 1)[0].strip().upper()
    return base in _RESERVED


def max_stem_for(directory: str | Path | None) -> int:
    """The longest stem that keeps ``directory/<stem>.ntnxsnap.json.tmp`` under MAX_PATH."""
    if directory is None:
        return MAX_STEM
    room = _MAX_PATH - len(str(directory)) - 1 - _LONGEST_SUFFIX
    return max(MIN_STEM, min(MAX_STEM, room))


def safe_name(name: str, *, max_len: int = MAX_STEM, fallback: str = "snapshot") -> str:
    """A stem that is a valid file name on Windows and macOS.

    Keeps letters and digits from any script plus ``-_.()`` and spaces (the characters
    the store has always kept), replaces everything else with ``_``, normalises Unicode
    to NFC, trims leading/trailing dots and spaces, and renames device names.
    """
    text = unicodedata.normalize("NFC", str(name or ""))
    text = _ILLEGAL.sub("_", text)
    keep = "-_.() "
    text = "".join(c if c.isalnum() or c in keep else "_" for c in text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if len(text) > max_len:
        text = text[:max_len].rstrip(" .")
    if not text:
        text = fallback
    if is_reserved(text):
        # The device name is what counts ("nul.txt" is NUL too), so mark the base.
        base, dot, rest = text.partition(".")
        text = f"{base.rstrip()}_{dot}{rest}"
    return text


def unique_name(name: str, taken: Iterable[str], *, max_len: int = MAX_STEM) -> str:
    """`name`, or `name-2`, `name-3`, … : the first one not in `taken` (ignoring case)."""
    lowered = {unicodedata.normalize("NFC", t).casefold() for t in taken}
    if name.casefold() not in lowered:
        return name
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = f"{name[: max_len - len(suffix)].rstrip(' .')}{suffix}"
        if candidate.casefold() not in lowered:
            return candidate
        n += 1
