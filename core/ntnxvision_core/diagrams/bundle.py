"""The editable diagrams bundle: ``<report>_diagrams.zip``.

Built from the same report content as the Word and PDF documents (`content.build_report`
with the same options), so every file matches a report figure one to one: the figures
are collected in document order, then written as

* ``<stem>.vsdx``: one Visio page per figure (real shapes, groups, glued connectors);
* ``<stem>.drawio``: one draw.io tab per figure (decision 4: one multi-tab file);
* ``svg/NN-<slug>[-<group>][-part-k].svg``: one SVG per figure, live text;
* ``png/NN-<slug>[-<group>][-part-k].png``: the same figure at 200 dpi, for slides and
  tools that cannot read SVG.

`<stem>` is the zip's own stem (``acme-demo-2026-10-18_diagrams``). Entries are written
in a fixed order with fixed timestamps, so an unchanged snapshot gives identical bytes.
The same files can be written to a folder instead of a zip (``package="folder"``).
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
from .render.png import render_png
from .render.svg import render_svg
from .render.vsdx import ZIP_TIME, render_vsdx
from .scene import Scene

#: The .vsdx font (see `bundle`); draw.io names it first too, the live SVG keeps Figtree.
VSDX_FONT = "Calibri"

#: Short tab names per diagram type (falls back to the type's title).
TAB_NAMES = {"conceptual": "Conceptual", "logical": "Logical", "management": "Management",
             "network": "Network", "dr": "Data protection", "estate": "Estate map",
             "segments": "Segments", "vpc": "Virtual networking",
             "flow": "Microsegmentation"}
_SLUG = re.compile(r"[^a-z0-9]+")
#: Resolution of the bundle's PNG copies.
PNG_DPI = 200
#: PNG bytes per pixel, measured on the synthetic estates at `PNG_DPI` (the export
#: preview's "about" size; the bundle itself always renders).
PNG_BYTES_PER_PX = 0.18
PACKAGES = ("zip", "folder")


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


def bundle_files(scenes: list[Scene], stem: str, created: str | None = None, *,
                 png: bool = True) -> list[tuple[str, bytes]]:
    """The bundle's files in order: (path inside the bundle, bytes)."""
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
    if png:
        files += [(f"png/{file_stem(i, sc)}.png", render_png(sc, PNG_DPI))
                  for i, sc in enumerate(scenes, start=1)]
    return files


def png_estimate(sc: Scene) -> int:
    """About how many bytes the 200 dpi PNG of `sc` takes (no rendering)."""
    px = (sc.width * PNG_DPI / 72) * (sc.height * PNG_DPI / 72)
    return int(round(px * PNG_BYTES_PER_PX / 1024)) * 1024


def bundle_bytes(scenes: list[Scene], stem: str, created: str | None = None) -> bytes:
    """The zip for `scenes`; inner files are named after `stem`."""
    files = bundle_files(scenes, stem, created)
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


def write_bundle(env: Any, options: Any, path: str | Path, package: str = "zip") -> str:
    """Write the bundle for `env` to `path` (a ``.zip``, or with ``package="folder"`` a
    folder holding the same files); returns a short detail ("9 diagrams"). Raises
    ValueError when no diagram applies."""
    path = Path(path)
    scenes = report_scenes(env, options)
    if package == "folder":
        write_folder(bundle_files(scenes, path.name, _created(env)), path)
    else:
        data = bundle_bytes(scenes, path.stem, _created(env))
        tmp = path.with_name(path.name + ".partial")
        tmp.write_bytes(data)
        tmp.replace(path)
    return f"{len(scenes):,} diagram{'s' if len(scenes) != 1 else ''}"


#: Most figures whose PNGs the export preview renders to size them exactly.
PREVIEW_PNG_MAX = 16


def bundle_preview(scenes: list[Scene], stem: str, package: str = "zip",
                   tags: dict[str, list[str]] | None = None,
                   created: str | None = None) -> dict[str, Any]:
    """What the bundle for `scenes` will hold, for the export dialog's preview: its
    entries in order with their exact sizes, and per figure file the `tags` of its
    diagram type ("edited", "stale") when that part draws an edit. PNGs are rendered
    to size them up to `PREVIEW_PNG_MAX` figures; past that their sizes (and the
    bundle's) are left unknown (None) rather than guessed."""
    files = bundle_files(scenes, stem, created, png=False)
    sizes = {name: len(data) for name, data in files}
    tags = tags or {}

    def tagged(sc: Scene) -> list[str]:
        # Only a figure part that draws an edit says so ("(edited)" in its caption,
        # scoped to the part); a part of an edited diagram that no edit touches is
        # exported as generated.
        return tags.get(sc.type, []) if " (edited)" in sc.caption else []

    def figure(folder: str, ext: str, i: int, sc: Scene, size: int | None, approx: bool
               ) -> dict[str, Any]:
        return {"name": f"{file_stem(i, sc)}.{ext}", "kind": ext.upper(),
                "size_bytes": size, "approx": approx, "tags": tagged(sc),
                "path": f"{folder}/{file_stem(i, sc)}.{ext}"}

    svgs = [figure("svg", "svg", i, sc, sizes[f"svg/{file_stem(i, sc)}.svg"], False)
            for i, sc in enumerate(scenes, start=1)]
    exact = len(scenes) <= PREVIEW_PNG_MAX
    pngs = [figure("png", "png", i, sc, len(render_png(sc, PNG_DPI)) if exact else None,
                   False) for i, sc in enumerate(scenes, start=1)]
    png_total = sum(f["size_bytes"] for f in pngs) if exact else None
    n = len(scenes)
    entries = [
        {"name": f"{stem}.vsdx", "kind": "Visio", "detail": f"{n:,} page{'s' * (n != 1)}",
         "size_bytes": sizes[f"{stem}.vsdx"], "approx": False},
        {"name": f"{stem}.drawio", "kind": "draw.io", "detail": f"{n:,} tab{'s' * (n != 1)}",
         "size_bytes": sizes[f"{stem}.drawio"], "approx": False},
        {"name": "svg/", "kind": "SVG", "detail": "Live text", "folder": True,
         "size_bytes": sum(f["size_bytes"] for f in svgs), "approx": False,
         "children": svgs},
        {"name": "png/", "kind": "PNG", "detail": f"{PNG_DPI} dpi", "folder": True,
         "size_bytes": png_total, "approx": False, "children": pngs},
    ]
    total: int | None
    if png_total is None:
        total = None
    elif package == "folder":
        total = sum(int(e["size_bytes"]) for e in entries)
    else:
        # Deflate shrinks the XML files; PNGs are already compressed (stored as is).
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in files:
                z.writestr(name, data)
        total = len(buf.getvalue()) + png_total
    return {"package": package, "count": n, "size_bytes": total,
            "approx": total is not None and package == "zip", "entries": entries}


def write_folder(files: list[tuple[str, bytes]], path: Path) -> None:
    """Write `files` into the new folder `path` (built beside it, then renamed, so a
    failed export never leaves half a folder under the final name)."""
    import shutil

    if path.exists():
        raise FileExistsError(f"{path.name} already exists")
    tmp = path.with_name(path.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    for name, data in files:
        target = tmp / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    tmp.replace(path)


__all__ = ["PACKAGES", "PNG_DPI", "bundle_bytes", "bundle_files", "bundle_preview",
           "file_stem", "png_estimate", "report_scenes", "tab_name", "write_bundle",
           "write_folder"]
