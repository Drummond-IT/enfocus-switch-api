"""Config-driven Pace API writer: escaping, allow-list, off-by-default, audit."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from mcp.client import Client

from switch_mcp.automation import pace, workflows
from switch_mcp.automation.audit import AuditLog
from switch_mcp.client import SwitchClient
from switch_mcp.config import Settings
from switch_mcp.server import build_server

EXAMPLE = Path(__file__).resolve().parents[1] / "pace_api.example.json"

JSON_OPS = {"operations": {
    "update_job_status": {"method": "POST", "path": "/jobs/{job_number}/status", "content_type": "application/json",
                          "body": '{"job": {job_number}, "status": {status}, "note": {note}}'},
    "add_job_note": {"method": "POST", "path": "/jobs/{job_number}/notes", "content_type": "application/json",
                     "body": '{"job": {job_number}, "note": {note}}'},
}}
XML_OPS = {"operations": {
    "add_job_note": {"method": "PUT", "path": "/note", "content_type": "text/xml",
                     "body": "<note job=\"{job_number}\">{note}</note>"},
}}


class Recorder:
    def __init__(self, status: int = 200):
        self.requests: list[httpx.Request] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, text="nope" if self.status >= 400 else "{}")


def writer(ops=JSON_OPS, status=200, **kw):
    rec, events = Recorder(status), []
    defaults = {"allow_write": True, "allowed_statuses": ["Proof Approved"]}
    w = pace.PaceApiWriter(ops, "https://pace.test/api", "svc", "secret", transport=httpx.MockTransport(rec),
                           audit=events.append, **{**defaults, **kw})
    return w, rec, events


def test_example_config_is_valid():
    cfg = pace.PaceApiWriter.load_config(EXAMPLE)
    assert set(cfg["operations"]) == {"update_job_status", "add_job_note"}


def test_config_validation(tmp_path):
    bad = tmp_path / "p.json"
    for data, msg in (({"operations": {"delete_job": {}}}, "unknown operation"),
                      ({"operations": {"add_job_note": {"method": "POST", "path": "/x"}}}, "missing body"),
                      ({"operations": {"add_job_note": {"method": "DELETE", "path": "/x", "body": "x"}}}, "method"),
                      ([], "operations")):
        bad.write_text(json.dumps(data))
        with pytest.raises(pace.PaceError, match=msg):
            pace.PaceApiWriter.load_config(bad)


def test_json_values_are_escaped_and_path_quoted():
    w, rec, events = writer()
    w.update_job_status("12/34", "Proof Approved", 'He said "ok"\n}, "status": "Hacked')
    req = rec.requests[0]
    assert req.url.raw_path == b"/api/jobs/12%2F34/status"
    body = json.loads(req.content)  # still valid JSON: the injection attempt is just text
    assert body == {"job": "12/34", "status": "Proof Approved", "note": 'He said "ok"\n}, "status": "Hacked'}
    assert req.headers["authorization"].startswith("Basic ")
    assert events[-1]["result"] == "done" and "note" not in events[-1] and events[-1]["note_chars"] > 0


def test_xml_values_are_escaped():
    w, rec, _ = writer(XML_OPS)
    w.add_job_note('1"2', "<b>&</b>")
    assert rec.requests[0].content.decode() == '<note job="1&quot;2">&lt;b&gt;&amp;&lt;/b&gt;</note>'


def test_status_allow_list_and_empty_list():
    w, rec, _ = writer()
    with pytest.raises(pace.PaceError, match="not in PACE_ALLOWED_STATUSES"):
        w.update_job_status("1", "Closed")
    w, rec, _ = writer(allowed_statuses=[])
    with pytest.raises(pace.PaceError, match="is empty"):
        w.update_job_status("1", "Proof Approved")
    assert rec.requests == []


def test_writes_off_or_unconfigured_become_manual():
    w, rec, events = writer(allow_write=False)
    with pytest.raises(pace.PaceWriteNotImplemented, match="by hand"):
        w.add_job_note("1", "hi")
    assert rec.requests == [] and events[-1]["reason"] == "PACE_ALLOW_WRITE is off"
    w, rec, events = writer(XML_OPS)
    with pytest.raises(pace.PaceWriteNotImplemented, match="by hand"):
        w.update_job_status("1", "Proof Approved")  # operation not in the config
    with pytest.raises(pace.PaceWriteNotImplemented, match="aren't configured"):
        pace.PaceApiWriter().add_job_note("1", "x")


def test_http_error_is_reported_and_audited():
    w, _, events = writer(status=500)
    with pytest.raises(pace.PaceError, match="HTTP 500"):
        w.add_job_note("1", "x")
    assert events[-1] == {"operation": "add_job_note", "job_number": "1", "note_chars": 1,
                          "result": "failed", "http_status": 500}


def test_gateway_from_env_write_only(tmp_path):
    cfg = tmp_path / "pace_api.json"
    cfg.write_text(json.dumps(JSON_OPS))
    assert pace.gateway_from_env({}) is None
    gw = pace.gateway_from_env({"PACE_API_CONFIG": str(cfg), "PACE_API_URL": "https://pace.test"})
    assert isinstance(gw, pace.PaceWriteOnlyGateway) and gw.can_read is False
    with pytest.raises(pace.PaceNotConfigured):
        gw.get_job_spec("1")
    with pytest.raises(pace.PaceWriteNotImplemented):  # PACE_ALLOW_WRITE not set
        gw.add_job_note("1", "x")


def test_audit_log_file(tmp_path):
    log = AuditLog(tmp_path / "a" / "audit.jsonl", actor="test")
    log({"action": "x", "job": "1"})
    log({"action": "y"})
    lines = [json.loads(line) for line in (tmp_path / "a" / "audit.jsonl").read_text().splitlines()]
    assert [r["action"] for r in lines] == ["x", "y"] and lines[0]["actor"] == "test"
    assert (tmp_path / "a" / "audit.jsonl").stat().st_mode & 0o077 == 0


async def test_approve_proof_with_real_writer(client, fake):
    w, rec, _ = writer()
    gw = pace.PaceWriteOnlyGateway(w)
    done = await workflows.approve_proof(client, gw, "job-1", "Sam", "123456", dry_run=False)
    assert [s["status"] for s in done["steps"]] == ["done", "done", "done"]
    assert [r.url.path for r in rec.requests] == ["/api/jobs/123456/status", "/api/jobs/123456/notes"]


async def test_write_only_pace_hides_read_tools(settings: Settings, fake, tmp_path):
    cfg = tmp_path / "pace_api.json"
    cfg.write_text(json.dumps(JSON_OPS))
    s = Settings(**{**settings.__dict__, "pace_api_config": str(cfg), "pace_api_url": "https://pace.test",
                    "allow_write": True})
    server = build_server(s, SwitchClient(s, transport=httpx.MockTransport(fake)))
    async with Client(server) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert "pace_job" not in names and "approve_proof" in names
        pdf_missing = await c.call_tool("compare_file_to_ticket", {"file_path": "/nope.pdf", "pace_job_number": "1"})
        assert pdf_missing.is_error and "Pace database isn't connected" in pdf_missing.content[0].text


def test_settings_validate_pace_write(tmp_path):
    cfg = tmp_path / "p.json"
    cfg.write_text("{}")
    s = Settings(username="u", password="p", pace_api_config=str(cfg), pace_allow_write=True)
    errors, warnings = s.validate()
    assert any("PACE_ALLOWED_STATUSES is empty" in e for e in errors)
    s.pace_allowed_statuses, s.pace_api_url = "Proof Approved", "http://pace.remote"
    errors, warnings = s.validate()
    assert not errors and any("not https" in w for w in warnings) and any("Pace writes are ON" in w for w in warnings)
    assert Settings.from_env({"PACE_ALLOW_WRITE": "yes", "PACE_ALLOWED_STATUSES": "A,B"}).pace_env()[
        "PACE_ALLOWED_STATUSES"] == "A,B"
