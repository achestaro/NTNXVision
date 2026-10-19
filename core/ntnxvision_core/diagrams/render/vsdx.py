"""Scenes -> Microsoft Visio .vsdx (one page per scene), stdlib only (zipfile + XML).

The package is the minimal OPC set Visio 2013+ writes: content types, package rels,
docProps, ``visio/document.xml`` (one "No Style" stylesheet and the font names),
``pages/pages.xml`` with one ``pages/pageN.xml`` per scene, and ``windows.xml``.

What is editable in Visio:

* every node is a real 2-D shape: a (rounded) rectangle with fill, line and dash cells,
  its text as formatted runs (Character rows per size, weight and colour, font Figtree,
  which Visio substitutes when it is not installed), and its semantic data as Shape Data
  (plus "Diagram id" and "Kind");
* a container (an availability zone, a POD) is a Visio group holding its member shapes,
  drawn behind them (``DisplayMode`` 1), so moving it moves its contents;
* every edge is a 1-D connector glued to its end shapes (``<Connect>`` with dynamic glue
  and ``_XFTRIGGER`` begin and end triggers, so Visio re-routes it when a shape moves),
  with orthogonal geometry, arrowheads and its first label as connector text on a white
  background; further labels are text-only shapes. Legend samples have no ends and stay
  plain lines.

Determinism: shape ids follow scene order, zip entries are written in a fixed order with
a fixed timestamp (1980-01-01) and fixed compression; docProps carry the given creation
time (the snapshot's collection time), never the wall clock.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from .. import text as tx
from ..scene import LAYER, Edge, EdgeLabel, Node, Scene, Style, TextLine

NS = "http://schemas.microsoft.com/office/visio/2012/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_DOC = "http://schemas.microsoft.com/visio/2010/relationships/document"
REL_PAGES = "http://schemas.microsoft.com/visio/2010/relationships/pages"
REL_PAGE = "http://schemas.microsoft.com/visio/2010/relationships/page"
REL_WINDOWS = "http://schemas.microsoft.com/visio/2010/relationships/windows"
REL_CORE = ("http://schemas.openxmlformats.org/package/2006/relationships/metadata/"
            "core-properties")
REL_APP = ("http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
           "extended-properties")
FONT = "Figtree"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
XML_HEAD = "<?xml version='1.0' encoding='utf-8' standalone='yes'?>\n"
#: Visio arrowhead: a filled triangle.
ARROW = "4"
#: Paint layer per node kind, as in `scene.draw_order` (connectors sit at 2).


def _in(v_pt: float) -> str:
    """Points -> inches on a fixed 6-decimal grid (Visio stores inches)."""
    s = f"{v_pt / 72:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _c(name: str, value: str, unit: str | None = None, f: str | None = None) -> str:
    u = f" U='{unit}'" if unit else ""
    ff = f" F={quoteattr(f)}" if f else ""
    return f"<Cell N='{name}' V={quoteattr(value)}{u}{ff}/>"


def _rect_geom(no_fill: bool, no_line: bool) -> str:
    rows = [("RelMoveTo", 0, 0), ("RelLineTo", 1, 0), ("RelLineTo", 1, 1),
            ("RelLineTo", 0, 1), ("RelLineTo", 0, 0)]
    body = "".join(f"<Row T='{t}' IX='{i + 1}'>{_c('X', str(x))}{_c('Y', str(y))}</Row>"
                   for i, (t, x, y) in enumerate(rows))
    return (f"<Section N='Geometry' IX='0'>{_c('NoFill', '1' if no_fill else '0')}"
            f"{_c('NoLine', '1' if no_line else '0')}{_c('NoShow', '0')}"
            f"{_c('NoSnap', '0')}{body}</Section>")


def _pattern(st: Style) -> str:
    if not st.dash:
        return "1"
    return "3" if st.dash[0] <= 1.6 else "2"  # dots for the short user / down dashes


@dataclass(frozen=True)
class _Block:
    """A text block: its lines, alignment and the margins that place it."""

    lines: list[TextLine]
    centred: bool
    left: float
    top: float


def _block(lines: list[TextLine], w: float) -> _Block:
    centred = all(abs(t.x - (w - t.width) / 2) < 0.6 for t in lines) and any(
        t.x > 1.0 for t in lines)
    left = 0.0 if centred else min(t.x for t in lines)
    first = lines[0]
    top = max(0.0, first.baseline - tx.baseline(first.size))
    return _Block(lines, centred, left, top)


def _text_sections(b: _Block, font: str = FONT) -> tuple[str, str]:
    """(Character + Paragraph sections, <Text> element) for a text block. Each line is
    a paragraph with the scene's exact line pitch (absolute spacing), so the lines sit
    where the layout put them."""
    fmts: list[tuple[float, bool, str]] = []
    pitches: list[float] = []
    for t in b.lines:
        key = (t.size, t.bold, t.color)
        if key not in fmts:
            fmts.append(key)
        if tx.line_height(t.size) not in pitches:
            pitches.append(tx.line_height(t.size))
    chars = "".join(
        f"<Row IX='{i}'>{_c('Font', font)}{_c('Color', c)}{_c('Style', '1' if bold else '0')}"
        f"{_c('Size', _in(s), 'PT')}</Row>" for i, (s, bold, c) in enumerate(fmts))
    paras = "".join(
        f"<Row IX='{i}'>{_c('HorzAlign', '1' if b.centred else '0')}"
        f"{_c('SpLine', _in(p), 'PT')}{_c('SpBefore', '0', 'PT')}"
        f"{_c('SpAfter', '0', 'PT')}{_c('IndFirst', '0', 'PT')}{_c('IndLeft', '0', 'PT')}"
        f"</Row>" for i, p in enumerate(pitches))
    runs = []
    for i, t in enumerate(b.lines):
        sep = "\n" if i < len(b.lines) - 1 else ""
        runs.append(f"<cp IX='{fmts.index((t.size, t.bold, t.color))}'/>"
                    f"<pp IX='{pitches.index(tx.line_height(t.size))}'/>"
                    f"{escape(t.text)}{sep}")
    return (f"<Section N='Character'>{chars}</Section><Section N='Paragraph'>{paras}"
            f"</Section>", f"<Text>{''.join(runs)}</Text>")


def _text_cells(b: _Block, w: float, h: float) -> str:
    natural = sum(tx.line_height(t.size) for t in b.lines)
    bottom = max(0.0, h - b.top - natural)
    return "".join([
        _c("VerticalAlign", "0"), _c("TopMargin", _in(b.top), "PT"),
        _c("BottomMargin", _in(bottom), "PT"), _c("LeftMargin", _in(b.left), "PT"),
        _c("RightMargin", _in(b.left if b.centred else 0.0), "PT"),
        _c("TextBkgnd", "0"),
    ])


def _style_cells(st: Style, text_only: bool) -> str:
    out = []
    if st.fill and not text_only:
        out += [_c("FillForegnd", st.fill), _c("FillBkgnd", st.fill), _c("FillPattern", "1")]
    else:
        out += [_c("FillPattern", "0")]
    if st.stroke and not text_only:
        out += [_c("LineColor", st.stroke), _c("LineWeight", _in(st.stroke_w), "PT"),
                _c("LinePattern", _pattern(st))]
    else:
        out += [_c("LinePattern", "0")]
    out.append(_c("Rounding", _in(st.radius if not text_only else 0.0), "PT"))
    return "".join(out)


_PROP = re.compile(r"[^A-Za-z0-9_]")


def _shape_data(n: Node) -> str:
    rows = [("Diagram_id", "Diagram id", n.id), ("Kind", "Kind", n.kind)]
    for k, v in n.data.items():
        rows.append((_PROP.sub("_", k), k.replace("_", " ").capitalize(), v))
    seen: set[str] = set()
    body = []
    for name, label, value in rows:
        if name in seen:
            continue
        seen.add(name)
        body.append(f"<Row N={quoteattr(name)}>{_c('Label', label)}{_c('Value', value, 'STR')}"
                    f"{_c('Type', '0')}{_c('Invisible', '0')}</Row>")
    return f"<Section N='Property'>{''.join(body)}</Section>"


class _Page:
    """One scene as a Visio page: ids, group membership, shapes and glue records."""

    def __init__(self, sc: Scene, font: str = FONT) -> None:
        self.font = font
        self.sc = sc
        self.by_id = {n.id: n for n in sc.nodes}
        self.index = {n.id: i for i, n in enumerate(sc.nodes)}
        self.ids = {n.id: i + 1 for i, n in enumerate(sc.nodes)}
        self.next_id = len(sc.nodes) + 1
        #: Every node with members is a Visio group (an AZ, a POD, a cluster card with
        #: its chips and strips), so moving it moves what it holds.
        self.parents = {n.parent for n in sc.nodes if n.parent in self.by_id}
        self.members: dict[str | None, list[Node]] = {}
        for n in sc.nodes:
            self.members.setdefault(self.group_of(n), []).append(n)

    def group_of(self, n: Node) -> str | None:
        """The group holding `n` (its parent), None at page level."""
        return n.parent if n.parent in self.by_id and n.parent != n.id else None

    def _ordered(self, nodes: list[Node]) -> list[Node]:
        return sorted(nodes, key=lambda n: (LAYER.get(n.kind, 1), self.index[n.id]))

    def node(self, n: Node, ox: float, oy_bottom: float) -> str:
        """`n` positioned in its parent's coordinates: origin (`ox`, `oy_bottom`) is the
        parent's bottom-left corner in scene points (y down)."""
        cx, cy = n.x + n.w / 2, n.y + n.h / 2
        text_only = n.kind == "label"
        group = n.id in self.parents
        cells = [
            _c("PinX", _in(cx - ox), "PT"), _c("PinY", _in(oy_bottom - cy), "PT"),
            _c("Width", _in(n.w), "PT"), _c("Height", _in(n.h), "PT"),
            _c("LocPinX", _in(n.w / 2), "PT", "Width*0.5"),
            _c("LocPinY", _in(n.h / 2), "PT", "Height*0.5"), _c("Angle", "0"),
            _style_cells(n.style, text_only),
        ]
        chars = text = ""
        if n.lines:
            b = _block(n.lines, n.w)
            cells.append(_text_cells(b, n.w, n.h))
            chars, text = _text_sections(b, self.font)
        if group:
            # Group geometry behind the members; a click selects the group first.
            cells += [_c("DisplayMode", "1"), _c("SelectMode", "1"),
                      _c("IsTextEditTarget", "1"), _c("IsSnapTarget", "1")]
        no_fill = text_only or not n.style.fill
        no_line = text_only or not n.style.stroke
        kind = "Group" if group else "Shape"
        inner = ""
        if group:
            kids = self._ordered(self.members.get(n.id, []))
            inner = "<Shapes>" + "".join(self.node(k, n.x, n.y + n.h) for k in kids) + \
                "</Shapes>"
        return (f"<Shape ID='{self.ids[n.id]}' NameU='Sheet.{self.ids[n.id]}' "
                f"Name={quoteattr(n.id)} "
                f"Type='{kind}' LineStyle='0' FillStyle='0' TextStyle='0'>{''.join(cells)}"
                f"{chars}{_shape_data(n)}{_rect_geom(no_fill, no_line)}{text}{inner}</Shape>")

    def label_shape(self, lab: EdgeLabel, e: Edge) -> str:
        sid = self.next_id
        self.next_id += 1
        H = self.sc.height
        n = Node(id=f"{e.id}:label:{sid}", kind="label", x=lab.x, y=lab.y, w=lab.w, h=lab.h,
                 style=Style(fill="#FFFFFF" if lab.background else None), lines=lab.lines)
        b = _block(lab.lines, lab.w)
        chars, text = _text_sections(b, self.font)
        cells = [
            _c("PinX", _in(lab.x + lab.w / 2), "PT"), _c("PinY", _in(H - lab.y - lab.h / 2), "PT"),
            _c("Width", _in(lab.w), "PT"), _c("Height", _in(lab.h), "PT"),
            _c("LocPinX", _in(lab.w / 2), "PT", "Width*0.5"),
            _c("LocPinY", _in(lab.h / 2), "PT", "Height*0.5"), _c("Angle", "0"),
            _style_cells(n.style, False), _c("LinePattern", "0"), _text_cells(b, lab.w, lab.h),
        ]
        return (f"<Shape ID='{sid}' NameU='Sheet.{sid}' Name={quoteattr(n.id)} "
                f"Type='Shape' LineStyle='0' FillStyle='0' TextStyle='0'>{''.join(cells)}"
                f"{chars}{_rect_geom(not lab.background, True)}{text}</Shape>")

    def edge(self, e: Edge) -> tuple[str, list[str]]:
        H = self.sc.height
        sid = self.next_id
        self.next_id += 1
        pts = [(x, H - y) for x, y in e.points]  # y up, points
        (bx, by), (ex, ey) = pts[0], pts[-1]
        st = e.style
        src, dst = self.ids.get(e.src), self.ids.get(e.dst)
        arrow = "1" if st.stroke_w < 1.0 else "2" if st.stroke_w < 1.5 else "3"
        cells = [
            _c("PinX", _in((bx + ex) / 2), "PT"), _c("PinY", _in((by + ey) / 2), "PT"),
            _c("Width", _in(ex - bx), "PT", "GUARD(EndX-BeginX)"),
            _c("Height", _in(ey - by), "PT", "GUARD(EndY-BeginY)"),
            _c("LocPinX", _in((ex - bx) / 2), "PT", "Width*0.5"),
            _c("LocPinY", _in((ey - by) / 2), "PT", "Height*0.5"),
            _c("BeginX", _in(bx), "PT"), _c("BeginY", _in(by), "PT"),
            _c("EndX", _in(ex), "PT"), _c("EndY", _in(ey), "PT"),
            _c("ObjType", "2"),
            _c("LineColor", st.stroke or "#000000"), _c("LineWeight", _in(st.stroke_w), "PT"),
            _c("LinePattern", _pattern(st)), _c("Rounding", "0", "PT"),
            _c("BeginArrow", ARROW if e.arrow_start else "0"),
            _c("EndArrow", ARROW if e.arrow_end else "0"),
            # Arrowheads grow with the line weight (Visio sizes: 1 small to 3 large).
            _c("BeginArrowSize", arrow), _c("EndArrowSize", arrow),
            # Right-angle routing when Visio re-routes after a glued shape moves.
            _c("ShapeRouteStyle", "1" if (src or dst) else "0"),
            _c("ConLineRouteExt", "1"), _c("FillPattern", "0"),
        ]
        if src is not None:
            cells.append(_c("BegTrigger", "2", None, f"_XFTRIGGER(Sheet.{src}!EventXFMod)"))
        if dst is not None:
            cells.append(_c("EndTrigger", "2", None, f"_XFTRIGGER(Sheet.{dst}!EventXFMod)"))
        geom = [f"<Row T='MoveTo' IX='1'>{_c('X', '0', 'PT')}{_c('Y', '0', 'PT')}</Row>"]
        for i, (x, y) in enumerate(pts[1:], start=2):
            geom.append(f"<Row T='LineTo' IX='{i}'>{_c('X', _in(x - bx), 'PT')}"
                        f"{_c('Y', _in(y - by), 'PT')}</Row>")
        geometry = (f"<Section N='Geometry' IX='0'>{_c('NoFill', '1')}{_c('NoLine', '0')}"
                    f"{_c('NoShow', '0')}{_c('NoSnap', '0')}{''.join(geom)}</Section>")
        chars = text = ""
        if e.labels and e.labels[0].lines:
            lab = e.labels[0]
            b = _block(lab.lines, lab.w)
            lx = lab.x + lab.w / 2 - e.points[0][0]
            ly = -(lab.y + lab.h / 2 - e.points[0][1])
            cells += [
                _c("TxtPinX", _in(lx), "PT"), _c("TxtPinY", _in(ly), "PT"),
                _c("TxtWidth", _in(lab.w), "PT"), _c("TxtHeight", _in(lab.h), "PT"),
                _c("TxtLocPinX", _in(lab.w / 2), "PT", "TxtWidth*0.5"),
                _c("TxtLocPinY", _in(lab.h / 2), "PT", "TxtHeight*0.5"), _c("TxtAngle", "0"),
                _c("TextBkgnd", "#FFFFFF" if lab.background else "0"),
                _c("LeftMargin", "0", "PT"), _c("RightMargin", "0", "PT"),
                _c("TopMargin", _in(b.top), "PT"), _c("BottomMargin", "0", "PT"),
                _c("VerticalAlign", "0"),
            ]
            chars, text = _text_sections(_Block(b.lines, True, 0.0, b.top), self.font)
        data = "".join(
            f"<Row N={quoteattr(_PROP.sub('_', k))}>{_c('Label', k.capitalize())}"
            f"{_c('Value', v, 'STR')}{_c('Type', '0')}</Row>" for k, v in e.data.items())
        props = f"<Section N='Property'>{data}</Section>" if data else ""
        shape = (f"<Shape ID='{sid}' NameU={quoteattr('Connector.' + str(sid))} "
                 f"Name={quoteattr(e.id)} Type='Shape' LineStyle='0' FillStyle='0' "
                 f"TextStyle='0'>{''.join(cells)}{chars}{props}{geometry}{text}</Shape>")
        connects = []
        if src is not None:
            connects.append(f"<Connect FromSheet='{sid}' FromCell='BeginX' FromPart='9' "
                            f"ToSheet='{src}' ToCell='PinX' ToPart='3'/>")
        if dst is not None:
            connects.append(f"<Connect FromSheet='{sid}' FromCell='EndX' FromPart='12' "
                            f"ToSheet='{dst}' ToCell='PinX' ToPart='3'/>")
        return shape, connects

    def xml(self) -> str:
        H = self.sc.height
        top = self._ordered(self.members.get(None, []))
        lower = [n for n in top if LAYER.get(n.kind, 1) < 2]
        upper = [n for n in top if LAYER.get(n.kind, 1) >= 2]
        shapes = [self.node(n, 0.0, H) for n in lower]
        connects: list[str] = []
        for e in self.sc.edges:
            s, c = self.edge(e)
            shapes.append(s)
            connects.extend(c)
        for e in self.sc.edges:
            for lab in e.labels[1:]:
                shapes.append(self.label_shape(lab, e))
        shapes += [self.node(n, 0.0, H) for n in upper]
        con = f"<Connects>{''.join(connects)}</Connects>" if connects else ""
        return (f"{XML_HEAD}<PageContents xmlns='{NS}' xmlns:r='{NS_R}' xml:space='preserve'>"
                f"<Shapes>{''.join(shapes)}</Shapes>{con}</PageContents>")


def _stylesheet() -> str:
    cells = "".join([
        _c("EnableLineProps", "1"), _c("EnableFillProps", "1"), _c("EnableTextProps", "1"),
        _c("HideForApply", "0"), _c("LineWeight", "0.01041666666666667"),
        _c("LineColor", "#000000"), _c("LinePattern", "1"), _c("Rounding", "0"),
        _c("EndArrowSize", "2"), _c("BeginArrow", "0"), _c("EndArrow", "0"),
        _c("LineCap", "0"), _c("BeginArrowSize", "2"), _c("LineColorTrans", "0"),
        _c("FillForegnd", "#FFFFFF"), _c("FillBkgnd", "#FFFFFF"), _c("FillPattern", "1"),
        _c("ShdwPattern", "0"), _c("LeftMargin", "0"), _c("RightMargin", "0"),
        _c("TopMargin", "0"), _c("BottomMargin", "0"),
        _c("VerticalAlign", "0"), _c("TextBkgnd", "0"), _c("TextDirection", "0"),
    ])
    char = (f"<Section N='Character'><Row IX='0'>{_c('Font', FONT)}{_c('Color', '#0F1A24')}"
            f"{_c('Style', '0')}{_c('Size', '0.1111111')}</Row></Section>")
    para = f"<Section N='Paragraph'><Row IX='0'>{_c('HorzAlign', '0')}</Row></Section>"
    return (f"<StyleSheets><StyleSheet ID='0' NameU='No Style' IsCustomNameU='1' "
            f"Name='No Style' IsCustomName='1'>{cells}{char}{para}</StyleSheet></StyleSheets>")


def page_names(scenes: list[Scene], names: list[str] | None) -> list[str]:
    """Unique page names of at most 31 characters (Visio and draw.io tab labels)."""
    raw = names or [(sc.caption.split(":")[0] if sc.caption else sc.title) for sc in scenes]
    out: list[str] = []
    for name in raw:
        base = (name.strip() or "Diagram")[:31]
        cand, k = base, 2
        while cand.lower() in (o.lower() for o in out):
            suffix = f" {k}"
            cand = base[:31 - len(suffix)] + suffix
            k += 1
        out.append(cand)
    return out


def _zip(files: list[tuple[str, str | bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files:
            zi = zipfile.ZipInfo(name, ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data.encode("utf-8") if isinstance(data, str) else data)
    return buf.getvalue()


def render_vsdx(scenes: list[Scene], names: list[str] | None = None, title: str = "Diagrams",
                created: str | None = None, font: str = FONT) -> bytes:
    """A .vsdx with one page per scene, named `names` (defaults from the captions)."""
    if not scenes:
        raise ValueError("A Visio file needs at least one diagram")
    created = created or "2000-01-01T00:00:00Z"
    pnames = page_names(scenes, names)
    overrides = [
        ("/visio/document.xml", "application/vnd.ms-visio.drawing.main+xml"),
        ("/visio/pages/pages.xml", "application/vnd.ms-visio.pages+xml"),
        ("/visio/windows.xml", "application/vnd.ms-visio.windows+xml"),
        ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
        ("/docProps/app.xml",
         "application/vnd.openxmlformats-officedocument.extended-properties+xml"),
    ] + [(f"/visio/pages/page{i + 1}.xml", "application/vnd.ms-visio.page+xml")
         for i in range(len(scenes))]
    files: list[tuple[str, str | bytes]] = []
    files.append(("[Content_Types].xml",
                  f"{XML_HEAD}<Types xmlns='http://schemas.openxmlformats.org/package/2006/"
                  "content-types'><Default Extension='rels' ContentType='application/"
                  "vnd.openxmlformats-package.relationships+xml'/><Default Extension='xml' "
                  "ContentType='application/xml'/>"
                  + "".join(f"<Override PartName='{p}' ContentType='{t}'/>" for p, t in overrides)
                  + "</Types>"))
    files.append(("_rels/.rels", f"{XML_HEAD}<Relationships xmlns='{NS_PKG_REL}'>"
                  f"<Relationship Id='rId1' Type='{REL_DOC}' Target='visio/document.xml'/>"
                  f"<Relationship Id='rId2' Type='{REL_CORE}' Target='docProps/core.xml'/>"
                  f"<Relationship Id='rId3' Type='{REL_APP}' Target='docProps/app.xml'/>"
                  "</Relationships>"))
    files.append(("docProps/core.xml",
                  f"{XML_HEAD}<cp:coreProperties xmlns:cp='http://schemas.openxmlformats.org/"
                  "package/2006/metadata/core-properties' xmlns:dc='http://purl.org/dc/"
                  "elements/1.1/' xmlns:dcterms='http://purl.org/dc/terms/' "
                  "xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance'>"
                  f"<dc:title>{escape(title)}</dc:title><dc:creator>NTNXVision</dc:creator>"
                  f"<dcterms:created xsi:type='dcterms:W3CDTF'>{escape(created)}"
                  "</dcterms:created>"
                  f"<dcterms:modified xsi:type='dcterms:W3CDTF'>{escape(created)}"
                  "</dcterms:modified></cp:coreProperties>"))
    files.append(("docProps/app.xml",
                  f"{XML_HEAD}<Properties xmlns='http://schemas.openxmlformats.org/"
                  "officeDocument/2006/extended-properties'><Application>NTNXVision"
                  "</Application><DocSecurity>0</DocSecurity></Properties>"))
    # A sans-serif PANOSE, so Windows maps Figtree to a like font where it is missing.
    panose = {"Calibri": "2 15 5 2 2 2 4 3 2 4"}
    faces = "".join(
        f"<FaceName NameU='{f}' UnicodeRanges='-536859905 -1073732485 9 0' "
        f"CharSets='536871327 0' Panos='{panose.get(f, '2 11 5 2 4 2 4 2 2 3')}' "
        "Flags='325'/>" for f in dict.fromkeys((font, "Calibri")))
    files.append(("visio/document.xml",
                  f"{XML_HEAD}<VisioDocument xmlns='{NS}' xmlns:r='{NS_R}' "
                  "xml:space='preserve'><DocumentSettings TopPage='0' DefaultTextStyle='0' "
                  "DefaultLineStyle='0' DefaultFillStyle='0' DefaultGuideStyle='0'>"
                  "<GlueSettings>9</GlueSettings><SnapSettings>65847</SnapSettings>"
                  "<SnapExtensions>34</SnapExtensions><DynamicGridEnabled>0"
                  "</DynamicGridEnabled></DocumentSettings>"
                  f"<FaceNames>{faces}</FaceNames>{_stylesheet()}</VisioDocument>"))
    files.append(("visio/_rels/document.xml.rels",
                  f"{XML_HEAD}<Relationships xmlns='{NS_PKG_REL}'>"
                  f"<Relationship Id='rId1' Type='{REL_PAGES}' Target='pages/pages.xml'/>"
                  f"<Relationship Id='rId2' Type='{REL_WINDOWS}' Target='windows.xml'/>"
                  "</Relationships>"))
    pages = []
    for i, (sc, name) in enumerate(zip(scenes, pnames, strict=True)):
        sheet = "".join([
            _c("PageWidth", _in(sc.width), "PT"), _c("PageHeight", _in(sc.height), "PT"),
            _c("ShdwOffsetX", "0.125"), _c("ShdwOffsetY", "-0.125"),
            _c("PageScale", "1", "IN_F"), _c("DrawingScale", "1", "IN_F"),
            _c("DrawingSizeType", "3"), _c("DrawingScaleType", "0"),
            _c("InhibitSnap", "0"), _c("PageLockReplace", "0"), _c("PageLockDuplicate", "0"),
            _c("UIVisibility", "0"), _c("ShdwType", "0"), _c("ShdwObliqueAngle", "0"),
            _c("ShdwScaleFactor", "1"), _c("DrawingResizeType", "1"),
            _c("PageLeftMargin", "0.25"), _c("PageRightMargin", "0.25"),
            _c("PageTopMargin", "0.25"), _c("PageBottomMargin", "0.25"),
            _c("PrintPageOrientation", "2" if sc.width >= sc.height else "1"),
        ])
        pages.append(f"<Page ID='{i}' NameU={quoteattr(name)} Name={quoteattr(name)} "
                     f"ViewScale='-1' ViewCenterX='{_in(sc.width / 2)}' "
                     f"ViewCenterY='{_in(sc.height / 2)}'><PageSheet LineStyle='0' "
                     f"FillStyle='0' TextStyle='0'>{sheet}</PageSheet>"
                     f"<Rel r:id='rId{i + 1}'/></Page>")
    files.append(("visio/pages/pages.xml",
                  f"{XML_HEAD}<Pages xmlns='{NS}' xmlns:r='{NS_R}' xml:space='preserve'>"
                  f"{''.join(pages)}</Pages>"))
    files.append(("visio/pages/_rels/pages.xml.rels",
                  f"{XML_HEAD}<Relationships xmlns='{NS_PKG_REL}'>"
                  + "".join(f"<Relationship Id='rId{i + 1}' Type='{REL_PAGE}' "
                            f"Target='page{i + 1}.xml'/>" for i in range(len(scenes)))
                  + "</Relationships>"))
    for i, sc in enumerate(scenes):
        files.append((f"visio/pages/page{i + 1}.xml", _Page(sc, font).xml()))
    first = scenes[0]
    files.append(("visio/windows.xml",
                  f"{XML_HEAD}<Windows ClientWidth='1600' ClientHeight='900' xmlns='{NS}' "
                  f"xmlns:r='{NS_R}' xml:space='preserve'><Window ID='0' "
                  "WindowType='Drawing' WindowState='1073741824' WindowLeft='-8' "
                  "WindowTop='-31' WindowWidth='1616' WindowHeight='939' "
                  "ContainerType='Page' Page='0' ViewScale='-1' "
                  f"ViewCenterX='{_in(first.width / 2)}' "
                  f"ViewCenterY='{_in(first.height / 2)}'><ShowRulers>1</ShowRulers>"
                  "<ShowGrid>0</ShowGrid><ShowPageBreaks>0</ShowPageBreaks>"
                  "<ShowGuides>1</ShowGuides><ShowConnectionPoints>1</ShowConnectionPoints>"
                  "<GlueSettings>9</GlueSettings><SnapSettings>65847</SnapSettings>"
                  "<SnapExtensions>34</SnapExtensions><DynamicGridEnabled>0"
                  "</DynamicGridEnabled><TabSplitterPos>0.5</TabSplitterPos></Window>"
                  "</Windows>"))
    return _zip(files)
