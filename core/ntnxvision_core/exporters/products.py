"""The Nutanix products a report or a diagram names, and their product icons.

One registry for every consumer: the PDF, Word and Excel exports put a product's icon
beside the section that documents it (`SECTION_PRODUCT`, `SHEET_PRODUCT`) and list the
products an estate runs (`in_estate`); the diagram engine badges Prism Central blocks and
cluster cards with theirs, and the Diagrams editor offers every product as an icon.

The icons are the Nutanix product icon set's colour, icon-only drawings (``icons/``, see
its README), cropped to a square: ``<id>.svg`` (vector: the PDF, the SVG / draw.io
figures) and ``<id>.png`` (256 px, transparent: Word, Excel, PNG figures, Visio).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

ICON_DIR = Path(__file__).resolve().parent / "icons"


@dataclass(frozen=True)
class Product:
    id: str
    #: Full product name ("Nutanix Unified Storage").
    name: str
    #: Abbreviation ("NUS").
    short: str
    #: What it covers, in a few words.
    covers: str

    @property
    def svg(self) -> Path:
        return ICON_DIR / f"{self.id}.svg"

    @property
    def png(self) -> Path:
        return ICON_DIR / f"{self.id}.png"


#: Every product with an icon, in display order.
PRODUCTS: dict[str, Product] = {p.id: p for p in (
    Product("prism", "Nutanix Prism", "Prism", "Prism Central management plane"),
    Product("nci", "Nutanix Cloud Infrastructure", "NCI",
            "AOS, AHV, networking, DR and Flow"),
    Product("nus", "Nutanix Unified Storage", "NUS", "Files, Objects and Volumes"),
    Product("ndb", "Nutanix Database Service", "NDB", "Database lifecycle"),
    Product("ncm", "Nutanix Cloud Manager", "NCM",
            "Self-Service, Intelligent Operations, playbooks"),
    Product("nc2", "Nutanix Cloud Clusters", "NC2", "Clusters in a public cloud"),
    Product("nkp", "Nutanix Kubernetes Platform", "NKP", "Kubernetes"),
    Product("nai", "Nutanix Enterprise AI", "NAI", "AI model serving"),
)}

#: Report section (`sections.CATALOG` id) -> the product it documents. Sections not
#: listed (executive summary, assumptions, the appendix) are about the report itself.
SECTION_PRODUCT: dict[str, str] = {
    "licensing": "prism",
    "prism_central": "prism",
    "logical_design": "nci",
    "physical_design": "nci",
    "workloads": "nci",
    "catalog": "prism",
    "nus": "nus",
    "networking": "nci",
    "data_protection": "nci",
    "security": "nci",
    "ndb": "ndb",
    "kubernetes": "nkp",
    "identity": "prism",
    "automation": "ncm",
    "health": "prism",
    "lcm": "prism",
}

#: Fixed Excel summary sheets that belong to a product (object-type sheets follow their
#: section through `sections.SHEET_SECTION`).
SUMMARY_SHEET_PRODUCT: dict[str, str] = {"Prism Central": "prism"}


def for_section(section_id: str) -> Product | None:
    pid = SECTION_PRODUCT.get(section_id)
    return PRODUCTS[pid] if pid else None


def for_sheet(spec_type: str) -> Product | None:
    """The product of an object-type Excel sheet (`TableSpec.type`)."""
    from .sections import SHEET_SECTION

    sid = SHEET_SECTION.get(spec_type)
    return for_section(sid) if sid else None


def _n(items: Any) -> int:
    return len(items or [])


def in_estate(env: Any) -> list[tuple[Product, str]]:
    """(product, what this snapshot has of it) for each product the estate runs, in
    display order. Only what was collected counts: a product with no collected objects
    is not listed."""
    from .theme import plural

    out: list[tuple[Product, str]] = []
    is_pe = getattr(env, "source_type", None) == "prism_element"
    if not is_pe:
        pcs = _n(getattr(env, "source_environments", None)) if env.is_combined else 1
        out.append((PRODUCTS["prism"], plural(max(pcs, 1), "Prism Central")))
    if env.clusters:
        out.append((PRODUCTS["nci"], f"{plural(len(env.clusters), 'cluster')}, "
                                     f"{plural(len(env.hosts), 'node')}"))
    nus = []
    if env.file_servers:
        nus.append(plural(len(env.file_servers), "file server"))
    if env.object_stores:
        nus.append(plural(len(env.object_stores), "object store"))
    if env.volume_groups:
        nus.append(plural(len(env.volume_groups), "volume group"))
    if nus or env.file_shares or env.buckets:
        out.append((PRODUCTS["nus"], ", ".join(nus) or "Unified storage objects"))
    if env.databases or env.database_servers or env.time_machines:
        out.append((PRODUCTS["ndb"], plural(len(env.databases), "database")
                    if env.databases else plural(len(env.database_servers),
                                                 "database server")))
    kube = getattr(env, "kubernetes_clusters", None) or []
    if kube:
        nodes = _n(getattr(env, "kubernetes_nodes", None))
        out.append((PRODUCTS["nkp"], f"{plural(len(kube), 'cluster')}, "
                                     f"{plural(nodes, 'node')}"))
    if env.playbooks or env.ncm_reports:
        parts = []
        if env.playbooks:
            parts.append(plural(len(env.playbooks), "playbook"))
        if env.ncm_reports:
            parts.append(plural(len(env.ncm_reports), "report"))
        out.append((PRODUCTS["ncm"], ", ".join(parts)))
    return out


# ─── icon art ──────────────────────────────────────────────────────────────────

_PATH = re.compile(r'<path fill="(#[0-9A-Fa-f]{6})" d="([^"]+)"/>')
_VIEWBOX = re.compile(r'viewBox="([^"]+)"')


@dataclass(frozen=True)
class Art:
    """An icon's vector drawing: its square viewBox and its filled paths."""

    viewbox: tuple[float, float, float, float]
    paths: tuple[tuple[str, str], ...]


@lru_cache(maxsize=16)
def art(product_id: str) -> Art:
    text = PRODUCTS[product_id].svg.read_text(encoding="utf-8")
    m = _VIEWBOX.search(text)
    if m is None:  # pragma: no cover - the checked-in files all have one
        raise ValueError(f"{product_id}.svg has no viewBox")
    x, y, w, h = (float(v) for v in m.group(1).split())
    return Art((x, y, w, h), tuple(_PATH.findall(text)))


@lru_cache(maxsize=16)
def svg_bytes(product_id: str) -> bytes:
    return PRODUCTS[product_id].svg.read_bytes()


@lru_cache(maxsize=16)
def png_bytes(product_id: str) -> bytes:
    return PRODUCTS[product_id].png.read_bytes()


@lru_cache(maxsize=64)
def png_at(product_id: str, px: int) -> bytes:
    """The icon as a `px` square PNG (LANCZOS from the 256 px master), for consumers
    that store one copy per placement (an Excel sheet), so each copy stays small."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(png_bytes(product_id))) as im:
        small = im.convert("RGBA").resize((px, px), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "PNG", optimize=True)
    return buf.getvalue()


__all__ = ["ICON_DIR", "PRODUCTS", "SECTION_PRODUCT", "SUMMARY_SHEET_PRODUCT", "Art",
           "Product", "art", "for_section", "for_sheet", "in_estate", "png_at", "png_bytes",
           "svg_bytes"]
