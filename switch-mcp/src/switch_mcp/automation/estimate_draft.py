"""Draft an item spec (and a Pace item-template payload) from a print PDF.

This is a starting point for an estimator, never a price: it reads what the
file says (finished size, pages, sides, inks, bleed) and guesses the product
from standard sizes. Stock, quantity, finishing and turnaround still come
from the customer.

``to_item_template`` maps the draft onto your Pace item template fields
using a field map (JSON), so the payload can be reviewed and then entered
through the Pace API / Pace MCP by a person.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .pdf_facts import PdfFacts

# (width, height) in inches, either orientation. Extend with house products.
STANDARD_PRODUCTS: list[tuple[str, tuple[float, float]]] = [
    ("business card", (3.5, 2.0)),
    ("business card (EU 85x55mm)", (3.346, 2.165)),
    ("postcard 4x6", (6.0, 4.0)),
    ("postcard 5x7", (7.0, 5.0)),
    ("postcard 6x9", (9.0, 6.0)),
    ("postcard 6x11", (11.0, 6.0)),
    ("rack card", (4.0, 9.0)),
    ("door hanger", (4.25, 11.0)),
    ("letter / flyer", (8.5, 11.0)),
    ("half letter", (5.5, 8.5)),
    ("legal", (8.5, 14.0)),
    ("tabloid", (11.0, 17.0)),
    ("#10 envelope", (9.5, 4.125)),
    ("A4", (8.268, 11.693)),
    ("A5", (5.827, 8.268)),
    ("A6", (4.134, 5.827)),
    ("poster 18x24", (18.0, 24.0)),
    ("poster 24x36", (24.0, 36.0)),
]
SIZE_MATCH_TOLERANCE_IN = 0.07


@dataclass
class ItemDraft:
    product_guess: str
    trim_in: tuple[float, float] | None
    pages: int
    sides: int | None
    front_inks: str
    back_inks: str | None
    spot_colors: list[str]
    bleed: bool
    binding_guess: str | None
    confidence: str                      # high | medium | low
    notes: list[str] = field(default_factory=list)
    needs_from_customer: list[str] = field(
        default_factory=lambda: ["quantity", "stock / paper", "finishing (cutting, folding, coating)",
                                 "due date", "shipping / delivery"])

    @property
    def colors_label(self) -> str:
        label = f"{self.front_inks}/{self.back_inks or 0}"
        return label + (" + " + ", ".join(self.spot_colors) if self.spot_colors else "")

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trim_in"] = list(self.trim_in) if self.trim_in else None
        d["colors"] = self.colors_label
        return d


def _match_product(size: tuple[float, float]) -> str | None:
    for name, std in STANDARD_PRODUCTS:
        if all(abs(a - b) <= SIZE_MATCH_TOLERANCE_IN for a, b in zip(sorted(size), sorted(std))):
            return name
    return None


def draft_from_facts(facts: PdfFacts) -> ItemDraft:
    notes: list[str] = []
    sizes = facts.trim_sizes()
    trim = sizes[0] if sizes else None
    if len(sizes) > 1:
        notes.append(f"Pages have {len(sizes)} different sizes; the first size is used. Check for covers/inserts.")
    n = facts.page_count
    product = _match_product(trim) if trim else None

    if n == 1:
        sides, binding = 1, None
    elif n == 2:
        sides, binding = 2, None
    elif n % 4 == 0:
        sides, binding = 2, "saddle stitch" if n <= 64 else "perfect bound"
        product = f"{n}-page booklet" + (f" ({product})" if product else "")
    else:
        sides, binding = None, None
        notes.append(f"{n} pages isn't a flat piece or a standard booklet count: a multi-up file, a set of "
                     "versions, or a document needing blanks added?")

    def inks(p: Any) -> str:
        return p.inks() if p else "0"

    front = facts.pages[0] if facts.pages else None
    back = facts.pages[1] if n == 2 else None
    if n > 2:  # booklet: report the "heaviest" page for both sides
        heavy = max(facts.pages, key=lambda p: (p.is_color, len(p.spots)))
        front_inks = back_inks = inks(heavy)
    else:
        front_inks, back_inks = inks(front), (inks(back) if back else None)
    if any(p.rgb for p in facts.pages):
        notes.append("Contains RGB; priced as 4-color, converted to CMYK in prepress.")
    bleed = bool(facts.pages) and all(p.bleed_in and p.bleed_in >= 0.1 for p in facts.pages)
    if not bleed:
        notes.append("No (or short) bleed: fine for white-border designs, otherwise the file needs bleed.")

    confidence = "high" if product and not notes else "medium" if product or sides else "low"
    return ItemDraft(
        product_guess=product or (f'custom {trim[0]:g}" x {trim[1]:g}"' if trim else "unknown"),
        trim_in=trim, pages=n, sides=sides, front_inks=front_inks, back_inks=back_inks,
        spot_colors=sorted(facts.spot_colors), bleed=bleed, binding_guess=binding,
        confidence=confidence, notes=notes,
    )


# ------------------------------------------------------------------ Pace item template

DEFAULT_FIELD_MAP: dict[str, str] = {
    # draft field  -> Pace item template field.  PLACEHOLDERS: replace with the field names of
    # your Pace item templates (Pace admin > item templates), then save as JSON and point
    # PACE_ITEM_TEMPLATE_MAP at it.
    "product_guess": "product",
    "trim_width_in": "finishedWidth",
    "trim_height_in": "finishedHeight",
    "pages": "pages",
    "front_inks": "colorsSide1",
    "back_inks": "colorsSide2",
    "spot_colors": "specialColors",
    "bleed": "bleeds",
    "binding_guess": "binding",
}


def load_field_map(path: str | Path | None) -> dict[str, str]:
    if not path:
        return dict(DEFAULT_FIELD_MAP)
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if not k.startswith("_")}
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError(f"{path} must be a JSON object of draft field -> Pace field names.")
    return data


def to_item_template(draft: ItemDraft, field_map: dict[str, str], template: str | None = None) -> dict[str, Any]:
    """A reviewable payload for a Pace item template. Nothing is sent anywhere."""
    values: dict[str, Any] = {
        "product_guess": draft.product_guess,
        "trim_width_in": draft.trim_in[0] if draft.trim_in else None,
        "trim_height_in": draft.trim_in[1] if draft.trim_in else None,
        "pages": draft.pages,
        "front_inks": draft.front_inks,
        "back_inks": draft.back_inks,
        "spot_colors": ", ".join(draft.spot_colors) or None,
        "bleed": draft.bleed,
        "binding_guess": draft.binding_guess,
    }
    fields = {field_map[k]: v for k, v in values.items() if k in field_map and v not in (None, "")}
    return {
        "item_template": template,
        "fields": fields,
        "still_needed": draft.needs_from_customer,
        "status": "DRAFT - review before entering in Pace",
    }
