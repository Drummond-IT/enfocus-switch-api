"""Compare a print PDF against its job ticket.

Catches the expensive surprises before prepress touches the file: wrong
size, wrong page count, a color file on a black-only ticket, a spot color
the ticket doesn't pay for, missing bleed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .pdf_facts import PdfFacts
from .specs import JobSpec

SIZE_TOLERANCE_IN = 1 / 32  # a common prepress tolerance; tune to house standard
BLEED_TOLERANCE_IN = 0.01


@dataclass
class Mismatch:
    check: str
    severity: str        # "error" (won't print as ordered) | "warning" (ask) | "info"
    expected: str
    found: str
    message: str         # plain language, CSR-friendly
    pages: list[int] | None = None


def _fmt_size(size: tuple[float, float]) -> str:
    return f'{size[0]:g}" x {size[1]:g}"'


def _same_size(a: tuple[float, float], b: tuple[float, float], tol: float) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(sorted(a), sorted(b)))


def _norm_spot(name: str) -> str:
    """'PANTONE 185 C' / 'PMS 185C' / 'Pantone 185 CV' -> '185'."""
    n = name.upper().replace("PANTONE", "").replace("PMS", "")
    m = re.search(r"\d{2,4}", n)
    return m.group(0) if m else re.sub(r"\W", "", n)


def compare(facts: PdfFacts, spec: JobSpec, size_tolerance_in: float = SIZE_TOLERANCE_IN) -> dict[str, Any]:
    issues: list[Mismatch] = []

    if spec.trim_in:
        wrong = [p.page for p in facts.pages if not _same_size(p.trim_in, spec.trim_in, size_tolerance_in)]
        if wrong:
            found = ", ".join(_fmt_size(s) for s in facts.trim_sizes())
            proportional = all(
                abs(p.trim_in[0] / p.trim_in[1] - spec.trim_in[0] / spec.trim_in[1]) < 0.01
                or abs(p.trim_in[1] / p.trim_in[0] - spec.trim_in[0] / spec.trim_in[1]) < 0.01
                for p in facts.pages if p.page in wrong)
            issues.append(Mismatch(
                "trim_size", "error", _fmt_size(spec.trim_in), found,
                f"The file is {found} but the order is {_fmt_size(spec.trim_in)}."
                + (" It's the same shape, so it could be scaled with the customer's OK." if proportional
                   else " It's a different shape, so it needs new artwork or a decision from the customer."),
                wrong))

    if spec.pages is not None and facts.page_count != spec.pages:
        issues.append(Mismatch(
            "page_count", "error", str(spec.pages), str(facts.page_count),
            f"The file has {facts.page_count} page(s); the order is for {spec.pages}."))
    binding = (spec.binding or spec.product or "").lower()
    if "saddle" in binding and facts.page_count % 4:
        issues.append(Mismatch(
            "page_count", "error", "multiple of 4", str(facts.page_count),
            f"Saddle-stitched booklets need a page count divisible by 4; the file has {facts.page_count}."))

    if spec.front_inks is not None:
        sides = [facts.pages[0]] if facts.pages else []
        if spec.back_inks and facts.page_count >= 2:
            sides.append(facts.pages[1])
        is_flat = spec.pages in (None, 1, 2) and facts.page_count <= 2
        expected = {1: spec.front_inks, 2: spec.back_inks or 0}
        for p in (sides if is_flat else facts.pages):
            ordered = expected.get(p.page, spec.front_inks) if is_flat else max(spec.front_inks, spec.back_inks or 0)
            if not p.analysed:
                continue
            if ordered < 4 and p.is_color:
                issues.append(Mismatch(
                    "colors", "error", f"{ordered}-color", "full color" + (" (RGB)" if p.rgb else ""),
                    f"Page {p.page} is in full color but the order is {spec.colors_label}. Either the order "
                    "needs to change (price) or the file should be converted to black/spot only.", [p.page]))
            elif ordered == 0 and (p.process or p.spots or p.rgb):
                issues.append(Mismatch(
                    "colors", "warning", "blank", "printed content",
                    f"Page {p.page} has printed content but the order says that side is blank.", [p.page]))
        if is_flat and spec.back_inks == 0 and facts.page_count > 1:
            issues.append(Mismatch(
                "sides", "warning", "1-sided", f"{facts.page_count} pages",
                "The order is single-sided but the file has a back page. Is the back meant to print?"))
        if is_flat and (spec.back_inks or 0) > 0 and facts.page_count == 1:
            issues.append(Mismatch(
                "sides", "error", "2-sided", "1 page",
                "The order is double-sided but the file only has one page. We need the back artwork."))
        if any(p.rgb for p in facts.pages):
            issues.append(Mismatch(
                "colors", "info", "CMYK", "RGB content",
                "Some content is RGB; prepress will convert it to CMYK (colors may shift slightly).",
                [p.page for p in facts.pages if p.rgb]))

    ordered_spots = {_norm_spot(s) for s in spec.spot_colors}
    found_spots = {_norm_spot(s): s for s in facts.spot_colors}
    extra = [name for key, name in found_spots.items() if key not in ordered_spots]
    missing = [s for s in spec.spot_colors if _norm_spot(s) not in found_spots]
    if extra:
        issues.append(Mismatch(
            "spot_colors", "warning" if spec.front_inks == 4 else "error",
            ", ".join(spec.spot_colors) or "none", ", ".join(sorted(extra)),
            f"The file uses spot color(s) {', '.join(sorted(extra))} that aren't on the order. "
            + ("They'll be converted to CMYK unless the customer wants (and pays for) spot ink."
               if spec.front_inks == 4 else "Confirm the inks with the customer.")))
    if missing and facts.pages and all(p.analysed for p in facts.pages):
        issues.append(Mismatch(
            "spot_colors", "warning", ", ".join(spec.spot_colors), "not found",
            f"The order includes {', '.join(missing)} but the file doesn't use it. Check the artwork was built "
            "with the right swatch name."))

    if spec.bleed_in:
        # 3 mm (0.118") is the metric equivalent of 1/8" (0.125"), so allow a small tolerance.
        missing = [p.page for p in facts.pages if not p.bleed_in]
        short = [p for p in facts.pages if p.bleed_in and p.bleed_in + BLEED_TOLERANCE_IN < spec.bleed_in]
        if missing:
            issues.append(Mismatch(
                "bleed", "warning", f'{spec.bleed_in:g}"', "none",
                f'Page(s) {", ".join(map(str, missing[:20]))} have no bleed. Fine if nothing prints to the edge; '
                "otherwise prepress extends it or the customer resends.", missing))
        if short:
            found = min(p.bleed_in for p in short)
            issues.append(Mismatch(
                "bleed", "warning", f'{spec.bleed_in:g}"', f'{found:.3f}"',
                f'Page(s) {", ".join(str(p.page) for p in short[:20])} have only {found:.3f}" bleed '
                f'({found * 25.4:.1f} mm); {spec.bleed_in:g}" is required. Prepress can usually extend it.',
                [p.page for p in short]))

    errors = [i for i in issues if i.severity == "error"]
    verdict = "matches_ticket" if not issues or all(i.severity == "info" for i in issues) else \
        ("does_not_match" if errors else "check_with_customer")
    return {
        "verdict": verdict,
        "job_number": spec.job_number,
        "ticket": spec.as_dict(),
        "file": {k: v for k, v in facts.as_dict().items() if k != "pages"},
        "issues": [asdict(i) for i in issues],
    }
