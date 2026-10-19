"""Readable version strings.

Some Nutanix endpoints answer with the full build string instead of the version
(``el8.5-release-ganges-7.5.1.1-stable-<40-hex commit>``). The UI, every report and
Compare show the plain version, so the model shortens these on the way in, which also
fixes snapshots saved before this existed.
"""

from __future__ import annotations

import re

#: ``el8.5-release-<branch>-<version>-stable-…``
_BUILD_STRING = re.compile(r"^el[\d.]*-release-[\w.]+?-(?P<v>(?:pc\.)?\d+(?:\.\d+)+)(?:-|$)", re.I)
#: A long hex commit id, the part that makes build strings unreadable.
_COMMIT = re.compile(r"[0-9a-f]{12,}", re.I)
_VERSION = re.compile(r"(?:pc\.)?\d+(?:\.\d+)+", re.I)


def short_version(value: str | None) -> str | None:
    """The plain version from a Nutanix build string, or the input unchanged.

    ``el8.5-release-ganges-7.5.1.1-stable-0123abcd…`` becomes ``7.5.1.1``. Plain
    versions (``7.5.1.6``, ``pc.2024.3``) and anything unrecognised pass through.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    match = _BUILD_STRING.match(text)
    if match:
        return match.group("v")
    if _COMMIT.search(text):
        found = _VERSION.search(text)
        if found:
            return found.group(0)
    return text


def pc_version(value: str | None) -> str | None:
    """The one display form of a Prism Central version: ``pc.7.5.1.1``, ``pc.2024.3``.

    Every part the API reports is kept (never cut to three), and a numeric 7.x version
    gains the ``pc.`` prefix the calendar builds already carry. The model stores the plain
    value (compat parsing reads it); reports, the UI and the snapshot index show this.
    """
    v = short_version(value)
    if not v:
        return None
    if v[:3].lower() == "pc.":
        return "pc." + v[3:]
    return f"pc.{v}" if v[0].isdigit() else v
