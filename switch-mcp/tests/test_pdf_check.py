from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    StreamObject,
)

from switch_mcp.pdf_check import check_pdf


def _box(*vals):
    return ArrayObject([FloatObject(v) for v in vals])


def make_pdf(path, *, bleed_pt=9.0, font_embedded=True, rgb_image=False):
    w = PdfWriter()
    trim_w, trim_h = 8.5 * 72, 11 * 72
    page = w.add_blank_page(trim_w + 2 * bleed_pt, trim_h + 2 * bleed_pt)
    page[NameObject("/TrimBox")] = _box(bleed_pt, bleed_pt, bleed_pt + trim_w, bleed_pt + trim_h)
    descriptor = DictionaryObject({NameObject("/Type"): NameObject("/FontDescriptor")})
    if font_embedded:
        descriptor[NameObject("/FontFile2")] = w._add_object(StreamObject())
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/TrueType"),
        NameObject("/BaseFont"): NameObject("/Arial"), NameObject("/FontDescriptor"): w._add_object(descriptor),
    })
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})})
    if rgb_image:
        img = StreamObject()
        img.update({
            NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(10), NameObject("/Height"): NumberObject(10),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8),
        })
        resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im1"): w._add_object(img)})
    page[NameObject("/Resources")] = resources
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def test_good_pdf(tmp_path):
    result = check_pdf(make_pdf(tmp_path / "ok.pdf"), 8.5, 11)
    assert result["summary"]["pages"] == 1
    assert result["summary"]["verdict"] in ("looks good", "check warnings")
    assert not [f for f in result["findings"] if f["severity"] != "info"]


def test_problems_found(tmp_path):
    result = check_pdf(make_pdf(tmp_path / "bad.pdf", bleed_pt=0, font_embedded=False, rgb_image=True), 5, 7)
    messages = " | ".join(f["message"] for f in result["findings"])
    assert "Trim size does not match" in messages
    assert "No bleed" in messages
    assert "Font not embedded: Arial" in messages
    assert "RGB images" in messages
    assert result["summary"]["verdict"] == "problems"


def test_small_bleed(tmp_path):
    result = check_pdf(make_pdf(tmp_path / "small.pdf", bleed_pt=4.0), 8.5, 11)
    assert any("smaller than the required" in f["message"] for f in result["findings"])
