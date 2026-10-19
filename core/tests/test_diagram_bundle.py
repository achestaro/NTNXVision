"""The editable diagrams bundle (.vsdx, .drawio, .svg) and the Word SVG embedding.

Every file is built from the report's own figures (`bundle.report_scenes`), so the
checks run on the scenes the Word and PDF documents draw.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pytest

import ntnxvision_core.sidecar as sc
from ntnxvision_core.diagrams import conceptual_scenes
from ntnxvision_core.diagrams.bundle import (
    bundle_bytes,
    file_stem,
    report_scenes,
    tab_name,
    write_bundle,
)
from ntnxvision_core.diagrams.render.drawio import render_drawio
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.diagrams.render.vsdx import page_names, render_vsdx
from ntnxvision_core.diagrams.scene import Scene
from ntnxvision_core.exporters import export_environment
from ntnxvision_core.exporters.naming import diagrams_stem
from ntnxvision_core.models import Environment

from .diagram_helpers import shuffled as _shuffled

V = "{http://schemas.microsoft.com/office/visio/2012/main}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
EM_DASH = chr(0x2014)




def _container_of(sc_: Scene, node_id: str) -> str | None:
    by_id = {n.id: n for n in sc_.nodes}
    seen = {node_id}
    p = by_id[node_id].parent
    while p and p in by_id and p not in seen:
        if by_id[p].kind == "container":
            return p
        seen.add(p)
        p = by_id[p].parent
    return None


# ─── Visio ─────────────────────────────────────────────────────────────


def check_vsdx(data: bytes, scenes: list[Scene]) -> None:
    """OPC structure, well-formed XML, ids, glue and groups of a .vsdx."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    assert len(names) == len(set(names))
    xml = {n: ET.fromstring(z.read(n)) for n in names}  # every part is well formed
    types = xml["[Content_Types].xml"]
    defaults = {d.get("Extension") for d in types.iter(f"{CT}Default")}
    overrides = {o.get("PartName") for o in types.iter(f"{CT}Override")}
    for n in names:
        if n == "[Content_Types].xml":
            continue
        assert f"/{n}" in overrides or n.rsplit(".", 1)[-1] in defaults, n
    for n in names:
        if not n.endswith(".rels"):
            continue
        base = n.replace("_rels/", "").removesuffix(".rels")
        folder = base.rsplit("/", 1)[0] if "/" in base else ""
        for rel in xml[n].iter(f"{PR}Relationship"):
            target = rel.get("Target") or ""
            path = f"{folder}/{target}" if folder else target
            assert path in names, f"{n} -> {target}"
    pages = [p for p in xml["visio/pages/pages.xml"].iter(f"{V}Page")]
    assert len(pages) == len(scenes)
    page_names_ = [p.get("Name") for p in pages]
    assert len(set(page_names_)) == len(page_names_)
    assert all(len(n or "") <= 31 for n in page_names_)
    for i, sc_ in enumerate(scenes, start=1):
        root = xml[f"visio/pages/page{i}.xml"]
        shapes = list(root.iter(f"{V}Shape"))
        ids = [s.get("ID") for s in shapes]
        assert len(ids) == len(set(ids)), f"page {i}: duplicate shape ids"
        for con in root.iter(f"{V}Connect"):
            assert con.get("FromSheet") in ids and con.get("ToSheet") in ids
        by_name = {s.get("NameU"): s for s in shapes}
        for n in sc_.nodes:
            assert n.id in by_name, f"page {i}: {n.id} missing"
            parent = _container_of(sc_, n.id)
            if parent is None:
                continue
            group = by_name[parent]
            assert group.get("Type") == "Group"
            members = {s.get("NameU") for s in group.iter(f"{V}Shape")}
            assert n.id in members, f"page {i}: {n.id} not inside group {parent}"
        glued = [e for e in sc_.edges if e.src and e.dst]
        assert len(list(root.iter(f"{V}Connect"))) == 2 * len(glued)


def test_vsdx_structure_groups_and_glue(design_envs: dict[str, Any]) -> None:
    for name in ("medium", "multi3", "enterprise"):
        scenes = report_scenes(design_envs[name])
        assert scenes, name
        names = [tab_name(s) for s in scenes]
        check_vsdx(render_vsdx(scenes, names, created="2026-10-01T00:00:00Z"), scenes)


def test_page_names_are_unique_and_short() -> None:
    assert page_names([Scene("a", "t", "T", "", 1, 1)] * 3, ["Logical"] * 3) == [
        "Logical", "Logical 2", "Logical 3"]
    long = "x" * 40
    got = page_names([Scene("a", "t", "T", "", 1, 1)] * 2, [long, long])
    assert all(len(n) <= 31 for n in got) and len(set(got)) == 2


def test_libreoffice_opens_the_vsdx(design_envs: dict[str, Any], tmp_path: Path) -> None:
    soffice = shutil.which("soffice") or (
        "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        if Path("/Applications/LibreOffice.app/Contents/MacOS/soffice").exists() else None)
    if not soffice:
        pytest.skip("LibreOffice is not installed")
    scenes = report_scenes(design_envs["medium"])
    src = tmp_path / "medium.vsdx"
    src.write_bytes(render_vsdx(scenes, [tab_name(s) for s in scenes]))
    subprocess.run([soffice, "--headless", f"-env:UserInstallation=file://{tmp_path}/lo",
                    "--convert-to", "pdf", "--outdir", str(tmp_path), str(src)],
                   check=True, capture_output=True, timeout=180)
    pdf = (tmp_path / "medium.pdf").read_bytes()
    assert pdf.startswith(b"%PDF") and len(pdf) > 10_000


# ─── draw.io ───────────────────────────────────────────────────────────


def test_drawio_one_tab_per_figure_with_containers_and_ends(
        design_envs: dict[str, Any]) -> None:
    scenes = report_scenes(design_envs["multi3"])
    root = ET.fromstring(render_drawio(scenes, [tab_name(s) for s in scenes]))
    tabs = root.findall("diagram")
    assert len(tabs) == len(scenes) == int(root.get("pages") or 0)
    assert len({t.get("name") for t in tabs}) == len(tabs)
    for tab, sc_ in zip(tabs, scenes, strict=True):
        cells = {}
        for el in tab.iter():
            if el.tag in ("object", "mxCell") and el.get("id"):
                assert el.get("id") not in cells, el.get("id")
                cells[el.get("id")] = el
        for el in tab.iter("mxCell"):
            for end in ("source", "target"):
                if el.get(end):
                    assert el.get(end) in cells
        # Members of a container are its draw.io children, with relative geometry.
        for n in sc_.nodes:
            parent = _container_of(sc_, n.id)
            if parent is None:
                continue
            obj = next(o for o in tab.iter("object") if o.get("id", "").endswith(f":{n.id}"))
            cell = obj.find("mxCell")
            assert cell is not None and cell.get("parent", "").endswith(f":{parent}")
            containers = [o for o in tab.iter("object")
                          if o.get("id", "").endswith(f":{parent}")]
            assert "container=1" in (containers[0].find("mxCell").get("style") or "")


# ─── SVG ───────────────────────────────────────────────────────────────


def test_svg_outline_draws_glyphs_live_keeps_text(design_envs: dict[str, Any]) -> None:
    sc_ = conceptual_scenes(design_envs["small"])[1][0]
    live, outline = render_svg(sc_), render_svg(sc_, mode="outline")
    for data in (live, outline):
        ET.fromstring(data)
    assert b"<text" in live and b"<text" not in outline
    assert b"<path" in outline and b"<use" not in outline and b"font-family" not in outline
    assert render_svg(sc_, mode="outline") == outline
    with pytest.raises(ValueError):
        render_svg(sc_, mode="bitmap")


# ─── bundle ────────────────────────────────────────────────────────────


def test_bundle_holds_vsdx_drawio_and_one_svg_per_figure(design_envs: dict[str, Any],
                                                         tmp_path: Path) -> None:
    env = design_envs["multi3"]
    out = tmp_path / "acme-multi3-2026-10-18_diagrams.zip"
    detail = write_bundle(env, None, out)
    scenes = report_scenes(env)
    assert detail == f"{len(scenes)} diagram{'s' if len(scenes) != 1 else ''}"
    z = zipfile.ZipFile(out)
    names = z.namelist()
    assert names[:2] == ["acme-multi3-2026-10-18_diagrams.vsdx",
                         "acme-multi3-2026-10-18_diagrams.drawio"]
    svgs = [n for n in names if n.startswith("svg/")]
    assert svgs == [f"svg/{file_stem(i, s)}.svg" for i, s in enumerate(scenes, start=1)]
    assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in z.infolist())
    for n in svgs:
        ET.fromstring(z.read(n))
        assert EM_DASH.encode() not in z.read(n)
    check_vsdx(z.read(names[0]), scenes)
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.parametrize("name", ["medium", "enterprise"])
def test_bundle_bytes_are_deterministic_and_order_free(design_envs: dict[str, Any],
                                                       name: str) -> None:
    env = design_envs[name]
    base = bundle_bytes(report_scenes(env), "x_diagrams", "2026-10-01T00:00:00Z")
    assert bundle_bytes(report_scenes(env), "x_diagrams", "2026-10-01T00:00:00Z") == base
    for seed in (1, 2):
        got = bundle_bytes(report_scenes(_shuffled(env, seed)), "x_diagrams",
                           "2026-10-01T00:00:00Z")
        assert got == base, f"seed {seed}"


def test_no_figure_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No architecture diagrams"):
        bundle_bytes([], "x")
    with pytest.raises(ValueError, match="No architecture diagrams"):
        write_bundle(Environment(), None, tmp_path / "x_diagrams.zip")


def test_export_environment_names_the_bundle(design_envs: dict[str, Any],
                                             tmp_path: Path) -> None:
    path, detail = export_environment(design_envs["small"], "diagrams", tmp_path, "sm-x",
                                      with_detail=True)
    assert path == tmp_path / "sm-x_diagrams.zip" and path.is_file()
    assert detail and detail.endswith(("diagram", "diagrams"))


def test_diagrams_stem_never_overwrites(tmp_path: Path) -> None:
    from datetime import date

    day = date(2026, 10, 18)
    assert diagrams_stem(tmp_path, "demo", "Acme", day) == "Acme-demo-2026-10-18"
    (tmp_path / "Acme-demo-2026-10-18_diagrams.zip").write_bytes(b"")
    assert diagrams_stem(tmp_path, "demo", "Acme", day) == "Acme-demo-2026-10-18-2"


# ─── Word ──────────────────────────────────────────────────────────────


def test_word_figures_carry_an_svg_alternative(design_envs: dict[str, Any],
                                               tmp_path: Path) -> None:
    path = export_environment(design_envs["small"], "word", tmp_path, "small")
    z = zipfile.ZipFile(path)
    doc = z.read("word/document.xml").decode()
    rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels.iter(f"{PR}Relationship")}
    blips = ET.fromstring(doc).iter(
        "{http://schemas.openxmlformats.org/drawingml/2006/main}blip")
    r_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    svg_ns = "{http://schemas.microsoft.com/office/drawing/2016/SVG/main}"
    pairs = []
    for blip in blips:
        svg = blip.find(f".//{svg_ns}svgBlip")
        if svg is not None:
            pairs.append((targets[blip.get(f"{r_ns}embed") or ""],
                          targets[svg.get(f"{r_ns}embed") or ""]))
    figures = len(report_scenes(design_envs["small"]))
    assert len(pairs) == figures > 0
    for png, svg in pairs:
        assert png.endswith(".png") and svg.endswith(".svg")
        data = z.read(f"word/{svg}")
        assert b"<path" in data and b"<text" not in data
    types = z.read("[Content_Types].xml").decode()
    assert "image/svg+xml" in types


# ─── sidecar ───────────────────────────────────────────────────────────


def test_sidecar_export_writes_the_bundle_next_to_the_reports(demo: Any,
                                                              data_dir: Path) -> None:
    run = sc._m_inventory(None, {
        "host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
        "insecure": False, "name": "pc01-diagrams", "formats": [],
        "export_dir": str(data_dir / "exports")})
    out = data_dir / "exp"
    res = sc._m_export(None, {"snapshot": run["snapshot_path"], "formats": ["pdf"],
                              "export_dir": str(out), "file_prefix": "Acme",
                              "diagrams": True})
    assert res["errors"] == {}
    bundle = res["diagrams"]
    assert bundle and bundle["name"].startswith("Acme-pc01-diagrams-")
    assert bundle["name"].endswith("_diagrams.zip") and Path(bundle["path"]).parent == out
    assert bundle["size_bytes"] > 0 and bundle["detail"].endswith(("diagram", "diagrams"))
    assert Path(res["paths"]["pdf"]).stem + "_diagrams.zip" == bundle["name"]
    # Off by default; history only records the report formats.
    again = sc._m_export(None, {"snapshot": run["snapshot_path"], "formats": ["pdf"],
                                "export_dir": str(out)})
    assert again["diagrams"] is None
    detail = sc._m_get_snapshot(None, {"snapshot": run["snapshot_path"]})
    assert {e["format"] for e in detail["exports"]} == {"pdf"}
