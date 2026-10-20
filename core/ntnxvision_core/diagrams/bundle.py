"""The editable diagrams bundle: ``<report>_diagrams.zip``.

Built from the same report content as the Word and PDF documents (`content.build_report`
with the same options), so every file matches a report figure one to one: the figures
are collected in document order, then written as

* ``<stem>.vsdx``: one Visio page per figure (real shapes, groups, glued connectors);
* ``<stem>.drawio``: one draw.io tab per figure (decision 4: one multi-tab file);
* ``svg/NN-<slug>[-<group>][-part-k].svg``: one SVG per figure, live text.

`<stem>` is the zip's own stem (``acme-demo-2026-10-18_diagrams``). Entries are written
in a fixed order with fixed timestamps, so an unchanged snapshot gives identical bytes.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any

from . import TYPES
from .facts import short_name
from .render.drawio import render_drawio
from .render.svg import render_svg
from .render.vsdx import ZIP_TIME, render_vsdx
from .scene import Scene

#: The .vsdx font (see `bundle`); draw.io names it first too, the live SVG keeps Figtree.
VSDX_FONT = "Calibri"

#: Short tab names per diagram type (falls back to the type's title).
TAB_NAMES = {"conceptual": "Conceptual", "logical": "Logical", "management": "Management",
             "network": "Network", "dr": "Data protection", "estate": "Estate map"}
_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")


def report_scenes(env: Any, options: Any = None) -> list[Scene]:
    """The architecture figures of the report for `env`, in document order."""
    from ntnxvision_core.exporters import ExportOptions
    from ntnxvision_core.exporters.content import SceneFigure, build_report

    report = build_report(env, ExportOptions.coerce(options),
                          eyebrow="Architecture design document")
    out: list[Scene] = []
    for ch in report.chapters:
        for blk in [*ch.blocks, *(b for s in ch.sections for b in s.blocks)]:
            if isinstance(blk, SceneFigure):
                out.append(blk.scene)
    return out


def _group(sc: Scene) -> str:
    name = str(sc.meta.get("group_name") or sc.meta.get("group") or "")
    # "Profile A" reads "Network A" / "host-network-profile-a": the type says profile.
    if name.lower().startswith("profile "):
        name = name[len("profile "):]
    return short_name(name) if name else ""


def file_stem(index: int, sc: Scene) -> str:
    """``03-logical-design-pc02-part-2`` for the `index`-th figure (1-based)."""
    dtype = TYPES.get(sc.type)
    slug = (dtype.slug if dtype and dtype.slug else _slugify(sc.type)) or "diagram"
    parts = [f"{index:02d}", slug]
    if g := _slugify(_group(sc)):
        parts.append(g)
    if sc.part[1] > 1:
        parts.append(f"part-{sc.part[0]}")
    return "-".join(parts)


def tab_name(sc: Scene) -> str:
    """``Logical pc02``, ``Conceptual 2 of 3``: the page / tab name of a figure."""
    dtype = TYPES.get(sc.type)
    base = TAB_NAMES.get(sc.type) or (dtype.title if dtype else sc.title) or "Diagram"
    parts = [base]
    if g := _group(sc):
        parts.append(g)
    if sc.part[1] > 1:
        parts.append(f"{sc.part[0]:,} of {sc.part[1]:,}")
    return " ".join(parts)[:31]


def bundle_bytes(scenes: list[Scene], stem: str, created: str | None = None) -> bytes:
    """The zip for `scenes`; inner files are named after `stem`."""
    if not scenes:
        raise ValueError("No architecture diagrams apply to this snapshot")
    names = [tab_name(sc) for sc in scenes]
    files: list[tuple[str, bytes]] = [
        # Calibri: the font Visio has on every Windows machine; the layout keeps a
        # width reserve for it (text.SAFETY), so text measured in Figtree still fits.
        (f"{stem}.vsdx", render_vsdx(scenes, names, title=stem, created=created,
                                     font=VSDX_FONT)),
        (f"{stem}.drawio", render_drawio(scenes, names)),
    ]
    files += [(f"svg/{file_stem(i, sc)}.svg", render_svg(sc))
              for i, sc in enumerate(scenes, start=1)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files:
            zi = zipfile.ZipInfo(name, ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
    return buf.getvalue()


def _created(env: Any) -> str:
    """The collection time as W3CDTF (UTC), or a fixed time when unknown."""
    from ntnxvision_core.exporters.theme import parse_datetime

    dt = parse_datetime(getattr(env, "collected_at", None))
    if dt is None:
        return "2000-01-01T00:00:00Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def write_bundle(env: Any, options: Any, path: str | Path) -> str:
    """Write the bundle for `env` to `path` (a ``.zip``); returns a short detail
    ("9 diagrams"). Raises ValueError when no diagram applies."""
    path = Path(path)
    scenes = report_scenes(env, options)
    data = bundle_bytes(scenes, path.stem, _created(env))
    tmp = path.with_name(path.name + ".partial")
    tmp.write_bytes(data)
    tmp.replace(path)
    return f"{len(scenes):,} diagram{'s' if len(scenes) != 1 else ''}"


__all__ = ["bundle_bytes", "file_stem", "report_scenes", "tab_name", "write_bundle"]
