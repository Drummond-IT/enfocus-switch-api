"""Morning digest and stuck-job alerts.

``build_digest`` collects: jobs waiting in checkpoints (flagging those waiting
longer than ``stuck_after_hours``), flows that aren't running, and recurring
errors from the message log. ``render_markdown`` turns it into a message for
Teams/Slack/email; ``post_webhook`` sends it to an incoming-webhook URL.

Run from the command line (e.g. a scheduled task at 7:00):

    enfocus-switch-mcp digest                 # print it
    enfocus-switch-mcp digest --post          # also send to DIGEST_WEBHOOK_URL
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import httpx

from ..client import SwitchClient


def hours_since(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)


def group_messages(messages: list[dict[str, Any]], top: int = 15) -> list[dict[str, Any]]:
    """Group messages by flow, element and message pattern (numbers replaced with #)."""
    counter = Counter(
        (m.get("flow"), m.get("element"), re.sub(r"\d+", "#", str(m.get("message", "")))[:160]) for m in messages)
    return [{"flow": f, "element": e, "message_pattern": msg, "count": c}
            for (f, e, msg), c in counter.most_common(top)]


async def build_digest(switch: SwitchClient, hours: int = 16, stuck_after_hours: float = 4.0) -> dict[str, Any]:
    jobs = (await switch.list_jobs(filter_query={"and": [{"status": {"is": "alert"}}]}, sort="onAlertSince",
                                   limit=500)).get("data") or []
    waiting = []
    for j in jobs:
        waited = hours_since(j.get("onAlertSince"))
        waiting.append({"id": j.get("id"), "name": j.get("name"), "flow": j.get("flowName"),
                        "checkpoint": j.get("checkpointName"), "waiting_hours": waited,
                        "stuck": waited is not None and waited >= stuck_after_hours})
    flows = await switch.list_flows(fields=["id", "name", "status"])
    not_running = [{"id": f.get("id"), "name": f.get("name"), "status": f.get("status")}
                   for f in flows if str(f.get("status", "")).lower() not in ("running", "removed")]
    errors = (await switch.messages(period=f"{max(1, hours)}h", type="error", limit=5000)).get("messages", [])
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "hours": hours,
        "stuck_after_hours": stuck_after_hours,
        "waiting": waiting,
        "stuck": [w for w in waiting if w["stuck"]],
        "flows_not_running": not_running,
        "error_count": len(errors),
        "top_errors": group_messages(errors, top=8),
    }


def render_markdown(d: dict[str, Any]) -> str:
    lines = [f"**Switch digest** ({d['generated']})", ""]
    lines.append(f"- Waiting in checkpoints: **{len(d['waiting'])}**"
                 f" ({len(d['stuck'])} waiting over {d['stuck_after_hours']:g} h)")
    lines.append(f"- Errors in the last {d['hours']} h: **{d['error_count']}**")
    if d["flows_not_running"]:
        lines.append("- Flows not running: " + ", ".join(f"{f['name']} ({f['status']})"
                                                         for f in d["flows_not_running"]))
    if d["stuck"]:
        lines += ["", "**Needs attention (longest first)**"]
        for w in d["stuck"][:15]:
            lines.append(f"- {w['name']} in *{w['checkpoint']}* ({w['flow']}): {w['waiting_hours']:g} h")
    if d["top_errors"]:
        lines += ["", "**Recurring errors**"]
        for e in d["top_errors"]:
            lines.append(f"- {e['count']}x {e['flow']} / {e['element']}: {e['message_pattern']}")
    return "\n".join(lines)


def post_webhook(url: str, text: str, timeout: float = 30.0) -> None:
    """Send to a Teams/Slack-style incoming webhook (JSON body {"text": ...})."""
    if not url.startswith("https://"):
        raise ValueError("DIGEST_WEBHOOK_URL must be an https:// URL.")
    resp = httpx.post(url, json={"text": text}, timeout=timeout)
    resp.raise_for_status()
