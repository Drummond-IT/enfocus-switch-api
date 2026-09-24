"""Customer-specific rules.

Some customers have standing agreements: they accept low-resolution images at
their own risk, or they pay us to fix bleed, or their products always use
1/16" bleed. Those live in one JSON file (``CUSTOMER_RULES``), e.g.:

    {
      "ACME Corporation": {
        "aliases": ["ACME", "Acme Corp"],
        "accept": ["low_resolution"],
        "prepress_fixes": ["missing_bleed", "fonts_not_embedded"],
        "ticket_defaults": {"bleed_in": 0.0625},
        "notes": "Signed low-res waiver 2025-03 (CSR: Pat)."
      }
    }

* ``accept``: issue categories the customer has accepted. They no longer make
  the verdict "needs customer"; CSR/prepress write-ups list them as accepted.
* ``prepress_fixes``: categories we fix in-house for this customer.
* ``ticket_defaults``: defaults for the file-vs-ticket check (e.g. bleed_in).

Categories are the keys in ``knowledge.py``. Names match case-insensitively.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import preflight
from ..knowledge import CATEGORIES

KNOWN_CATEGORIES = {c.key for c in CATEGORIES} | {"other"}
ALLOWED_DEFAULTS = {"bleed_in", "binding"}


@dataclass
class CustomerRule:
    customer: str
    aliases: list[str] = field(default_factory=list)
    accept: list[str] = field(default_factory=list)
    prepress_fixes: list[str] = field(default_factory=list)
    ticket_defaults: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def summary(self) -> dict[str, Any]:
        return {"customer": self.customer, "accept": self.accept, "prepress_fixes": self.prepress_fixes,
                "ticket_defaults": self.ticket_defaults, "notes": self.notes}


def _norm(name: str) -> str:
    return " ".join(str(name).lower().replace(",", " ").replace(".", " ").split())


class CustomerRules:
    def __init__(self, rules: list[CustomerRule]):
        self.rules = rules
        self._index: dict[str, CustomerRule] = {}
        for r in rules:
            for name in [r.customer, *r.aliases]:
                self._index[_norm(name)] = r

    @classmethod
    def load(cls, path: str | Path | None) -> CustomerRules:
        if not path:
            return cls([])
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be a JSON object: customer name -> rule.")  # noqa: TRY004 - config error
        rules = []
        for name, raw in data.items():
            if name.startswith("_"):
                continue
            if not isinstance(raw, dict):
                raise ValueError(f"Rule for {name!r} must be an object.")  # noqa: TRY004 - config error
            unknown = [k for k in raw if k not in {"aliases", "accept", "prepress_fixes", "ticket_defaults", "notes"}]
            if unknown:
                raise ValueError(f"Rule for {name!r} has unknown keys: {', '.join(unknown)}.")
            for key in ("accept", "prepress_fixes"):
                bad = [c for c in raw.get(key, []) if c not in KNOWN_CATEGORIES]
                if bad:
                    raise ValueError(f"Rule for {name!r}: unknown categories in {key}: {', '.join(bad)}. "
                                     f"Known: {', '.join(sorted(KNOWN_CATEGORIES))}.")
            bad_defaults = [k for k in raw.get("ticket_defaults", {}) if k not in ALLOWED_DEFAULTS]
            if bad_defaults:
                raise ValueError(f"Rule for {name!r}: ticket_defaults may only set {', '.join(sorted(ALLOWED_DEFAULTS))}.")
            rules.append(CustomerRule(
                customer=name, aliases=list(raw.get("aliases", [])), accept=list(raw.get("accept", [])),
                prepress_fixes=list(raw.get("prepress_fixes", [])),
                ticket_defaults=dict(raw.get("ticket_defaults", {})), notes=str(raw.get("notes", "")),
            ))
        return cls(rules)

    def find(self, customer: str | None) -> CustomerRule | None:
        return self._index.get(_norm(customer)) if customer else None


def apply_to_analysis(analysis: dict[str, Any], rule: CustomerRule | None) -> dict[str, Any]:
    """Return a copy of an ``analyze()`` result with the customer's standing agreements applied."""
    if rule is None:
        return analysis
    out = copy.deepcopy(analysis)
    for issue in out["issues"]:
        if issue["severity"] not in ("error", "warning"):
            continue
        if issue["category"] in rule.accept:
            issue["original_severity"] = issue["severity"]
            issue["severity"] = "accepted"
        elif issue["category"] in rule.prepress_fixes:
            issue["fix_owner"] = "prepress"
    out["verdict"], out["verdict_text"] = preflight.decide_verdict(out["issues"])
    out["customer_rule"] = rule.summary()
    return out


def customer_from_job(job: dict[str, Any]) -> str | None:
    """Customer name from a Switch job's custom fields (any field whose name contains 'customer')."""
    for f in job.get("customFields") or []:
        if "customer" in str(f.get("name", "")).lower() and str(f.get("value", "")).strip():
            return str(f["value"]).strip()[:200]
    return None


def same_customer(a: str | None, b: str | None, rules: CustomerRules | None = None) -> bool:
    """True when two customer names refer to the same customer (exact after normalising, or via aliases)."""
    if not a or not b:
        return False
    if _norm(a) == _norm(b):
        return True
    rule_a = rules.find(a) if rules else None
    return rule_a is not None and rule_a is rules.find(b)


def spec_defaults(rule: CustomerRule | None) -> dict[str, Any]:
    return dict(rule.ticket_defaults) if rule else {}
