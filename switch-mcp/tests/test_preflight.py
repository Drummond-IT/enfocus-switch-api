from pathlib import Path

from switch_mcp import preflight
from switch_mcp.knowledge import categorize

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_pitstop_xml_v3():
    report = preflight.parse_report((FIXTURES / "pitstop_report.xml").read_bytes())
    assert report.source_format == "pitstop-xml-v3"
    assert report.summary == {"profile": "Sheetfed Offset CMYK", "file": "ORD1001_Brochure.pdf", "pages": "8"}
    by_msg = {f.message: f for f in report.findings}
    font = by_msg["Font Helvetica-Bold not embedded"]  # "(2x on page 3)" is stripped into fields
    assert (font.severity, font.pages, font.occurrences) == ("error", [3], 2)
    res = by_msg["Color or grayscale image resolution is less than 225 ppi"]
    assert (res.pages, res.occurrences) == ([1, 2, 5], 4)  # Location@page is 0-based
    assert by_msg["Bleed is less than 3 mm"].severity == "warning"
    fixed = by_msg["Converted DeviceRGB to CMYK"]
    assert (fixed.severity, fixed.pages, fixed.occurrences) == ("fixed", list(range(1, 9)), 12)


def test_parse_pitstop_xml_v2():
    report = preflight.parse_report((FIXTURES / "pitstop_report_v2.xml").read_bytes())
    assert report.source_format == "pitstop-xml-v2"
    assert report.summary["reported_counts"]["warnings"] == 1
    assert [(f.severity, f.message, f.pages) for f in report.findings] == [
        ("warning", "Color or grayscale image resolution is less than 150 ppi", [1]),
        ("error", "PDF is encrypted", []),
        ("fixed", "Removed white overprint", [2, 4]),
    ]


def test_parse_pitstop_json():
    report = preflight.parse_report((FIXTURES / "pitstop_report.json").read_text())
    assert report.source_format == "pitstop-json"
    assert report.summary["profile"] == "PDF/X-4" and report.summary["pdf_version"] == "1.6"
    [err, fix] = report.findings
    assert (err.severity, err.message, err.pages, err.occurrences) == (
        "error", "Page size differs from 85 x 55 mm", [1, 2], 2)
    assert fix.severity == "fixed" and fix.pages == [1]


def test_namespaced_xml_is_supported():
    xml = """<r:EnfocusReport xmlns:r="urn:x"><r:PreflightReport><r:Warnings><r:PreflightReportItem>
             <r:Message>Text size is smaller than 6 pt (1x on page 4)</r:Message>
             </r:PreflightReportItem></r:Warnings></r:PreflightReport></r:EnfocusReport>"""
    [finding] = preflight.parse_report(xml).findings
    assert (finding.severity, finding.pages) == ("warning", [4])


def test_text_report_with_headings_and_prefixes():
    text = """Errors (1)
    - Font Arial not embedded (page 2)
    Warnings
    Image resolution 150 ppi (pages 1-3)
    Fixed: Removed white overprint
    x-height looks small
    Sign-offs
    Spot color PANTONE 185 C used (3x on pages 1, 4)
    """
    findings = preflight.parse_text_report(text).findings
    assert [(f.severity, f.pages) for f in findings] == [
        ("error", [2]), ("warning", [1, 2, 3]), ("fixed", []), ("warning", []), ("signed_off", [1, 4]),
    ]
    assert findings[3].message == "x-height looks small"
    assert findings[4].occurrences == 3


def test_categories():
    assert categorize("Image resolution 96 ppi is lower than 225 ppi").key == "low_resolution"
    assert categorize("Font Helvetica not embedded").key == "fonts_not_embedded"
    assert categorize("Object uses DeviceRGB").key == "rgb_color"
    assert categorize("Text is too close to the trim box").key == "safety_margin"
    assert categorize("Trim box size differs from 8.5 x 11 in").key == "page_size"
    assert categorize("Total area coverage exceeds 300%").key == "ink_coverage"
    assert categorize("Line width is less than 0.25 pt").key == "thin_lines"
    assert categorize("White text set to overprint").key == "overprint"
    assert categorize("Something unusual").key == "other"


def test_analyze_and_render_customer():
    report = preflight.parse_report((FIXTURES / "pitstop_report.xml").read_bytes())
    analysis = preflight.analyze(report)
    assert analysis["verdict"] == "needs_customer"
    assert analysis["counts"] == {"error": 2, "warning": 1, "fixed": 1}
    keys = [(i["category"], i["severity"]) for i in analysis["issues"]]
    assert keys[0][1] == "error" and ("rgb_color", "fixed") in keys

    text = preflight.render(analysis, "customer")
    assert text.startswith("We checked ORD1001_Brochure.pdf")
    assert "pages 1-2, 5" in text
    assert "ppi" not in text.split("We also")[0].replace("96 ppi", "")  # no jargon in explanations
    assert "RGB color" in text.split("We also")[1]


def test_verdict_prepress_can_fix():
    analysis = preflight.analyze(preflight.parse_text_report("Warning: Object uses DeviceRGB\nWarning: Hairline found"))
    assert analysis["verdict"] == "prepress_can_fix"
    assert "No action is needed" in preflight.render(analysis, "customer")


def test_verdict_ready_when_only_fixes():
    analysis = preflight.analyze(preflight.parse_text_report("Fixed: Converted RGB to CMYK"))
    assert analysis["verdict"] == "ready"
    assert preflight.render(analysis, "customer").startswith("Good news")


def test_render_csr_and_prepress():
    analysis = preflight.analyze(preflight.parse_report((FIXTURES / "pitstop_report.xml").read_bytes()))
    csr = preflight.render(analysis, "csr", "ORD1001")
    assert csr.startswith("ORD1001: Needs something from the customer")
    assert "owner: customer" in csr
    prepress = preflight.render(analysis, "prepress")
    assert "> Font Helvetica-Bold not embedded" in prepress
