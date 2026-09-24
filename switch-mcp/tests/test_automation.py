"""Tests for switch_mcp.automation: specs, PDF facts, ticket check, estimate draft,
auto-fix plan, job matching, Pace gateway, proof approval workflow, digest."""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from mcp.client import Client
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject

from switch_mcp.automation import autofix, digest, estimate_draft, job_matching, pace, ticket_check, workflows
from switch_mcp.automation.pdf_facts import analyse_pdf
from switch_mcp.automation.specs import JobSpec, SpecError, parse_colors, parse_size
from switch_mcp.client import SwitchClient, SwitchError
from switch_mcp.server import build_server

# ------------------------------------------------------------------ PDF builder

CMYK_BOX = b"0.8 0.1 0 0 k 10 10 50 50 re f"
BLACK_TEXT = b"0 0 0 1 k 10 10 50 50 re f"
GRAY = b"0.2 g 10 10 50 50 re f"
RGB = b"1 0 0 rg 10 10 50 50 re f"
SPOT = b"/CS0 cs 1 scn 10 10 50 50 re f"


def make_pdf(pages: list[bytes], size_in=(8.5, 11.0), bleed_in: float | None = 0.125) -> bytes:
    """Each page gets the given content stream; /CS0 is a PANTONE 185 C Separation."""
    w = PdfWriter()
    b = (bleed_in or 0) * 72
    tw, th = size_in[0] * 72, size_in[1] * 72
    sep = ArrayObject([NameObject("/Separation"), NameObject("/PANTONE 185 C"), NameObject("/DeviceCMYK"),
                       DictionaryObject({NameObject("/FunctionType"): FloatObject(2)})])
    for content in pages:
        page = w.add_blank_page(tw + 2 * b, th + 2 * b)
        if bleed_in is not None:
            page[NameObject("/TrimBox")] = ArrayObject([FloatObject(v) for v in (b, b, b + tw, b + th)])
        stream = DecodedStreamObject()
        stream.set_data(content)
        page[NameObject("/Contents")] = w._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/ColorSpace"): DictionaryObject({NameObject("/CS0"): sep})})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ specs

def test_parse_size_and_colors():
    assert parse_size("8.5 x 11") == (8.5, 11.0)
    assert parse_size('8.5" x 11"') == (8.5, 11.0)
    assert parse_size("85 x 55 mm") == (3.3465, 2.1654)
    assert parse_size([4, 6]) == (4.0, 6.0)
    assert parse_colors("4/4") == (4, 4, [])
    assert parse_colors("4/0 + PMS 185 C, PMS 286") == (4, 0, ["PMS 185 C", "PMS 286"])
    with pytest.raises(SpecError, match="Can't read size"):
        parse_size("letter")
    with pytest.raises(SpecError, match="Can't read colors"):
        parse_colors("full color")


def test_jobspec_from_loose_dict():
    s = JobSpec.from_dict({"Job_Number": 123456, "trim_width_in": 3.5, "trim_height_in": 2, "colors": "4/4",
                           "pages": "2", "bleed_mm": 3})
    assert (s.job_number, s.trim_in, s.sides, s.colors_label) == ("123456", (3.5, 2.0), 2, "4/4")
    assert abs(s.bleed_in - 0.118) < 0.001
    with pytest.raises(SpecError, match="pages must be a whole number"):
        JobSpec.from_dict({"pages": "two"})


# ------------------------------------------------------------------ PDF facts

def test_pdf_facts_detects_inks_rgb_and_spots():
    facts = analyse_pdf(make_pdf([CMYK_BOX, BLACK_TEXT + b" " + GRAY, RGB, SPOT]), "t.pdf")
    p1, p2, p3, p4 = facts.pages
    assert p1.process == {"C", "M"} and p1.is_color and p1.inks() == "4"
    assert p2.process == {"K"} and not p2.is_color and p2.inks() == "1"
    assert p3.rgb and p3.is_color
    assert p4.spots == {"PANTONE 185 C"} and p4.inks() == "1"
    assert facts.trim_sizes() == [(8.5, 11.0)] and abs(p1.bleed_in - 0.125) < 0.001


def test_pdf_facts_on_repo_sample():
    sample = Path(__file__).resolve().parents[2] / "src" / "Files" / "curl_test_file.pdf"
    facts = analyse_pdf(sample.read_bytes())
    assert facts.page_count == 2 and facts.trim_sizes() == [(3.35, 2.17)]


# ------------------------------------------------------------------ ticket check

def test_ticket_match():
    facts = analyse_pdf(make_pdf([CMYK_BOX, CMYK_BOX]))
    r = ticket_check.compare(facts, JobSpec.from_dict({"trim": "8.5x11", "pages": 2, "colors": "4/4"}))
    assert r["verdict"] == "matches_ticket" and r["issues"] == []


def test_ticket_mismatches():
    facts = analyse_pdf(make_pdf([CMYK_BOX, RGB, SPOT], size_in=(8.5, 14), bleed_in=None))
    r = ticket_check.compare(facts, JobSpec.from_dict({"trim": "8.5 x 11", "pages": 2, "colors": "1/1"}))
    checks = {(i["check"], i["severity"]) for i in r["issues"]}
    assert r["verdict"] == "does_not_match"
    assert ("trim_size", "error") in checks and ("page_count", "error") in checks
    assert ("colors", "error") in checks and ("spot_colors", "error") in checks and ("bleed", "warning") in checks
    size = next(i for i in r["issues"] if i["check"] == "trim_size")
    assert "different shape" in size["message"]


def test_ticket_single_sided_and_spot_names():
    facts = analyse_pdf(make_pdf([SPOT]))
    r = ticket_check.compare(facts, JobSpec.from_dict({"colors": "4/4 + PMS 185"}))
    assert {i["check"] for i in r["issues"]} == {"sides"}  # PMS 185 == PANTONE 185 C; back page missing
    r = ticket_check.compare(analyse_pdf(make_pdf([CMYK_BOX] * 6)), JobSpec(binding="saddle stitch"))
    assert any("divisible by 4" in i["message"] for i in r["issues"])


# ------------------------------------------------------------------ estimate draft

def test_estimate_draft_business_card_and_booklet():
    card = estimate_draft.draft_from_facts(analyse_pdf(make_pdf([CMYK_BOX, BLACK_TEXT], size_in=(3.5, 2))))
    assert (card.product_guess, card.sides, card.colors_label, card.bleed) == ("business card", 2, "4/1", True)
    booklet = estimate_draft.draft_from_facts(analyse_pdf(make_pdf([CMYK_BOX] * 8, size_in=(5.5, 8.5))))
    assert booklet.product_guess.startswith("8-page booklet") and booklet.binding_guess == "saddle stitch"
    payload = estimate_draft.to_item_template(card, estimate_draft.DEFAULT_FIELD_MAP, "Business Cards")
    assert payload["fields"]["colorsSide1"] == "4" and payload["status"].startswith("DRAFT")


def test_field_map_file(tmp_path):
    f = tmp_path / "map.json"
    f.write_text(json.dumps({"pages": "PageCount"}))
    assert estimate_draft.load_field_map(f) == {"pages": "PageCount"}
    f.write_text("[1, 2]")
    with pytest.raises(ValueError, match="JSON object"):
        estimate_draft.load_field_map(f)


# ------------------------------------------------------------------ auto-fix plan

def _analysis(*issues):
    return {"verdict": "x", "issues": [
        {"category": c, "title": c, "pages": [1], "severity": "error", "fix_owner": o,
         "explanation": {"prepress": "hint"}} for c, o in issues]}


def test_autofix_plan():
    fix_map = {"rgb_color": {"route_to": "Auto-fix", "action_list": "Convert"},
               "overprint": {"route_to": "Auto-fix", "needs_review": True}}
    plan = autofix.plan_fixes(_analysis(("rgb_color", "prepress"), ("overprint", "prepress")), fix_map,
                              [{"id": "5", "name": "Auto-fix"}])
    assert plan["suggestion"]["action"] == "route" and plan["suggestion"]["connection_available"]
    assert "checked on a proof" in plan["suggestion"]["reason"]
    plan = autofix.plan_fixes(_analysis(("rgb_color", "prepress"), ("low_resolution", "customer")), fix_map)
    assert plan["suggestion"]["action"] == "ask_customer" and len(plan["customer"]) == 1
    assert autofix.plan_fixes(_analysis(("fonts_not_embedded", "either")), {})["suggestion"]["action"] == "prepress"


# ------------------------------------------------------------------ job matching

def test_job_matching():
    found = job_matching.find_job_numbers("Re: Job #123456 proofs - file 654321_Brochure.pdf and ORD1001")
    assert [c.job_number for c in found] == ["123456", "ORD1001", "654321"]
    custom = job_matching.compile_patterns(r"\bDP-(\d{5})\b")
    assert [c.job_number for c in job_matching.find_job_numbers("see DP-44321", custom)] == ["44321"]
    with pytest.raises(ValueError, match="capture group"):
        job_matching.compile_patterns(r"\d+")


# ------------------------------------------------------------------ Pace gateway

def test_pace_query_file(tmp_path):
    good = tmp_path / "q.sql"
    good.write_text("-- name: job_spec\nSELECT 1 AS job_number -- c\nWHERE x = %(job_number)s;\n"
                    "-- name: job_status\nWITH a AS (SELECT 1) SELECT * FROM a\n")
    q = pace.load_queries(good)
    assert set(q) == {"job_spec", "job_status"} and q["job_spec"].endswith("%(job_number)s")
    bad = tmp_path / "bad.sql"
    bad.write_text("-- name: job_spec\nDELETE FROM job\n")
    with pytest.raises(pace.PaceError, match="single SELECT"):
        pace.load_queries(bad)
    bad.write_text("-- name: job_spec\nSELECT 1; DROP TABLE job\n")
    with pytest.raises(pace.PaceError, match="single SELECT"):
        pace.load_queries(bad)


def test_example_queries_file_is_valid():
    q = pace.load_queries(Path(__file__).resolve().parents[1] / "pace_queries.example.sql")
    assert set(q) == {"job_spec", "job_status", "similar_jobs"}


def test_postgres_gateway_is_read_only(monkeypatch):
    executed, flags = [], {}

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None): executed.append((sql, params))
        def fetchmany(self, n): return [{"job_number": "1", "trim_width_in": 3.5, "trim_height_in": 2,
                                         "front_inks": 4, "back_inks": 4}]

    class Conn:
        read_only = False
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return Cur()
        def rollback(self): flags["rolled_back"] = True
        def __setattr__(self, k, v): flags[k] = v

    import sys
    import types
    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg.connect = lambda dsn, **kw: Conn()
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)
    gw = pace.PacePostgresGateway("postgresql://ro@db/pace", {"job_spec": "SELECT 1", "job_status": "SELECT 2"})
    spec = gw.get_job_spec("1")
    assert spec.trim_in == (3.5, 2.0) and spec.colors_label == "4/4"
    assert flags == {"read_only": True, "rolled_back": True}
    assert executed[0][0].startswith("SET LOCAL statement_timeout")
    with pytest.raises(pace.PaceWriteNotImplemented, match="by hand"):
        gw.update_job_status("1", "Proof Approved")


# ------------------------------------------------------------------ workflows

@pytest.fixture
def fake_pace():
    return pace.FakePaceGateway(jobs={"123456": {
        "spec": {"trim": "8.5 x 11", "pages": 2, "colors": "4/4"},
        "status": {"status": "Proofing", "customer": "ACME"}}})


async def test_approve_proof_dry_run_then_execute(client, fake, fake_pace):
    plan = await workflows.approve_proof(client, fake_pace, "job-1", "Sam", "123456")
    assert plan["dry_run"] and [s["status"] for s in plan["steps"]] == ["planned"] * 3
    assert fake.routed == [] and fake_pace.writes == []

    done = await workflows.approve_proof(client, fake_pace, "job-1", "Sam", "123456",
                                         metadata=[{"id": "cpMF_1", "name": "Approved by", "value": "Sam"}],
                                         dry_run=False)
    assert [s["status"] for s in done["steps"]] == ["done", "done", "done"]
    assert json.loads(fake.routed[0]["connections"]) == ["11"]
    assert [w["kind"] for w in fake_pace.writes] == ["status", "note"]


async def test_approve_proof_pace_write_not_implemented_becomes_manual(client, fake, fake_pace):
    fake_pace.allow_writes = False
    done = await workflows.approve_proof(client, fake_pace, "job-1", "Sam", "123456", dry_run=False)
    assert [s["status"] for s in done["steps"]] == ["done", "manual", "manual"]


async def test_approve_proof_refuses_job_not_in_checkpoint(client, fake_pace):
    with pytest.raises(SwitchError, match="not waiting at a checkpoint"):
        await workflows.approve_proof(client, fake_pace, "job-2", "Sam")


# ------------------------------------------------------------------ digest

async def test_digest(client):
    d = await digest.build_digest(client, hours=8, stuck_after_hours=1)
    assert len(d["waiting"]) == 1 and d["stuck"][0]["name"] == "ORD1001_Brochure.pdf"
    assert d["flows_not_running"] == [{"id": "2", "name": "Imposition", "status": "stopped"}]
    md = digest.render_markdown(d)
    assert "Needs attention" in md and "2x Customer PDFs / PitStop Server" in md


def test_webhook_requires_https():
    with pytest.raises(ValueError, match="https"):
        digest.post_webhook("http://hooks.example/x", "hi")


def test_webhook_posts_text(monkeypatch):
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, json=json)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(digest.httpx, "post", fake_post)
    digest.post_webhook("https://hooks.example/x", "hello")
    assert sent == {"url": "https://hooks.example/x", "json": {"text": "hello"}}


# ------------------------------------------------------------------ MCP tools

async def _call(server, name, args):
    async with Client(server) as c:
        return await c.call_tool(name, args)


def _server(settings, fake, pace_gw=None, **overrides):
    from switch_mcp.config import Settings

    s = Settings(**{**settings.__dict__, **overrides})
    return build_server(s, SwitchClient(s, transport=httpx.MockTransport(fake)), pace=pace_gw)


async def test_tool_compare_file_to_ticket_with_pace(settings, fake, fake_pace):
    pdf = settings.upload_dirs[0] / "a.pdf"
    pdf.write_bytes(make_pdf([CMYK_BOX]))
    server = _server(settings, fake, fake_pace)
    r = await _call(server, "compare_file_to_ticket", {"file_path": str(pdf), "pace_job_number": "123456"})
    assert not r.is_error, r.content[0].text
    assert r.structured_content["verdict"] == "does_not_match"  # ordered 2 pages / 4/4, file has 1 page
    # An explicit colors argument replaces Pace's inks.
    r = await _call(server, "compare_file_to_ticket", {"file_path": str(pdf), "pace_job_number": "123456",
                                                       "colors": "4/0", "pages": 1})
    assert r.structured_content["verdict"] == "matches_ticket", r.structured_content["issues"]


async def test_tool_compare_without_pace_needs_details(settings, fake):
    pdf = settings.upload_dirs[0] / "a.pdf"
    pdf.write_bytes(make_pdf([CMYK_BOX]))
    server = _server(settings, fake)
    r = await _call(server, "compare_file_to_ticket", {"file_path": str(pdf), "pace_job_number": "1"})
    assert r.is_error and "Pace isn't connected" in r.content[0].text
    r = await _call(server, "compare_file_to_ticket", {"file_path": str(pdf), "trim": "8.5x11", "colors": "4/0"})
    assert r.structured_content["verdict"] == "matches_ticket"


async def test_tool_draft_and_facts(settings, fake, fake_pace):
    pdf = settings.upload_dirs[0] / "card.pdf"
    pdf.write_bytes(make_pdf([CMYK_BOX, CMYK_BOX], size_in=(8.5, 11)))
    r = await _call(_server(settings, fake, fake_pace), "draft_item_from_pdf", {"file_path": str(pdf)})
    data = r.structured_content
    assert data["draft"]["product_guess"] == "letter / flyer" and data["field_map_is_placeholder"]
    assert data["similar_pace_jobs"][0]["job_number"] == "123456"
    r = await _call(_server(settings, fake), "pdf_facts", {"file_path": str(pdf)})
    assert r.structured_content["page_count"] == 2


async def test_tool_plan_autofixes_and_matching(settings, fake, tmp_path):
    fix_map = tmp_path / "fix.json"
    fix_map.write_text(json.dumps({"rgb_color": {"route_to": "Approve"}}))
    server = _server(settings, fake, autofix_map=str(fix_map))
    r = await _call(server, "plan_autofixes", {"job_id": "job-1"})
    assert r.structured_content["suggestion"]["action"] == "ask_customer"  # low-res images need the customer
    # The only mapped category (rgb_color) was already fixed by PitStop, so nothing is left to auto-fix.
    assert r.structured_content["auto_fixable"] == [] and r.structured_content["configured"] is True
    r = await _call(server, "find_job_numbers", {"text": "Job 777777 artwork"})
    assert r.structured_content["candidates"][0]["job_number"] == "777777"


async def test_tool_pace_job_only_when_connected(settings, fake, fake_pace):
    async with Client(_server(settings, fake)) as c:
        assert "pace_job" not in {t.name for t in (await c.list_tools()).tools}
    r = await _call(_server(settings, fake, fake_pace), "pace_job", {"job_number": "123456"})
    assert r.structured_content["status"]["status"] == "Proofing"


async def test_tool_approve_proof_defaults_to_dry_run(settings, fake, fake_pace):
    server = _server(settings, fake, fake_pace, allow_write=True)
    r = await _call(server, "approve_proof", {"job_id": "job-1", "approved_by": "Sam", "pace_job_number": "123456"})
    assert r.structured_content["dry_run"] is True and fake.routed == []


async def test_tool_morning_digest(settings, fake):
    r = await _call(_server(settings, fake), "morning_digest", {"stuck_after_hours": 1})
    assert "Switch digest" in r.structured_content["markdown"]


def test_escaped_pdf_names_are_decoded():
    from switch_mcp.automation.pdf_facts import _name

    assert _name(NameObject("/PANTONE#20185#20C")) == "PANTONE 185 C"
    assert ticket_check._norm_spot("PANTONE 185 C") == ticket_check._norm_spot("PMS 185") == "185"


def test_proportional_size_is_called_scalable():
    facts = analyse_pdf(make_pdf([CMYK_BOX], size_in=(8.25, 10.75)))
    r = ticket_check.compare(facts, JobSpec.from_dict({"trim": "8.5 x 11"}))
    assert "same shape" in r["issues"][0]["message"]


def test_example_maps_load():
    root = Path(__file__).resolve().parents[1]
    fix_map = autofix.load_autofix_map(root / "autofix_map.example.json")
    assert "_comment" not in fix_map and fix_map["rgb_color"]["route_to"] == "Auto-fix"
    field_map = estimate_draft.load_field_map(root / "item_template_map.example.json")
    assert "_comment" not in field_map and field_map["pages"] == "pages"


def test_metric_bleed_meets_eighth_inch_and_short_bleed_reports_amount():
    three_mm = analyse_pdf(make_pdf([CMYK_BOX], bleed_in=3 / 25.4))
    assert ticket_check.compare(three_mm, JobSpec.from_dict({"trim": "8.5x11"}))["issues"] == []
    short = analyse_pdf(make_pdf([CMYK_BOX], bleed_in=0.0625))
    [issue] = ticket_check.compare(short, JobSpec.from_dict({"trim": "8.5x11"}))["issues"]
    assert issue["found"] == '0.062"' and 'have only 0.062" bleed (1.6 mm)' in issue["message"]
    none = analyse_pdf(make_pdf([CMYK_BOX], bleed_in=None))
    [issue] = ticket_check.compare(none, JobSpec.from_dict({"trim": "8.5x11"}))["issues"]
    assert issue["found"] == "none" and "have no bleed" in issue["message"]
