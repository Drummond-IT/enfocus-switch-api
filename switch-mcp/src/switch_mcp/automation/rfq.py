"""Check a request for quote (RFQ) is complete before it reaches an estimator.

Claude (or a web form) extracts what the customer asked for; ``validate_rfq``
normalises it to a :class:`JobSpec`, lists what is missing for that kind of
product, flags combinations that can't be produced as asked, and writes the
questions to send back, in plain language. Nothing is priced.

Which fields a product needs is house-specific: adjust ``REQUIRED`` and
``QUESTIONS`` to match how your estimators quote.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .specs import JobSpec, SpecError

# Fields every quote needs, plus extras per product family.
BASE_REQUIRED = ["quantity", "trim", "colors", "stock"]
REQUIRED: dict[str, list[str]] = {
    "booklet": ["pages", "binding"],
    "catalog": ["pages", "binding"],
    "magazine": ["pages", "binding"],
    "book": ["pages", "binding"],
    "brochure": ["folding"],
    "envelope": [],
    "business card": [],
    "postcard": [],
    "flyer": [],
    "letterhead": [],
    "poster": [],
}
QUESTIONS = {
    "quantity": "How many do you need? (If you're not sure, we can quote a few quantities, e.g. 500 / 1,000 / 2,500.)",
    "trim": "What is the finished size (for example 8.5 x 11 in, or 85 x 55 mm)?",
    "colors": "Is it full color or black only, and does it print on one side or both?",
    "stock": "What paper would you like (e.g. 100 lb gloss text, 14 pt cover, uncoated)? We can suggest one.",
    "pages": "How many pages, counting the covers?",
    "binding": "How should it be bound: stapled (saddle stitch), perfect bound (glued spine), or coil?",
    "folding": "How should it fold (e.g. half fold, tri-fold, z-fold)?",
    "due_date": "When do you need it delivered?",
    "delivery": "Where should we deliver it, or will you pick it up?",
    "artwork": "Will you send print-ready PDF files, or do you need design help?",
}
OPTIONAL_ASKS = ("due_date", "delivery", "artwork")
KNOWN_FIELDS = {"product", "quantity", "trim", "trim_size", "size", "finished_size", "pages", "colors",
                "front_inks", "back_inks", "spot_colors", "stock", "binding", "folding", "finishing",
                "due_date", "delivery", "artwork", "customer", "notes", "bleed_in", "bleed_mm", "job_number"}


def _family(product: str | None) -> str | None:
    text = (product or "").lower()
    for family in sorted(REQUIRED, key=len, reverse=True):
        if family in text:
            return family
    return None


def _quantities(value: Any) -> list[int]:
    """'500, 1000 and 2,500' -> [500, 1000, 2500]."""
    if value in (None, ""):
        return []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [int(value)]
    if isinstance(value, (list, tuple)):
        return [q for v in value for q in _quantities(v)]
    numbers = re.findall(r"\d[\d,]{0,15}(?:\.\d{1,3})?\s?[kK]?", str(value)[:500])
    out = []
    for n in numbers:
        n = n.strip().replace(",", "")
        try:
            out.append(int(float(n[:-1]) * 1000) if n.lower().endswith("k") else int(float(n)))
        except (ValueError, OverflowError):
            continue
    return [q for q in out if q < 10**9][:10]


def validate_rfq(fields: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    raw = {str(k).lower().strip(): v for k, v in (fields or {}).items() if v not in (None, "", [])}
    problems: list[dict[str, str]] = []
    unknown = sorted(k for k in raw if k not in KNOWN_FIELDS)

    quantities = _quantities(raw.get("quantity"))
    spec_input = {k: v for k, v in raw.items() if k != "quantity"}
    if quantities:
        spec_input["quantity"] = quantities[0]
    try:
        spec = JobSpec.from_dict(spec_input)
        spec_error = None
    except (SpecError, ValueError, TypeError) as exc:
        spec, spec_error = JobSpec.from_dict({}), str(exc)
        problems.append({"severity": "error", "field": "spec", "message": spec_error})

    family = _family(spec.product)
    present = {
        "quantity": bool(quantities), "trim": spec.trim_in is not None, "colors": spec.front_inks is not None,
        "stock": bool(spec.stock), "pages": spec.pages is not None, "binding": bool(spec.binding),
        "folding": bool(raw.get("folding") or "fold" in str(raw.get("finishing", "")).lower()),
        "due_date": "due_date" in raw, "delivery": "delivery" in raw, "artwork": "artwork" in raw,
    }
    required = BASE_REQUIRED + (REQUIRED.get(family, []) if family else [])
    if not family and (spec.pages or 0) > 4:
        required = required + ["binding"]
    missing = [f for f in dict.fromkeys(required) if not present.get(f)]
    nice_to_have = [f for f in OPTIONAL_ASKS if not present.get(f)]

    binding = (spec.binding or "").lower()
    if spec.pages is not None:
        if spec.pages < 1:
            problems.append({"severity": "error", "field": "pages", "message": "Page count must be at least 1."})
        if "saddle" in binding or "stitch" in binding or "staple" in binding:
            if spec.pages % 4:
                problems.append({"severity": "error", "field": "pages", "message":
                                 f"Saddle-stitched booklets need a multiple of 4 pages; {spec.pages} isn't. "
                                 f"Ask whether it should be {spec.pages + (4 - spec.pages % 4)} (with blanks) "
                                 f"or {spec.pages - spec.pages % 4}."})
            if spec.pages > 96:
                problems.append({"severity": "warning", "field": "binding", "message":
                                 f"{spec.pages} pages is thick for saddle stitch; perfect binding may be better."})
        if "perfect" in binding and spec.pages < 28:
            problems.append({"severity": "warning", "field": "binding", "message":
                             f"{spec.pages} pages is thin for perfect binding (spine too narrow); "
                             "saddle stitch is usually better."})
        if spec.pages % 2 and spec.pages > 1 and family in ("booklet", "catalog", "magazine", "book"):
            problems.append({"severity": "warning", "field": "pages", "message":
                             "An odd page count means a blank page somewhere; confirm where."})
    if spec.trim_in and (min(spec.trim_in) < 1 or max(spec.trim_in) > 60):
        problems.append({"severity": "warning", "field": "trim", "message":
                         f'{spec.trim_in[0]:g}" x {spec.trim_in[1]:g}" is an unusual size; check the units.'})
    if any(q <= 0 for q in quantities):
        problems.append({"severity": "error", "field": "quantity", "message": "Quantity must be more than 0."})
    if raw.get("due_date"):
        try:
            due = date.fromisoformat(str(raw["due_date"])[:10])
            if due < (today or date.today()):  # noqa: DTZ011 - a local calendar date is what customers mean
                problems.append({"severity": "error", "field": "due_date",
                                 "message": f"The due date {due.isoformat()} is in the past."})
        except ValueError:
            pass  # free text like "next Friday": the estimator reads it

    questions = [QUESTIONS[f] for f in missing] + [QUESTIONS[f] for f in nice_to_have]
    questions += [p["message"] for p in problems if p["severity"] == "error" and p["field"] in ("pages", "spec")]
    ready = not missing and not any(p["severity"] == "error" for p in problems)
    return {
        "ready_to_quote": ready,
        "product_family": family,
        "spec": spec.as_dict() | ({"quantities": quantities} if len(quantities) > 1 else {}),
        "missing": missing,
        "also_ask": nice_to_have,
        "problems": problems,
        "questions_for_customer": questions,
        "ignored_fields": unknown,
        "summary": ("Ready to quote." if ready else
                    f"Not ready to quote: {len(missing)} missing detail(s)"
                    + (f", {sum(p['severity'] == 'error' for p in problems)} problem(s)" if problems else "") + "."),
    }
