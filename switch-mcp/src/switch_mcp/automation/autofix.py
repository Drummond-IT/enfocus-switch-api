"""Turn a preflight analysis into a fix plan.

Which issues your flows can fix automatically is house-specific, so it lives
in a JSON map (``SWITCH_AUTOFIX_MAP``), for example:

    {
      "rgb_color":     {"route_to": "Auto-fix", "action_list": "Convert to press CMYK"},
      "overprint":     {"route_to": "Auto-fix", "action_list": "Remove white overprint"},
      "thin_lines":    {"route_to": "Auto-fix", "action_list": "Thicken hairlines 0.25pt"},
      "missing_bleed": {"route_to": "Auto-fix", "action_list": "Mirror bleed 0.125in", "needs_review": true}
    }

``route_to`` is the name of the checkpoint's outgoing connection that leads
to your auto-fix branch; ``action_list`` is informational (what that branch runs).
Categories come from ``knowledge.py`` (low_resolution, rgb_color, ...).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_autofix_map(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if not k.startswith("_")}
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise ValueError(f"{path} must be a JSON object: category -> {{route_to, action_list, ...}}.")
    return data


def plan_fixes(analysis: dict[str, Any], autofix_map: dict[str, dict[str, Any]],
               out_connections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Split open issues into: auto-fixable by a configured route, prepress by hand, customer."""
    auto, manual, customer = [], [], []
    for issue in analysis.get("issues", []):
        if issue["severity"] not in ("error", "warning"):
            continue
        rule = autofix_map.get(issue["category"])
        entry = {"category": issue["category"], "title": issue["title"], "pages": issue["pages"]}
        if rule:
            auto.append(entry | {"route_to": rule.get("route_to"), "action_list": rule.get("action_list"),
                                 "needs_review": bool(rule.get("needs_review"))})
        elif issue["fix_owner"] == "customer":
            customer.append(entry)
        else:
            manual.append(entry | {"hint": issue["explanation"]["prepress"]})

    routes = {a["route_to"] for a in auto if a["route_to"]}
    available = {str(c.get("name", "")).lower(): c for c in (out_connections or [])}
    suggestion: dict[str, Any] | None = None
    if customer:
        suggestion = {"action": "ask_customer", "reason": "Some issues need the customer (see 'customer')."}
    elif manual:
        suggestion = {"action": "prepress", "reason": "Some issues need prepress by hand (see 'prepress_manual')."}
    elif len(routes) == 1:
        route = next(iter(routes))
        conn = available.get(route.lower())
        suggestion = {
            "action": "route", "connection": route, "connection_available": conn is not None,
            "reason": "Every open issue is covered by the auto-fix branch.",
        }
        if any(a["needs_review"] for a in auto):
            suggestion["reason"] += " At least one fix should be checked on a proof afterwards."
    elif len(routes) > 1:
        suggestion = {"action": "prepress", "reason": f"Fixes need different routes ({', '.join(sorted(routes))})."}
    elif not auto:
        suggestion = {"action": "none", "reason": "No open issues."}

    return {
        "verdict": analysis.get("verdict"),
        "auto_fixable": auto,
        "prepress_manual": manual,
        "customer": customer,
        "suggestion": suggestion,
        "configured": bool(autofix_map),
    }
