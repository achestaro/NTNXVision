"""Report file names: ``<prefix>-<snapshot name>-<export date>.<ext>``.

Every report the app writes (a collection's reports, a re-export from Snapshots or
Results, the Compare "Export changes" file) is named here, so the rule lives in one
place:

* the optional user prefix comes first, sanitized for Windows and macOS (reserved
  characters and device names, trailing dots and spaces, a length cap);
* then the snapshot name (already a safe stem when it comes from the store, sanitized
  again in case it does not);
* then the export date as ``YYYY-MM-DD`` (local time, the date style of default
  snapshot names);
* an existing file is never overwritten: ``-2``, ``-3``, ... is appended, matching how
  snapshot files and ``.ntnxsnap`` exports avoid collisions (case-insensitively, as
  both file systems default to).

The renderer mirrors `sanitize_prefix` for its live preview (``app/src/renderer/lib/format.ts``).
"""

from __future__ import annotations

import contextlib
from datetime import date
from pathlib import Path

from ntnxvision_core.store.names import is_reserved, safe_name, unique_name

#: The longest prefix kept (characters, after sanitizing).
PREFIX_MAX = 40
#: Windows MAX_PATH less the terminating NUL.
_MAX_PATH = 259
#: Never cut a stem shorter than this, however deep the folder.
_MIN_STEM = 24
#: A stem never exceeds this (file names cap at 255 on both OSes).
_MAX_STEM = 200
#: Room kept for a "-NN" collision suffix.
_SUFFIX_ROOM = 4


def sanitize_prefix(prefix: str | None) -> str:
    """The prefix as it will appear in file names, or "" for none.

    Reserved characters become ``_``; surrounding spaces, dots, dashes and underscores
    are trimmed (the ``-`` separator follows it); it is capped at `PREFIX_MAX`.
    """
    text = safe_name((prefix or "").strip(" .-_"), max_len=PREFIX_MAX, fallback="")
    text = text.strip(" .-_")
    # Trimming can expose a device name again ("CON_" from "CON"): mark it once more.
    return safe_name(text, max_len=PREFIX_MAX, fallback="") if is_reserved(text) else text


def export_date(on: date | None = None) -> str:
    """The export date part of a file name: ``YYYY-MM-DD`` (local date)."""
    return (on or date.today()).isoformat()


def report_stem(snapshot_name: str, prefix: str | None = None, on: date | None = None, *,
                max_len: int = _MAX_STEM) -> str:
    """``<prefix>-<snapshot_name>-<YYYY-MM-DD>``, or without the prefix when it is empty.

    When the stem would exceed `max_len`, the snapshot name is shortened; the prefix and
    the date are always kept whole.
    """
    pre = sanitize_prefix(prefix)
    day = export_date(on)
    name = safe_name(snapshot_name or "", fallback="report")
    fixed = len(day) + 1 + (len(pre) + 1 if pre else 0)
    room = max(1, max_len - fixed)
    if len(name) > room:
        name = name[:room].rstrip(" .-_") or "report"
    return "-".join(p for p in (pre, name, day) if p)


def _max_stem_for(directory: Path, ext: str) -> int:
    room = _MAX_PATH - len(str(directory)) - 1 - len(ext) - 1 - _SUFFIX_ROOM
    return max(_MIN_STEM, min(_MAX_STEM, room))


def unique_report_path(directory: str | Path, stem: str, ext: str) -> Path:
    """``directory/<stem>.<ext>``, suffixed ``-2``, ``-3``, ... when that file exists."""
    folder = Path(directory)
    ext = ext.lstrip(".")
    suffix = f".{ext}".casefold()
    try:
        taken = [p.name[: -len(suffix)] for p in folder.iterdir()
                 if p.name.casefold().endswith(suffix)]
    except OSError:
        taken = []
    return folder / f"{unique_name(stem, taken, max_len=_MAX_STEM + _SUFFIX_ROOM)}.{ext}"


def report_path(directory: str | Path, snapshot_name: str, ext: str,
                prefix: str | None = None, on: date | None = None) -> Path:
    """The full, collision-free path of one report file."""
    folder = Path(directory)
    stem = report_stem(snapshot_name, prefix, on, max_len=_max_stem_for(folder, ext))
    return unique_report_path(folder, stem, ext)


#: The editable diagrams bundle is ``<report stem>_diagrams.zip``.
DIAGRAMS_SUFFIX = "_diagrams"


def diagrams_stem(directory: str | Path, snapshot_name: str, prefix: str | None = None,
                  on: date | None = None) -> str:
    """The report stem for a diagrams bundle: neither ``<stem>_diagrams.zip`` nor a
    ``<stem>_diagrams`` folder exists yet (``-2``, ``-3``, ... is added to the stem, as
    for the other reports)."""
    folder = Path(directory)
    room = _max_stem_for(folder, "zip") - len(DIAGRAMS_SUFFIX)
    stem = report_stem(snapshot_name, prefix, on, max_len=max(_MIN_STEM, room))
    taken: list[str] = []
    for suffix in (f"{DIAGRAMS_SUFFIX}.zip".casefold(), DIAGRAMS_SUFFIX.casefold()):
        with contextlib.suppress(OSError):
            taken += [p.name[: -len(suffix)] for p in folder.iterdir()
                      if p.name.casefold().endswith(suffix)]
    return unique_name(stem, taken, max_len=_MAX_STEM + _SUFFIX_ROOM)


__all__ = ["DIAGRAMS_SUFFIX", "PREFIX_MAX", "diagrams_stem", "export_date", "report_path",
           "report_stem", "sanitize_prefix", "unique_report_path"]
