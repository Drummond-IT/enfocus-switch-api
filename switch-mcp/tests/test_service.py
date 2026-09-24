"""Automation web service: auth, limits, and each endpoint (fake Switch + fake Pace)."""

from __future__ import annotations

import json
import sys

import httpx
import pytest
from starlette.testclient import TestClient
from test_automation import CMYK_BOX, make_pdf

from switch_mcp import cli, service
from switch_mcp.automation import pace
from switch_mcp.client import SwitchClient
from switch_mcp.config import Settings

KEYS = {"portal": "portal-secret-key", "switch": "switch-secret-key"}


@pytest.fixture
def api_keys():
    return [service.ApiKey("portal", service.hash_key(KEYS["portal"]), ["preflight"]),
            service.ApiKey("switch", service.hash_key(KEYS["switch"]),
                           ["switch_events", "match_job", "proof_approve"])]


@pytest.fixture
def fake_pace():
    return pace.FakePaceGateway(jobs={
        "123456": {"spec": {"trim": "8.5 x 11", "pages": 1, "colors": "4/0", "customer": "ACME Corp"},
                   "status": {"status": "Proofing", "customer": "ACME Corp"}},
        "ORD1001": {"spec": {"trim": "8.5 x 11"}, "status": {"status": "Proofing"}},
    })


@pytest.fixture
def status_map(tmp_path):
    p = tmp_path / "status_map.json"
    p.write_text(json.dumps({"events": {
        "preflight_failed": {"pace_status": "Waiting on Customer", "note": "Preflight problems", "explain": True},
        "preflight_done": {"explain": True, "auto_route": True},
        "proof_sent": {"pace_status": "Proof Sent"},
    }}))
    return p


@pytest.fixture
def make_app(settings, fake, fake_pace, api_keys, status_map, tmp_path):
    def _make(pace_gw=fake_pace, **overrides):
        s = Settings(**{**settings.__dict__, "service_status_map": str(status_map),
                        "audit_log": str(tmp_path / "audit.jsonl"), **overrides})
        app = service.create_app(s, SwitchClient(s, transport=httpx.MockTransport(fake)), pace_gw, api_keys)
        return TestClient(app)
    return _make


def auth(name="switch"):
    return {"Authorization": f"Bearer {KEYS[name]}"}


# ------------------------------------------------------------------ keys, auth, limits

def test_new_key_and_load(tmp_path):
    key, entry = service.new_key("portal", ["preflight"])
    assert key.startswith("swk_") and entry["portal"]["sha256"] == service.hash_key(key)
    p = tmp_path / "keys.json"
    p.write_text(json.dumps({"_comment": "x", **entry}))
    assert service.load_keys(p)[0].scopes == ["preflight"]
    for bad in ({"a": {"sha256": "nothex", "scopes": ["preflight"]}},
                {"a": {"sha256": "0" * 64, "scopes": ["admin"]}}, {}):
        p.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            service.load_keys(p)
    with pytest.raises(ValueError):
        service.new_key("bad name!", ["preflight"])


def test_status_map_validation(tmp_path):
    p = tmp_path / "m.json"
    for bad in ({"events": {"Bad-Name": {}}}, {"events": {"ok": {"shell": "rm"}}}, {"nope": 1}):
        p.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            service.load_status_map(p)


def test_example_status_map_loads():
    from pathlib import Path

    m = service.load_status_map(Path(__file__).resolve().parents[1] / "service_status_map.example.json")
    assert m["proof_sent"].pace_status == "Proof Sent"


def test_health_needs_no_key_but_everything_else_does(make_app, tmp_path):
    with make_app() as c:
        assert c.get("/health").json()["ok"] is True
        r = c.post("/switch/match-job", json={"text": "Job 123456"})
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert c.post("/switch/match-job", json={"text": "x"}, headers={"X-API-Key": "wrong"}).status_code == 401
        # The portal key can't use Switch endpoints.
        assert c.post("/switch/match-job", json={"text": "x"}, headers=auth("portal")).status_code == 403
        assert c.post("/switch/match-job", json={"text": "x"}, headers={"X-API-Key": KEYS["switch"]}).status_code == 200
    lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [line["status"] for line in lines] == [401, 401, 403, 200]
    assert KEYS["switch"] not in (tmp_path / "audit.jsonl").read_text()


def test_rate_limit(make_app):
    with make_app(service_rate_limit_per_min=2) as c:
        codes = [c.post("/switch/match-job", json={"text": "x"}, headers=auth()).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_rate_limiter_window():
    rl = service.RateLimiter(1)
    assert rl.allow("a", 0) and not rl.allow("a", 30) and rl.allow("a", 61) and rl.allow("b", 61)


def test_json_limits(make_app):
    with make_app() as c:
        assert c.post("/switch/match-job", content=b"text=x", headers=auth()).status_code == 415
        assert c.post("/switch/match-job", content=b"[1]", headers={**auth(), "Content-Type": "application/json"}
                      ).status_code == 400
        big = json.dumps({"text": "x" * (service.MAX_JSON_BYTES + 10)})
        assert c.post("/switch/match-job", content=big, headers={**auth(), "Content-Type": "application/json"}
                      ).status_code == 413
        r = c.post("/switch/match-job", json={"text": 5.5}, headers=auth())
        assert r.status_code == 400


# ------------------------------------------------------------------ /preflight

def upload(c, pdf, **params):
    return c.post("/preflight", params=params, content=pdf,
                  headers={**auth("portal"), "Content-Type": "application/pdf"})


def test_preflight_upload_file_only(make_app):
    with make_app() as c:
        r = upload(c, make_pdf([CMYK_BOX], bleed_in=None), name="../../etc/flyer.pdf")
    body = r.json()
    assert r.status_code == 200 and body["file"] == "flyer.pdf"
    assert body["verdict"] in ("prepress_can_fix", "needs_customer") and "bleed" in body["message"].lower()
    assert body["ticket"] is None and "prepress" not in json.dumps(body["issues"]).lower().split("what_to_do")[0]


def test_preflight_upload_against_ticket(make_app):
    with make_app() as c:
        r = upload(c, make_pdf([CMYK_BOX]), trim="5.5 x 8.5", colors="4/0")
    assert r.json()["ticket"]["verdict"] == "does_not_match"
    assert any("order is 5.5\" x 8.5\"" in i["message"] for i in r.json()["ticket"]["issues"])


def test_preflight_pace_job_only_for_matching_customer(make_app):
    with make_app() as c:
        mine = upload(c, make_pdf([CMYK_BOX]), job_number="123456", customer="acme corp.").json()
        theirs = upload(c, make_pdf([CMYK_BOX]), job_number="123456", customer="Someone Else").json()
    assert mine["ticket"]["verdict"] == "matches_ticket"
    assert theirs["ticket"]["verdict"] == "unknown" and "couldn't find" in theirs["ticket"]["message"]
    assert "8.5" not in json.dumps(theirs["ticket"])  # nothing about the other customer's job leaks


def test_preflight_rejects_bad_uploads(make_app):
    with make_app(service_max_upload_mb=1) as c:
        assert upload(c, b"hello, not a pdf").status_code == 415
        assert upload(c, b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 10)).status_code == 413
        assert upload(c, b"%PDF-1.7\ngarbage").status_code == 422
        assert upload(c, make_pdf([CMYK_BOX]), trim="huge").status_code == 400
        assert upload(c, make_pdf([CMYK_BOX]), job_number="12/../3").status_code == 400


# ------------------------------------------------------------------ /switch/match-job

def test_match_job_with_pace_status(make_app):
    with make_app() as c:
        r = c.post("/switch/match-job", json={"text": "RE: Job 123456 artwork attached"}, headers=auth()).json()
    assert r["job_number"] == "123456" and r["candidates"][0]["pace"]["status"] == "Proofing"


# ------------------------------------------------------------------ /proof/approve

def test_proof_approve_dry_run_by_default(make_app, fake, fake_pace):
    with make_app() as c:
        r = c.post("/proof/approve", json={"job_id": "job-1", "approved_by": "Sam", "pace_job_number": "123456"},
                   headers=auth()).json()
    assert r["dry_run"] is True and fake.routed == [] and fake_pace.writes == []


def test_proof_approve_live(make_app, fake, fake_pace):
    with make_app(service_dry_run=False) as c:
        r = c.post("/proof/approve", json={"job_id": "job-1", "approved_by": "Sam", "pace_job_number": "123456"},
                   headers=auth()).json()
        assert [s["status"] for s in r["steps"]] == ["done", "done", "done"]
        assert [w["kind"] for w in fake_pace.writes] == ["status", "note"]
        # The caller may ask for a dry run even when the service is live.
        r = c.post("/proof/approve", json={"job_id": "job-1", "approved_by": "Sam", "dry_run": True}, headers=auth())
        assert r.json()["dry_run"] is True
        assert c.post("/proof/approve", json={"job_id": "job-2", "approved_by": "Sam"}, headers=auth()).status_code == 409
        assert c.post("/proof/approve", json={"job_id": "../x", "approved_by": "Sam"}, headers=auth()).status_code == 502
        assert c.post("/proof/approve", json={"job_id": "job-1"}, headers=auth()).status_code == 400


# ------------------------------------------------------------------ /switch/events

def test_event_unknown_and_missing_job(make_app):
    with make_app() as c:
        r = c.post("/switch/events", json={"event": "rm_rf", "job_id": "job-1"}, headers=auth())
        assert r.status_code == 400 and "proof_sent" in r.json()["error"]
        assert c.post("/switch/events", json={"event": "proof_sent", "job_id": "nope"}, headers=auth()).status_code == 404


def test_event_explains_and_plans_status_in_dry_run(make_app, fake_pace):
    with make_app() as c:
        r = c.post("/switch/events", json={"event": "preflight_failed", "job_id": "job-1"}, headers=auth()).json()
    assert r["pace_job_number"] == "ORD1001"  # found in the Switch job name
    assert r["explanation"]["verdict"] == "needs_customer" and "Font" in r["explanation"]["csr"]
    assert r["steps"][-1]["status"] == "planned" and fake_pace.writes == []


def test_event_live_updates_pace(make_app, fake_pace):
    with make_app(service_dry_run=False) as c:
        r = c.post("/switch/events", json={"event": "proof_sent", "job_id": "job-1", "pace_job_number": "123456"},
                   headers=auth()).json()
    assert r["steps"] == [{"system": "pace", "action": "update_status", "status": "done", "result": "ok",
                           "detail": "Set Pace job 123456 status to 'Proof Sent'"}]
    assert fake_pace.writes[0]["status"] == "Proof Sent" and "ORD1001_Brochure.pdf" in fake_pace.writes[0]["note"]


def test_event_without_pace_is_manual(make_app):
    with make_app(pace_gw=None, service_dry_run=False) as c:
        r = c.post("/switch/events", json={"event": "proof_sent", "job_id": "job-1"}, headers=auth()).json()
    assert r["steps"][0]["status"] == "manual"


def test_event_auto_route(make_app, fake, tmp_path):
    fake.report = b"Error: Page uses RGB color space\n"
    fix_map = tmp_path / "fix.json"
    fix_map.write_text(json.dumps({"rgb_color": {"route_to": "Approve", "action_list": "Convert to CMYK"}}))
    with make_app(autofix_map=str(fix_map), service_dry_run=False) as c:  # SERVICE_AUTO_ROUTE off
        r = c.post("/switch/events", json={"event": "preflight_done", "job_id": "job-1"}, headers=auth()).json()
        assert r["steps"][0]["status"] == "skipped" and fake.routed == []
    with make_app(autofix_map=str(fix_map), service_dry_run=False, service_auto_route=True) as c:
        r = c.post("/switch/events", json={"event": "preflight_done", "job_id": "job-1"}, headers=auth()).json()
    assert r["steps"][0]["status"] == "done" and json.loads(fake.routed[0]["connections"]) == ["11"]


# ------------------------------------------------------------------ config + CLI

def test_validate_service():
    s = Settings(username="u", password="p")
    errors, _ = s.validate_service()
    assert any("SERVICE_KEYS_FILE" in e for e in errors)
    s = Settings.from_env({"SERVICE_HOST": "0.0.0.0", "SERVICE_DRY_RUN": "false", "SERVICE_TLS_CERT": "/x"})
    errors, _warnings = s.validate_service()
    assert any("both SERVICE_TLS_CERT" in e for e in errors) and not s.service_dry_run


def test_cli_service_key_writes_hashed_private_file(tmp_path, capsys):
    keys = tmp_path / "keys.json"
    settings = Settings(service_keys_file=str(keys))
    assert cli.run_service_key(settings, "portal", "preflight") == 0
    key = next(w for w in capsys.readouterr().out.split() if w.startswith("swk_"))
    stored = json.loads(keys.read_text())
    assert stored["portal"]["sha256"] == service.hash_key(key) and key not in keys.read_text()
    assert sys.platform == "win32" or keys.stat().st_mode & 0o077 == 0
    assert cli.run_service_key(settings, "portal", "admin") == 2


def test_rfq_validate_endpoint(make_app, api_keys):
    api_keys.append(service.ApiKey("forms", service.hash_key("forms-key"), ["rfq"]))
    with make_app() as c:
        r = c.post("/rfq/validate", json={"product": "flyer", "quantity": 500, "trim": "8.5 x 11",
                                          "colors": "4/4", "stock": "100lb gloss", "evil": {"x": 1}},
                   headers={"X-API-Key": "forms-key"})
    assert r.status_code == 200 and r.json()["ready_to_quote"] is True
