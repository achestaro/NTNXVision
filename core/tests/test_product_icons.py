"""Nutanix product icons: the registry, the diagram badges and product cards, every
figure format, and the PDF / Word / Excel reports."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from PIL import Image

from ntnxvision_core.diagrams import badges, check_scene
from ntnxvision_core.diagrams import icons as I
from ntnxvision_core.diagrams.bundle import report_scenes
from ntnxvision_core.diagrams.render.drawio import render_drawio
from ntnxvision_core.diagrams.render.pdf import draw_scene
from ntnxvision_core.diagrams.render.png import render_png
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.diagrams.render.vsdx import render_vsdx
from ntnxvision_core.diagrams.scene import Scene, from_doc, to_doc
from ntnxvision_core.exporters import export_environment, sections
from ntnxvision_core.exporters import products as P


def test_every_product_has_its_square_icon() -> None:
    for pid, product in P.PRODUCTS.items():
        assert product.svg.is_file() and product.png.is_file(), pid
        art = P.art(pid)
        assert art.viewbox[2] == art.viewbox[3] and art.paths
        with Image.open(product.png) as im:
            assert im.size == (256, 256) and im.mode == "RGBA"
    assert (P.ICON_DIR / "README.md").is_file()
    # Files is part of Nutanix Unified Storage.
    assert P.for_section("nus") is P.PRODUCTS["nus"]
    assert P.for_section("ndb") is P.PRODUCTS["ndb"]
    assert P.for_section("prism_central") is P.PRODUCTS["prism"]
    assert P.for_section("automation") is P.PRODUCTS["ncm"]


def test_section_and_sheet_mappings_name_real_sections() -> None:
    assert set(P.SECTION_PRODUCT) <= set(sections.BY_ID)
    assert set(P.SECTION_PRODUCT.values()) <= set(P.PRODUCTS)
    assert P.for_sheet("file_servers") is P.PRODUCTS["nus"]
    assert P.for_sheet("databases") is P.PRODUCTS["ndb"]
    assert P.for_sheet("clusters") is P.PRODUCTS["nci"]
    for fixed in ("executive_summary", "assumptions", "appendix"):
        assert P.for_section(fixed) is None


def test_products_in_estate(design_envs: dict[str, Any]) -> None:
    env = design_envs["medium"]
    ids = [p.id for p, _ in P.in_estate(env)]
    assert ids[:2] == ["prism", "nci"]
    assert all(detail for _, detail in P.in_estate(env))
    assert ids == sorted(ids, key=list(P.PRODUCTS).index)


# ─── diagrams ──────────────────────────────────────────────────────────


def _glyphs(sc: Scene) -> list[Any]:
    return [n for n in sc.nodes if n.kind == badges.KIND]


def test_pc_blocks_and_cluster_cards_get_badges(design_envs: dict[str, Any]) -> None:
    scenes = report_scenes(design_envs["medium"])
    by_type: dict[str, set[str]] = {}
    for sc in scenes:
        assert check_scene(sc) == [], sc.id
        for g in _glyphs(sc):
            parent = sc.node(g.parent)
            assert badges.product_of(parent) == g.data["product"]
            # Inside its element, clear of the element's text.
            assert parent.x <= g.x and g.x + g.w <= parent.x + parent.w
            assert parent.y <= g.y and g.y + g.h <= parent.y + parent.h
            by_type.setdefault(sc.type, set()).add(g.data["product"])
            # A dark element (the iris Prism Central block) puts its badge on a tile.
            assert bool(g.style.fill) == badges._dark(parent.style.fill)
    assert {"prism", "nci"} <= by_type["management"]
    assert "nci" in by_type["logical"]


def test_badges_are_deterministic_and_round_trip(design_envs: dict[str, Any]) -> None:
    a = report_scenes(design_envs["medium"])
    b = report_scenes(design_envs["medium"])
    assert [to_doc(s) for s in a] == [to_doc(s) for s in b]
    sc = next(s for s in a if _glyphs(s))
    assert _glyphs(from_doc(to_doc(sc)))


def test_a_full_corner_gets_no_badge() -> None:
    sc = Scene("s", "t", "T", "", 200, 100)
    I.place([sc], [{"id": "user:c", "icon": "cluster", "scene": "s", "x": 10, "y": 10,
                    "w": 120, "h": 30}])
    card = sc.node("user:c")
    # A chip in each right corner, wide enough to cover the whole right half.
    from ntnxvision_core.diagrams.scene import Node, Style
    for y in (card.y + 1, card.y + card.h - 13):
        sc.nodes.append(Node(f"chip{y}", "tag", card.x + card.w / 2 - 1, y, card.w / 2, 12,
                             Style(fill="#000000"), parent=card.id))
    badges.add([sc])
    assert not _glyphs(sc)


def test_product_cards_in_the_catalog_and_placed() -> None:
    docs = {d["id"]: d for d in I.catalog_docs()}
    for pid in P.PRODUCTS:
        d = docs[f"product_{pid}"]
        assert d["product"] == pid and d["group"] == "Nutanix products"
    assert docs["cluster"]["product"] is None
    assert I.GROUPS[0] == "Nutanix products"
    sc = Scene("s", "t", "T", "", 400, 200)
    I.place([sc], [{"id": "user:p", "icon": "product_nus", "scene": "s", "x": 20, "y": 20}])
    badges.add([sc])
    card, glyph = sc.node("user:p"), sc.node("user:p:glyph")
    assert glyph.kind == badges.KIND and glyph.data["product"] == "nus"
    assert glyph.parent == card.id
    assert card.lines[0].x >= glyph.x - card.x + glyph.w  # the text starts after it
    assert check_scene(sc) == []
    assert len(_glyphs(sc)) == 1


def test_every_format_draws_the_icon(design_envs: dict[str, Any], tmp_path: Path) -> None:
    scenes = [s for s in report_scenes(design_envs["medium"]) if _glyphs(s)]
    sc = scenes[0]
    product = _glyphs(sc)[0].data["product"]
    d0 = P.art(product).paths[0][1]
    for mode in ("live", "outline"):
        assert d0.encode() in render_svg(sc, mode)
    assert b"shape=image" in render_drawio([sc]) and b"data:image/svg+xml," in \
        render_drawio([sc])
    z = zipfile.ZipFile(io.BytesIO(render_vsdx(scenes)))
    assert any(n.startswith("visio/media/") for n in z.namelist())
    assert b"ForeignType='Bitmap'" in z.read("visio/pages/page1.xml")
    # PNG: the badge's pixels carry the icon's purple.
    g = _glyphs(sc)[0]
    with Image.open(io.BytesIO(render_png(sc, dpi=144))) as im:
        k = 144 / 72
        box = im.convert("RGB").crop((int(g.x * k), int(g.y * k), int((g.x + g.w) * k),
                                      int((g.y + g.h) * k)))
        assert any(abs(r - 0x78) < 40 and abs(gg - 0x55) < 40 and b > 0xC0
                   for r, gg, b in box.get_flattened_data())
    # PDF: fpdf2 imports the icon's SVG (vector paths, no raster).
    from fpdf import FPDF

    seen: list[bytes] = []
    pdf = FPDF(unit="pt", format=(sc.width, sc.height))
    original = pdf.image

    def image(name: Any, *a: Any, **k: Any) -> Any:
        seen.append(name.getvalue() if isinstance(name, io.BytesIO) else b"")
        return original(name, *a, **k)

    pdf.image = image  # type: ignore[method-assign]
    pdf.add_page()
    draw_scene(pdf, sc, 0, 0)
    pdf.output(str(tmp_path / "f.pdf"))
    assert P.svg_bytes(product) in seen and all(b.startswith(b"<svg") for b in seen)


# ─── reports ───────────────────────────────────────────────────────────


def test_pdf_report_carries_product_icons(design_envs: dict[str, Any], tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from ntnxvision_core.exporters import pdf as pdfmod

    icons: list[str] = []
    original = pdfmod._Pdf.product_icon

    def record(self: Any, product: P.Product, *a: Any, **k: Any) -> None:
        icons.append(product.id)
        original(self, product, *a, **k)

    monkeypatch.setattr(pdfmod._Pdf, "product_icon", record)
    export_environment(design_envs["medium"], "pdf", tmp_path, "m")
    estate = [p.id for p, _ in P.in_estate(design_envs["medium"])]
    # The cover row and the executive summary row show every product of the estate,
    # and the product chapters add theirs by their headings.
    assert icons[:len(estate)] == estate
    assert icons.count("nci") >= 4 and "prism" in icons[2 * len(estate):]


def test_word_report_carries_product_icons(design_envs: dict[str, Any],
                                           tmp_path: Path) -> None:
    path = export_environment(design_envs["medium"], "word", tmp_path, "m")
    z = zipfile.ZipFile(path)
    names = z.namelist()
    assert "word/media/product-nci.svg" in names and "word/media/product-prism.svg" in names
    xml = z.read("word/document.xml").decode()
    assert "<wp:anchor" in xml  # the heading icons float, so headings stay text only
    assert "Nutanix products in this environment" in xml


def test_excel_icons_leave_the_tables_alone(design_envs: dict[str, Any],
                                            tmp_path: Path) -> None:
    path = export_environment(design_envs["medium"], "excel", tmp_path, "m")
    wb = load_workbook(path)
    ws = wb["Clusters"]
    assert ws.cell(row=1, column=1).value == "Cluster"
    assert len(ws._images) == 1  # type: ignore[attr-defined]
    anchor = ws._images[0].anchor  # type: ignore[attr-defined]
    assert anchor._from.row == 0 and anchor._from.col == ws.max_column
    overview = wb["Overview"]
    labels = [r[0] for r in overview.iter_rows(values_only=True) if r[0]]
    assert "Nutanix products" in labels and "Nutanix Prism" in labels
    assert len(overview._images) >= 2  # type: ignore[attr-defined]
    assert not wb["Collection Notes"]._images  # type: ignore[attr-defined]


@pytest.fixture(scope="module")
def pe_env(_demo_state: Any) -> Any:
    from ntnxvision_core.api.element_client import ElementCredentials
    from ntnxvision_core.engine import collect_element_environment

    return collect_element_environment(ElementCredentials(host="pe01.demo.local",
                                                          username="admin", password="demo"))


@pytest.mark.parametrize("fmt", ["pdf", "word", "excel"])
def test_prism_element_runs_have_no_prism_product(pe_env: Any, fmt: str,
                                                  tmp_path: Path) -> None:
    ids = [p.id for p, _ in P.in_estate(pe_env)]
    assert "prism" not in ids and ids[0] == "nci"
    assert export_environment(pe_env, fmt, tmp_path, "pe").is_file()
