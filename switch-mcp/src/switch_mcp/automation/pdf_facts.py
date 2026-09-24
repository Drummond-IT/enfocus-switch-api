"""Measure what a print PDF actually contains, page by page.

Used to compare a customer file against its job ticket and to draft estimates.
It reads page boxes, and walks each page's content stream (and nested form
XObjects) for the colors actually painted: process inks (C, M, Y, K), RGB,
and spot (Separation / DeviceN) colorants.

Limits: colors set inside patterns/shadings and ink coverage are not
measured; that is what PitStop is for. Output is "good enough to spot a
4/4 file on a 1/1 ticket", not a separation preview.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

from pypdf import PdfReader
from pypdf.generic import ArrayObject, ContentStream, DictionaryObject, IndirectObject, NameObject

PT_PER_INCH = 72.0
MAX_PAGES = 400            # pages analysed for color (sizes are read for all pages)
MAX_OPS_PER_PAGE = 200_000
MAX_FORM_DEPTH = 8
PROCESS = ("C", "M", "Y", "K")


@dataclass
class PageFacts:
    page: int
    trim_in: tuple[float, float]
    bleed_in: float | None           # smallest bleed on any side, None if no TrimBox
    process: set[str] = field(default_factory=set)   # subset of C, M, Y, K
    rgb: bool = False                # non-neutral RGB content (will convert to CMYK)
    spots: set[str] = field(default_factory=set)     # spot colour names
    analysed: bool = True            # False when skipped (page limit / damaged content)

    @property
    def is_color(self) -> bool:
        return bool(self.rgb or (self.process & {"C", "M", "Y"}))

    def inks(self) -> str:
        """Ink count for this side, in the job-ticket sense: 4 = full color, 1 = black only."""
        if not self.analysed:
            return "?"
        n = 4 if self.is_color else (1 if "K" in self.process else 0)
        return str(n + len(self.spots)) if self.spots else str(n)

    def as_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "trim_in": [round(self.trim_in[0], 3), round(self.trim_in[1], 3)],
            "bleed_in": None if self.bleed_in is None else round(self.bleed_in, 3),
            "process_inks": sorted(self.process),
            "rgb": self.rgb,
            "spot_colors": sorted(self.spots),
            "color": self.is_color,
            "analysed": self.analysed,
        }


@dataclass
class PdfFacts:
    file: str
    pages: list[PageFacts]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def spot_colors(self) -> set[str]:
        return set().union(*(p.spots for p in self.pages)) if self.pages else set()

    def trim_sizes(self) -> list[tuple[float, float]]:
        sizes: list[tuple[float, float]] = []
        for p in self.pages:
            t = (round(p.trim_in[0], 2), round(p.trim_in[1], 2))
            if t not in sizes:
                sizes.append(t)
        return sizes

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "page_count": self.page_count,
            "trim_sizes_in": [list(s) for s in self.trim_sizes()],
            "spot_colors": sorted(self.spot_colors),
            "any_color": any(p.is_color for p in self.pages),
            "any_rgb": any(p.rgb for p in self.pages),
            "pages": [p.as_dict() for p in self.pages],
        }


def _resolve(obj: Any) -> Any:
    return obj.get_object() if isinstance(obj, IndirectObject) else obj


def _box(page: Any, name: str) -> tuple[float, float, float, float] | None:
    raw = page.get(NameObject(name))
    if raw is None:
        return None
    x0, y0, x1, y1 = (float(v) for v in _resolve(raw))
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _name(obj: Any) -> str:
    """PDF name without the slash, with #xx escapes decoded ("PANTONE#20185#20C" -> "PANTONE 185 C")."""
    raw = str(_resolve(obj)).lstrip("/")
    return re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), raw)


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class _ColorSpace:
    """What painting with a colorspace means in inks."""

    def __init__(self, kind: str, names: tuple[str, ...] = ()):
        self.kind = kind      # gray | rgb | cmyk | spot | pattern | other
        self.names = names    # spot names (Separation / DeviceN), or alternate kinds

    @classmethod
    def from_obj(cls, obj: Any, depth: int = 0) -> _ColorSpace:
        obj = _resolve(obj)
        if depth > 4:
            return cls("other")
        if isinstance(obj, NameObject):
            return {
                "/DeviceGray": cls("gray"), "/G": cls("gray"), "/CalGray": cls("gray"),
                "/DeviceRGB": cls("rgb"), "/RGB": cls("rgb"), "/CalRGB": cls("rgb"),
                "/DeviceCMYK": cls("cmyk"), "/CMYK": cls("cmyk"), "/Pattern": cls("pattern"),
            }.get(str(obj), cls("other"))
        if isinstance(obj, ArrayObject) and obj:
            family = str(_resolve(obj[0]))
            if family == "/ICCBased" and len(obj) > 1:
                n = int(_num(_resolve(obj[1]).get("/N", 0)))
                return {1: cls("gray"), 3: cls("rgb"), 4: cls("cmyk")}.get(n, cls("other"))
            if family in ("/CalRGB", "/Lab"):
                return cls("rgb")
            if family == "/CalGray":
                return cls("gray")
            if family == "/Indexed" and len(obj) > 1:
                return cls.from_obj(obj[1], depth + 1)
            if family == "/Separation" and len(obj) > 1:
                name = _name(obj[1])
                if name in ("All", "None"):
                    return cls("other")
                return cls("cmyk", (name,)) if name in ("Cyan", "Magenta", "Yellow", "Black") else cls("spot", (name,))
            if family == "/DeviceN" and len(obj) > 1:
                names = tuple(_name(n) for n in _resolve(obj[1]))
                return cls("spot", names)
            if family == "/Pattern":
                return cls("pattern")
        return cls("other")


_PROCESS_NAMES = {"Cyan": "C", "Magenta": "M", "Yellow": "Y", "Black": "K"}


class _Painter:
    def __init__(self, facts: PageFacts):
        self.facts = facts
        self.ops = 0

    def _gray(self, values: list[float]) -> None:
        if values and values[0] < 0.999:  # anything but white uses black ink
            self.facts.process.add("K")

    def _rgb(self, values: list[float]) -> None:
        if len(values) < 3:
            return
        r, g, b = values[:3]
        if max(r, g, b) - min(r, g, b) > 0.01:
            self.facts.rgb = True
        elif r < 0.999:
            self.facts.process.add("K")  # neutral RGB grey/black

    def _cmyk(self, values: list[float]) -> None:
        for ink, v in zip(PROCESS, values[:4]):
            if v > 0.001:
                self.facts.process.add(ink)

    def _paint(self, cs: _ColorSpace, values: list[float]) -> None:
        if cs.kind == "gray":
            self._gray(values)
        elif cs.kind == "rgb":
            self._rgb(values)
        elif cs.kind == "cmyk" and cs.names:  # Separation /Cyan etc.
            if values and values[0] > 0.001:
                self.facts.process.add(_PROCESS_NAMES[cs.names[0]])
        elif cs.kind == "cmyk":
            self._cmyk(values)
        elif cs.kind == "spot":
            for name, v in zip(cs.names, values or [1.0] * len(cs.names)):
                if v > 0.001:
                    if name in _PROCESS_NAMES:
                        self.facts.process.add(_PROCESS_NAMES[name])
                    else:
                        self.facts.spots.add(name)

    def run(self, content: Any, resources: Any, reader: PdfReader, depth: int = 0, seen: set[int] | None = None) -> None:
        seen = seen if seen is not None else set()
        resources = _resolve(resources) or DictionaryObject()
        colorspaces = _resolve(resources.get("/ColorSpace")) or {}
        xobjects = _resolve(resources.get("/XObject")) or {}
        fill = stroke = _ColorSpace("gray")
        stream = ContentStream(content, reader)
        for operands, op in stream.operations:
            self.ops += 1
            if self.ops > MAX_OPS_PER_PAGE:
                return
            vals = [_num(o) for o in operands if not isinstance(o, NameObject)]
            if op in (b"g", b"G"):
                self._gray(vals)
                if op == b"g":
                    fill = _ColorSpace("gray")
                else:
                    stroke = _ColorSpace("gray")
            elif op in (b"rg", b"RG"):
                self._rgb(vals)
            elif op in (b"k", b"K"):
                self._cmyk(vals)
            elif op in (b"cs", b"CS") and operands:
                name = operands[0]
                cs = _ColorSpace.from_obj(colorspaces.get(name, name)) if isinstance(name, NameObject) else \
                    _ColorSpace("other")
                if op == b"cs":
                    fill = cs
                else:
                    stroke = cs
                # Setting a colorspace selects its initial color (1.0 tint for Separation/DeviceN).
            elif op in (b"sc", b"scn"):
                self._paint(fill, vals)
            elif op in (b"SC", b"SCN"):
                self._paint(stroke, vals)
            elif op == b"Do" and operands:
                xobj = _resolve(xobjects.get(operands[0]))
                if not isinstance(xobj, DictionaryObject):
                    continue
                if xobj.get("/Subtype") == "/Image":
                    if xobj.get("/ImageMask"):
                        self._paint(fill, [1.0])
                    elif "/ColorSpace" in xobj:
                        cs = _ColorSpace.from_obj(xobj["/ColorSpace"])
                        if cs.kind == "rgb":
                            self.facts.rgb = True
                        elif cs.kind == "cmyk" and not cs.names:
                            self.facts.process.update(PROCESS)
                        elif cs.kind == "gray":
                            self.facts.process.add("K")
                        else:
                            self._paint(cs, [])
                elif xobj.get("/Subtype") == "/Form" and depth < MAX_FORM_DEPTH and id(xobj) not in seen:
                    seen.add(id(xobj))
                    self.run(xobj, xobj.get("/Resources", resources), reader, depth + 1, seen)


def analyse_pdf(source: bytes, name: str = "document.pdf") -> PdfFacts:
    reader = PdfReader(io.BytesIO(source))
    if reader.is_encrypted:
        reader.decrypt("")
    pages: list[PageFacts] = []
    for i, page in enumerate(reader.pages, start=1):
        media = _box(page, "/MediaBox")
        crop = _box(page, "/CropBox") or media
        trim = _box(page, "/TrimBox") or crop
        w, h = trim[2] - trim[0], trim[3] - trim[1]
        if int(page.get("/Rotate", 0) or 0) % 180:
            w, h = h, w
        bleed = None
        if "/TrimBox" in page:
            outer = _box(page, "/BleedBox") or media
            bleed = max(0.0, min(trim[0] - outer[0], trim[1] - outer[1], outer[2] - trim[2], outer[3] - trim[3]))
            bleed /= PT_PER_INCH
        facts = PageFacts(page=i, trim_in=(w / PT_PER_INCH, h / PT_PER_INCH), bleed_in=bleed)
        if i <= MAX_PAGES and page.get("/Contents") is not None:
            try:
                _Painter(facts).run(page.get_contents(), page.get("/Resources"), reader)
            except Exception:  # noqa: BLE001 - damaged content: report sizes, mark colour unknown
                facts.analysed = False
        elif i > MAX_PAGES:
            facts.analysed = False
        pages.append(facts)
    return PdfFacts(file=name, pages=pages)
