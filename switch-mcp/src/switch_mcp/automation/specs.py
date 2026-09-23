"""JobSpec: a normalized job ticket.

A JobSpec can come from Pace (via ``pace.PaceGateway``), from a person typing
the order details, or from an RFQ email Claude has read. The parsers accept
the forms people actually write: "8.5 x 11", "8.5x11in", "85 x 55 mm",
"4/4", "4/0", "1/1", "4/4 + PMS 185 C".
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

MM_PER_INCH = 25.4


class SpecError(ValueError):
    """A spec value could not be understood; the message says which and why."""


def parse_size(value: Any) -> tuple[float, float] | None:
    """Parse a finished size into inches (width, height). Accepts "8.5 x 11", "85x55mm", [8.5, 11]."""
    if value in (None, ""):
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return float(value[0]), float(value[1])
    text = str(value).strip().lower().replace("×", "x").replace('"', " in")
    m = re.fullmatch(r"([\d.]+)\s*(mm|cm|in|inch|inches)?\s*x\s*([\d.]+)\s*(mm|cm|in|inch|inches)?", text)
    if not m:
        raise SpecError(f"Can't read size {value!r}. Use e.g. '8.5 x 11', '8.5x11 in' or '85 x 55 mm'.")
    w, unit1, h, unit2 = float(m.group(1)), m.group(2), float(m.group(3)), m.group(4)
    unit = unit2 or unit1 or "in"
    factor = {"mm": 1 / MM_PER_INCH, "cm": 10 / MM_PER_INCH}.get(unit, 1.0)
    return round(w * factor, 4), round(h * factor, 4)


def parse_colors(value: Any) -> tuple[int, int, list[str]]:
    """Parse "4/4", "4/0", "1/1", "4/4 + PMS 185 C, PMS 286" into (front inks, back inks, spot names)."""
    if value in (None, ""):
        raise SpecError("Colors are empty.")
    text = str(value).strip()
    m = re.match(r"\s*(\d)\s*/\s*(\d)\s*(.*)$", text)
    if not m:
        raise SpecError(f"Can't read colors {value!r}. Use e.g. '4/4', '4/0', '1/1' or '4/4 + PMS 185 C'.")
    rest = m.group(3).strip().lstrip("+,;").strip()
    spots = [s.strip() for s in re.split(r"[,;+]", rest) if s.strip()] if rest else []
    return int(m.group(1)), int(m.group(2)), spots


@dataclass
class JobSpec:
    """What the customer ordered. Only fields that are set are checked."""

    job_number: str | None = None
    customer: str | None = None
    product: str | None = None           # "business card", "booklet", "postcard", ...
    trim_in: tuple[float, float] | None = None
    pages: int | None = None             # PDF pages expected (e.g. 2 for a 2-sided flat)
    front_inks: int | None = None        # 4 = CMYK, 1 = black, 0 = blank
    back_inks: int | None = None
    spot_colors: list[str] = field(default_factory=list)
    bleed_in: float | None = 0.125
    quantity: int | None = None
    stock: str | None = None
    binding: str | None = None           # "saddle stitch", "perfect bound", ...
    notes: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobSpec:
        """Build from loose input (tool arguments, Pace query rows, extracted RFQ fields)."""
        d = {str(k).lower(): v for k, v in (data or {}).items() if v not in (None, "")}
        spec = cls()
        spec.job_number = str(d["job_number"]) if "job_number" in d else None
        spec.customer = d.get("customer")
        spec.product = d.get("product")
        spec.stock = d.get("stock")
        spec.binding = d.get("binding")
        spec.notes = d.get("notes")
        size = d.get("trim") or d.get("trim_size") or d.get("size") or d.get("finished_size")
        if size is None and "trim_width_in" in d and "trim_height_in" in d:
            size = (d["trim_width_in"], d["trim_height_in"])
        spec.trim_in = parse_size(size)
        for key in ("pages", "quantity"):
            if key in d:
                try:
                    setattr(spec, key, int(d[key]))
                except (TypeError, ValueError) as exc:
                    raise SpecError(f"{key} must be a whole number, got {d[key]!r}.") from exc
        if "colors" in d:
            spec.front_inks, spec.back_inks, spec.spot_colors = parse_colors(d["colors"])
        for key in ("front_inks", "back_inks"):
            if key in d:
                setattr(spec, key, int(d[key]))
        if "spot_colors" in d:
            spots = d["spot_colors"]
            spec.spot_colors = [s.strip() for s in (spots.split(",") if isinstance(spots, str) else spots) if s.strip()]
        if "bleed_in" in d:
            spec.bleed_in = float(d["bleed_in"])
        elif "bleed_mm" in d:
            spec.bleed_in = float(d["bleed_mm"]) / MM_PER_INCH
        return spec

    @property
    def sides(self) -> int | None:
        if self.back_inks is None:
            return None
        return 2 if self.back_inks > 0 else 1

    @property
    def colors_label(self) -> str | None:
        if self.front_inks is None:
            return None
        label = f"{self.front_inks}/{self.back_inks or 0}"
        return label + (" + " + ", ".join(self.spot_colors) if self.spot_colors else "")

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trim_in"] = list(self.trim_in) if self.trim_in else None
        d["colors"] = self.colors_label
        return {k: v for k, v in d.items() if v not in (None, [], "")}
