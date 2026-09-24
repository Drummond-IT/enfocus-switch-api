"""Preflight analytics: which customers send problem files, and which problems.

Each explained report is recorded as one row in a local SQLite database
(``ANALYTICS_DB``): time, source, customer, job reference, verdict and the
issue categories. No file contents or report text are stored. Rows older
than ``ANALYTICS_RETENTION_DAYS`` (default 365) are deleted automatically.

Use it to target client education ("ACME's files are low-res 40% of the
time: send them the image guide") and to see whether things improve.

    enfocus-switch-mcp report --days 30      # command line
    preflight_stats(days=30)                 # MCP tool
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS preflight_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,              -- UTC ISO timestamp
    source TEXT NOT NULL,          -- switch_job | portal | tool | ...
    customer TEXT,
    job_ref TEXT,
    verdict TEXT NOT NULL,         -- ready | prepress_can_fix | needs_customer
    errors INTEGER NOT NULL,
    warnings INTEGER NOT NULL,
    fixed INTEGER NOT NULL,
    open_categories TEXT NOT NULL, -- comma separated
    accepted_categories TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS preflight_events_ts ON preflight_events(ts);
CREATE INDEX IF NOT EXISTS preflight_events_customer ON preflight_events(customer);
"""


def _connect(path: str | Path) -> sqlite3.Connection:
    Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(Path(path).expanduser()), timeout=10)
    conn.executescript(SCHEMA)
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(path: str | Path, analysis: dict[str, Any], source: str, job_ref: str | None = None,
           customer: str | None = None, retention_days: int = 365, when: datetime | None = None) -> None:
    issues = analysis.get("issues", [])
    open_cats = sorted({i["category"] for i in issues if i["severity"] in ("error", "warning")})
    accepted = sorted({i["category"] for i in issues if i["severity"] == "accepted"})
    counts = analysis.get("counts", {})
    ts = (when or _now()).isoformat(timespec="seconds")
    with closing(_connect(path)) as conn, conn:
        conn.execute(
            "INSERT INTO preflight_events (ts, source, customer, job_ref, verdict, errors, warnings, fixed, "
            "open_categories, accepted_categories) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, source, (customer or "").strip()[:200] or None, (job_ref or "")[:200] or None,
             analysis.get("verdict", "unknown"), int(counts.get("error", 0)), int(counts.get("warning", 0)),
             int(counts.get("fixed", 0)), ",".join(open_cats), ",".join(accepted)))
        cutoff = (_now() - timedelta(days=max(1, retention_days))).isoformat(timespec="seconds")
        conn.execute("DELETE FROM preflight_events WHERE ts < ?", (cutoff,))


def report(path: str | Path, days: int = 30, customer: str | None = None, top: int = 10) -> dict[str, Any]:
    since = (_now() - timedelta(days=max(1, days))).isoformat(timespec="seconds")
    query = "SELECT ts, customer, verdict, open_categories FROM preflight_events WHERE ts >= ?"
    params: list[Any] = [since]
    if customer:
        query += " AND lower(customer) = lower(?)"
        params.append(customer)
    if not Path(path).expanduser().exists():
        rows: list[tuple] = []
    else:
        with closing(_connect(path)) as conn:
            rows = conn.execute(query, params).fetchall()

    total = len(rows)
    verdicts = Counter(r[2] for r in rows)
    categories = Counter(c for r in rows for c in r[3].split(",") if c)
    by_customer: dict[str, dict[str, Any]] = {}
    for _, cust, verdict, cats in rows:
        entry = by_customer.setdefault(cust or "(unknown)", {"files": 0, "needs_customer": 0, "categories": Counter()})
        entry["files"] += 1
        entry["needs_customer"] += verdict == "needs_customer"
        entry["categories"].update(c for c in cats.split(",") if c)
    customers = sorted(
        ({"customer": name, "files": e["files"],
          "needs_customer_rate": round(e["needs_customer"] / e["files"], 2),
          "top_issues": [c for c, _ in e["categories"].most_common(3)]} for name, e in by_customer.items()),
        key=lambda c: (-c["needs_customer_rate"], -c["files"]))
    weekly = Counter(datetime.fromisoformat(r[0]).strftime("%G-W%V") for r in rows)
    weekly_bad = Counter(datetime.fromisoformat(r[0]).strftime("%G-W%V") for r in rows if r[2] == "needs_customer")
    return {
        "days": days,
        "customer": customer,
        "files": total,
        "verdicts": dict(verdicts),
        "needs_customer_rate": round(verdicts.get("needs_customer", 0) / total, 2) if total else None,
        "top_issues": [{"category": c, "files": n} for c, n in categories.most_common(top)],
        "customers": customers[:top * 3],
        "weekly": [{"week": w, "files": weekly[w], "needs_customer": weekly_bad.get(w, 0)} for w in sorted(weekly)],
    }


def render_markdown(r: dict[str, Any]) -> str:
    if not r["files"]:
        return f"No preflight results recorded in the last {r['days']} days."
    lines = [f"**Preflight results, last {r['days']} days**" + (f" ({r['customer']})" if r["customer"] else ""),
             f"- Files explained: {r['files']}; needed the customer: {int((r['needs_customer_rate'] or 0) * 100)}%",
             "", "**Most common issues**"]
    lines += [f"- {t['category']}: {t['files']} file(s)" for t in r["top_issues"]]
    if not r["customer"]:
        lines += ["", "**Customers whose files most often need them**"]
        lines += [f"- {c['customer']}: {int(c['needs_customer_rate'] * 100)}% of {c['files']} file(s); "
                  f"usually {', '.join(c['top_issues']) or '-'}" for c in r["customers"][:10]]
    return "\n".join(lines)
