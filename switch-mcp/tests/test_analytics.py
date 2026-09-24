import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.client import Client

from switch_mcp import preflight
from switch_mcp.automation import analytics

FIXTURES = Path(__file__).parent / "fixtures"


def _analysis(text):
    return preflight.analyze(preflight.parse_text_report(text))


def test_record_and_report(tmp_path):
    db = tmp_path / "a.sqlite"
    bad = _analysis("Error: image resolution 72 ppi\nWarning: bleed missing")
    good = _analysis("Fixed: Converted RGB to CMYK")
    analytics.record(db, bad, "tool", "J1_card.pdf", "ACME")
    analytics.record(db, bad, "tool", "J2_card.pdf", "ACME")
    analytics.record(db, good, "tool", "J3.pdf", "Beta Co")
    r = analytics.report(db, days=30)
    assert r["files"] == 3 and r["verdicts"] == {"needs_customer": 2, "ready": 1}
    assert r["top_issues"][0] == {"category": "low_resolution", "files": 2}
    assert r["customers"][0] == {"customer": "ACME", "files": 2, "needs_customer_rate": 1.0,
                                 "top_issues": ["low_resolution", "missing_bleed"]}
    assert analytics.report(db, customer="acme")["files"] == 2
    md = analytics.render_markdown(r)
    assert "ACME: 100% of 2 file(s)" in md


def test_no_content_is_stored_and_retention(tmp_path):
    db = tmp_path / "a.sqlite"
    analysis = _analysis("Error: Font SecretClientFont not embedded")
    old = datetime.now(timezone.utc) - timedelta(days=400)
    analytics.record(db, analysis, "tool", when=old)
    analytics.record(db, analysis, "tool")
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT * FROM preflight_events").fetchall()
    assert len(rows) == 1, "rows older than the retention window are purged"
    assert "SecretClientFont" not in repr(rows), "only categories are stored, never report text"


def test_empty_report(tmp_path):
    r = analytics.report(tmp_path / "missing.sqlite")
    assert r["files"] == 0 and analytics.render_markdown(r).startswith("No preflight results")


async def test_tools_record_and_stats(make_server, tmp_path):
    db = tmp_path / "a.sqlite"
    server = make_server(analytics_db=str(db))
    async with Client(server) as c:
        await c.call_tool("explain_job_report", {"job_id": "job-1", "customer": "ACME"})
        await c.call_tool("explain_preflight_report", {"report": "Warning: RGB image", "customer": "Beta"})
        r = await c.call_tool("preflight_stats", {"days": 7})
    assert r.structured_content["files"] == 2
    assert {c["customer"] for c in r.structured_content["customers"]} == {"ACME", "Beta"}
    async with Client(make_server()) as c:
        assert "preflight_stats" not in {t.name for t in (await c.list_tools()).tools}


async def test_analytics_failure_does_not_break_tools(make_server, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    server = make_server(analytics_db=str(blocker / "sub" / "a.sqlite"))  # parent is a file: can't create
    async with Client(server) as c:
        r = await c.call_tool("explain_preflight_report", {"report": "Warning: RGB image"})
    assert not r.is_error
