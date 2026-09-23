"""Quick, local print-readiness check of a PDF.

This is not a replacement for PitStop; it catches the most common intake
problems (wrong size, no bleed, unembedded fonts, RGB images, encryption)
in a second or two, so a CSR can answer "is this file OK?" while the
customer is still on the phone.
"""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

PT_PER_INCH = 72.0
MM_PER_PT = 25.4 / 72.0


@dataclass
class Finding:
    severity: str  # "error" | "warning" | "info"
    message: str
    pages: list[int] = field(default_factory=list)


def _resolve(obj: Any) -> Any:
    return obj.get_object() if isinstance(obj, IndirectObject) else obj


def _box(page: Any, name: str) -> tuple[float, float, float, float] | None:
    raw = page.get(NameObject(name))
    if raw is None:
        return None
    x0, y0, x1, y1 = (float(v) for v in _resolve(raw))
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _size(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return box[2] - box[0], box[3] - box[1]


def _fmt_size(w: float, h: float) -> str:
    return f'{w / PT_PER_INCH:.3f}" x {h / PT_PER_INCH:.3f}" ({w * MM_PER_PT:.1f} x {h * MM_PER_PT:.1f} mm)'


def _colorspace_name(cs: Any) -> str:
    cs = _resolve(cs)
    if isinstance(cs, NameObject):
        return str(cs)
    if isinstance(cs, ArrayObject) and cs:
        family = str(_resolve(cs[0]))
        if family == "/ICCBased":
            n = _resolve(cs[1]).get("/N")
            return {1: "/ICCGray", 3: "/ICCRGB", 4: "/ICCCMYK"}.get(int(n) if n else 0, "/ICCBased")
        if family == "/Indexed":
            return _colorspace_name(cs[1])
        return family
    return "unknown"


def _walk_resources(resources: Any, fonts: dict, images: list, seen: set[int], page_no: int) -> None:
    resources = _resolve(resources)
    if not isinstance(resources, DictionaryObject) or id(resources) in seen:
        return
    seen.add(id(resources))
    for font in (_resolve(resources.get("/Font")) or {}).values():
        font = _resolve(font)
        name = str(font.get("/BaseFont", "unnamed"))
        subtype = str(font.get("/Subtype", ""))
        descriptor = font.get("/FontDescriptor")
        if subtype == "/Type0":
            descendants = _resolve(font.get("/DescendantFonts")) or []
            if descendants:
                descriptor = _resolve(descendants[0]).get("/FontDescriptor")
        descriptor = _resolve(descriptor)
        embedded = subtype == "/Type3" or bool(
            descriptor and any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))
        )
        entry = fonts.setdefault(name.lstrip("/"), {"embedded": embedded, "pages": set()})
        entry["embedded"] = entry["embedded"] and embedded
        entry["pages"].add(page_no)
    for xobj in (_resolve(resources.get("/XObject")) or {}).values():
        xobj = _resolve(xobj)
        subtype = xobj.get("/Subtype")
        if subtype == "/Image":
            if xobj.get("/ImageMask"):
                continue
            images.append({
                "page": page_no,
                "width_px": int(xobj.get("/Width", 0)),
                "height_px": int(xobj.get("/Height", 0)),
                "colorspace": _colorspace_name(xobj.get("/ColorSpace")) if "/ColorSpace" in xobj else "unknown",
                "bits": int(xobj.get("/BitsPerComponent", 0) or 0),
            })
        elif subtype == "/Form" and "/Resources" in xobj:
            _walk_resources(xobj["/Resources"], fonts, images, seen, page_no)


def check_pdf(
    source: str | Path | bytes,
    trim_width_in: float | None = None,
    trim_height_in: float | None = None,
    required_bleed_in: float = 0.125,
    tolerance_in: float = 0.01,
    *,
    name: str | None = None,
) -> dict[str, Any]:
    """Inspect a PDF (path or bytes) and return a summary plus a list of findings."""
    if isinstance(source, bytes):
        reader = PdfReader(io.BytesIO(source))
        name = name or "document.pdf"
    else:
        reader = PdfReader(str(source))
        name = name or Path(source).name
    findings: list[Finding] = []
    summary: dict[str, Any] = {"file": name, "pdf_version": reader.pdf_header.replace("%PDF-", "")}

    if reader.is_encrypted:
        findings.append(Finding("error", "PDF is encrypted/password protected."))
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001 - pypdf raises several types here
            summary["pages"] = None
            return {"summary": summary, "findings": [asdict(f) for f in findings]}

    pages = reader.pages
    summary["pages"] = len(pages)
    root = reader.trailer["/Root"]
    intents = _resolve(root.get("/OutputIntents")) or []
    summary["output_intent"] = (
        str(_resolve(intents[0]).get("/OutputConditionIdentifier", "")) if intents else None
    )
    if not intents:
        findings.append(Finding("info", "No output intent (not saved as PDF/X)."))

    sizes: dict[str, list[int]] = {}
    no_bleed: list[int] = []
    small_bleed: list[int] = []
    wrong_size: list[int] = []
    fonts: dict[str, dict] = {}
    images: list[dict] = []
    seen: set[int] = set()

    for i, page in enumerate(pages, start=1):
        media = _box(page, "/MediaBox")
        crop = _box(page, "/CropBox") or media
        trim = _box(page, "/TrimBox") or crop
        bleed = _box(page, "/BleedBox")
        w, h = _size(trim)
        rotate = int(page.get("/Rotate", 0) or 0) % 180
        if rotate:
            w, h = h, w
        sizes.setdefault(_fmt_size(w, h), []).append(i)

        if trim_width_in and trim_height_in:
            target = sorted((trim_width_in * PT_PER_INCH, trim_height_in * PT_PER_INCH))
            if any(abs(a - b) > tolerance_in * PT_PER_INCH for a, b in zip(sorted((w, h)), target)):
                wrong_size.append(i)

        if "/TrimBox" not in page:
            # Without a TrimBox the page edge is the trim, so there is no bleed.
            no_bleed.append(i)
        else:
            outer = bleed or media
            available = min(trim[0] - outer[0], trim[1] - outer[1], outer[2] - trim[2], outer[3] - trim[3])
            if available <= 0.5:
                no_bleed.append(i)
            elif available + tolerance_in * PT_PER_INCH < required_bleed_in * PT_PER_INCH:
                small_bleed.append(i)

        if "/Resources" in page:
            _walk_resources(page["/Resources"], fonts, images, seen, i)

    summary["trim_sizes"] = sizes
    if len(sizes) > 1:
        findings.append(Finding("warning", f"Pages have {len(sizes)} different trim sizes: {', '.join(sizes)}."))
    if wrong_size:
        findings.append(Finding(
            "error",
            f'Trim size does not match the ordered {trim_width_in}" x {trim_height_in}".',
            wrong_size,
        ))
    if no_bleed:
        findings.append(Finding("warning", "No bleed: no TrimBox, or artwork box equals the trim.", no_bleed))
    if small_bleed:
        findings.append(Finding(
            "warning", f'Bleed is smaller than the required {required_bleed_in}".', small_bleed
        ))

    missing = {n: sorted(v["pages"]) for n, v in fonts.items() if not v["embedded"]}
    summary["fonts"] = len(fonts)
    for font_name, font_pages in missing.items():
        findings.append(Finding("error", f"Font not embedded: {font_name}.", font_pages))

    rgb_pages = sorted({img["page"] for img in images if img["colorspace"] in ("/DeviceRGB", "/ICCRGB", "/CalRGB")})
    summary["images"] = len(images)
    if rgb_pages:
        findings.append(Finding("warning", "RGB images found (will be converted to CMYK).", rgb_pages))
    if any(img["bits"] == 16 for img in images):
        findings.append(Finding("info", "16-bit images found.", sorted({i["page"] for i in images if i["bits"] == 16})))

    annotated = [i for i, p in enumerate(pages, start=1)
                 if any(str(_resolve(a).get("/Subtype")) not in ("/Link",) for a in (_resolve(p.get("/Annots")) or []))]
    if annotated:
        findings.append(Finding("info", "Pages contain comments or form fields (annotations).", annotated))
    if "/OCProperties" in root:
        findings.append(Finding("info", "PDF contains layers (optional content); hidden layers may be present."))

    summary["verdict"] = (
        "problems" if any(f.severity == "error" for f in findings)
        else "check warnings" if any(f.severity == "warning" for f in findings)
        else "looks good"
    )
    return {"summary": summary, "findings": [asdict(f) for f in findings]}
