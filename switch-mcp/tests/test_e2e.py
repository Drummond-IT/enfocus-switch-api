"""End-to-end: the installed `enfocus-switch-mcp` command, over stdio, against a fake
Switch served over real HTTP that really decrypts the RSA-encrypted login password."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from fake_switch import FakeSwitch, make_keypair, serve

# A real PDF from the repo's original PHP example.
SAMPLE_PDF = Path(__file__).resolve().parents[2] / "src" / "Files" / "curl_test_file.pdf"
ALL_TOOLS = {
    "switch_status", "production_snapshot", "list_flows", "list_submit_points", "find_jobs",
    "jobs_needing_attention", "get_job", "get_job_thumbnail", "download_job", "explain_job_report",
    "explain_preflight_report", "quick_check_pdf", "recent_messages", "problem_summary", "graphql_query",
    "submit_job", "route_job", "replace_job", "set_job_lock", "rush_job", "set_flow_running",
    # automations
    "compare_file_to_ticket", "pdf_facts", "draft_item_from_pdf", "plan_autofixes", "find_job_numbers",
    "morning_digest", "approve_proof",
}


def _command() -> list[str]:
    exe = shutil.which("enfocus-switch-mcp", path=str(Path(sys.executable).parent))
    return [exe] if exe else [sys.executable, "-m", "switch_mcp"]


@pytest.fixture
def live(tmp_path: Path):
    key, pem = make_keypair()
    fake = FakeSwitch(username="automation", password="s3cr3t pass!", private_key=key)
    server = serve(fake)
    key_file = tmp_path / "switch-public.pem"
    key_file.write_bytes(pem)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    env_file = tmp_path / "config.env"
    env_file.write_text(
        "# test config\n"
        f"SWITCH_URL=http://127.0.0.1:{server.server_port}\n"
        "SWITCH_USERNAME=automation\n"
        "SWITCH_PASSWORD='s3cr3t pass!'\n"
        f"SWITCH_PUBLIC_KEY_PATH={key_file}\n"
        f"SWITCH_UPLOAD_DIRS={uploads}\n"
        f"SWITCH_DOWNLOAD_DIR={tmp_path / 'downloads'}\n"
    )
    env_file.chmod(0o600)
    yield fake, env_file, uploads, tmp_path
    server.shutdown()


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SWITCH_")}
    env["HOME"] = env.get("HOME", "/tmp")
    env.update(extra)
    return env


def _params(env_file: Path, **extra: str) -> StdioServerParameters:
    cmd = _command()
    return StdioServerParameters(command=cmd[0], args=[*cmd[1:], "--env-file", str(env_file)], env=_clean_env(**extra))


async def test_every_tool_end_to_end(live):
    fake, env_file, uploads, _ = live
    pdf = uploads / "ORD1005.pdf"
    pdf.write_bytes(SAMPLE_PDF.read_bytes())
    params = _params(env_file, SWITCH_ALLOW_WRITE="true", SWITCH_ALLOW_FLOW_CONTROL="true")

    async with Client(params) as c:
        assert {t.name for t in (await c.list_tools()).tools} == ALL_TOOLS

        async def ok(name, args=None):
            r = await c.call_tool(name, args or {})
            assert not r.is_error, f"{name}: {r.content[0].text if r.content else r}"
            return r

        status = (await ok("switch_status")).structured_content
        assert status["user"] == "automation" and status["connector"]["write_tools_enabled"] is True
        snap = (await ok("production_snapshot")).structured_content
        assert snap["by_status"] == {"alert": 1, "processing": 1}
        assert len((await ok("list_flows")).structured_content["result"]) == 2
        assert (await ok("list_submit_points")).structured_content["result"][0]["submit_point"] == "1-3"
        assert (await ok("find_jobs", {"name_contains": "ORD1001"})).structured_content["count"] == 1
        assert (await ok("jobs_needing_attention")).structured_content["jobs"][0]["id"] == "job-1"
        job = (await ok("get_job", {"job_id": "job-1"})).structured_content
        assert job["checkpoint_fields"][0]["name"] == "Approved by"
        thumb = await ok("get_job_thumbnail", {"job_id": "job-1"})
        assert thumb.content[0].type == "image" and thumb.content[0].mime_type == "image/png"
        saved = (await ok("download_job", {"job_id": "job-1"})).structured_content
        assert Path(saved["saved_to"]).read_bytes() == fake.job_file
        report = (await ok("explain_job_report", {"job_id": "job-1", "audience": "csr"})).structured_content
        assert report["verdict"] == "needs_customer" and report["write_up"].startswith("ORD1001_Brochure.pdf")
        assert (await ok("explain_preflight_report", {"report": "Error: Font Arial not embedded"})) \
            .structured_content["issues"][0]["category"] == "fonts_not_embedded"
        assert "summary" in (await ok("quick_check_pdf", {"file_path": str(pdf)})).structured_content
        assert (await ok("recent_messages", {"hours": 2})).structured_content["count"] == 2
        assert (await ok("problem_summary")).structured_content["error_count"] == 2
        assert (await ok("graphql_query", {"query": "{ jobs { count } }"})).structured_content["data"]

        # automations
        ticket = (await ok("compare_file_to_ticket", {"file_path": str(pdf), "trim": "85 x 55 mm", "pages": 2,
                                                       "colors": "4/4"})).structured_content
        assert ticket["verdict"] in ("matches_ticket", "check_with_customer"), ticket["issues"]
        draft = (await ok("draft_item_from_pdf", {"file_path": str(pdf)})).structured_content
        assert draft["draft"]["product_guess"].startswith("business card")
        assert (await ok("find_job_numbers", {"text": "Job 123456"})).structured_content["candidates"]
        assert "Switch digest" in (await ok("morning_digest")).structured_content["markdown"]
        assert (await ok("plan_autofixes", {"job_id": "job-1"})).structured_content["suggestion"]
        plan = (await ok("approve_proof", {"job_id": "job-1", "approved_by": "E2E"})).structured_content
        assert plan["dry_run"] is True

        sub = (await ok("submit_job", {"submit_point": "Upload PDF", "file_path": str(pdf),
                                        "metadata": {"Order number": "ORD1005", "Paper": "Matte"}})).structured_content
        assert sub["job_id"] == "job-new"
        await ok("route_job", {"job_id": "job-1", "connection": "Approve", "metadata": {"Approved by": "E2E"}})
        await ok("replace_job", {"job_id": "job-1", "file_path": str(pdf)})
        await ok("set_job_lock", {"job_id": "job-1", "locked": True})
        await ok("rush_job", {"job_id": "job-1"})
        await ok("set_flow_running", {"flow_id": "2", "running": True})

    # The password really went over the wire RSA-encrypted and was accepted.
    assert fake.tokens_issued == 1, "one login for the whole session"
    assert fake.open_sessions == set(), "session was logged out on shutdown"
    assert json.loads(fake.routed[0]["connections"]) == ["11"]
    assert ("replace", "job-1") in fake.actions and ("lock", "job-1") in fake.actions
    assert ("rush", "000A2") in fake.actions and ("start", "2") in fake.actions
    login = next(r for r in fake.requests if r.url.path == "/login")
    assert b"s3cr3t" not in login.content and json.loads(login.content)["password"].startswith("!@$")


async def test_read_only_by_default_and_hostile_inputs(live):
    fake, env_file, uploads, tmp_path = live
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-1.4 secret")
    async with Client(_params(env_file)) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert not names & {"submit_job", "route_job", "replace_job", "set_job_lock", "rush_job", "set_flow_running",
                            "approve_proof"}

        async def err(name, args):
            r = await c.call_tool(name, args)
            assert r.is_error, name
            return r.content[0].text

        # IDs that would escape a URL path or the download folder are refused before any request.
        before = len(fake.requests)
        assert "Invalid job id" in await err("download_job", {"job_id": "../../etc/passwd"})
        assert "Invalid job id" in await err("get_job", {"job_id": "job-1?action=route"})
        assert "Invalid job id" in await err("explain_job_report", {"job_id": "a/b"})
        assert len(fake.requests) == before
        # Files outside the allowed folders can't be read, even via symlinks.
        (uploads / "link.pdf").symlink_to(tmp_path / "secret.pdf")
        assert "outside the allowed folders" in await err("quick_check_pdf", {"file_path": str(tmp_path / "secret.pdf")})
        assert "outside the allowed folders" in await err("quick_check_pdf", {"file_path": str(uploads / "link.pdf")})
        assert "outside the allowed folders" in await err("explain_preflight_report", {"report_file": "/etc/passwd"})
        # XML entity bombs in a report are refused.
        bomb = ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
                "<EnfocusReport><PreflightReport><Errors><PreflightReportItem><Message>&b;</Message>"
                "</PreflightReportItem></Errors></PreflightReport></EnfocusReport>")
        assert "Refused to parse" in await err("explain_preflight_report", {"report": bomb})
        assert "Only queries" in await err("graphql_query", {"query": "mutation { x }"})


async def test_wrong_password_is_reported_clearly(live):
    _, env_file, *_ = live
    async with Client(_params(env_file, SWITCH_PASSWORD="nope")) as c:
        r = await c.call_tool("list_flows", {})
        assert r.is_error and "Wrong user name or password" in r.content[0].text


async def test_unconfigured_server_still_starts_and_explains(tmp_path):
    env = _clean_env(HOME=str(tmp_path))
    cmd = _command()
    async with Client(StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)) as c:
        r = await c.call_tool("list_flows", {})
        assert r.is_error and "SWITCH_USERNAME is not set" in r.content[0].text
        # Tools that don't need Switch keep working.
        r = await c.call_tool("explain_preflight_report", {"report": "Warning: Object uses DeviceRGB"})
        assert not r.is_error


def _check(env_file: Path, **extra: str) -> subprocess.CompletedProcess:
    cmd = _command()
    return subprocess.run([*cmd, "check", "--env-file", str(env_file)], capture_output=True, text=True,
                          env=_clean_env(**extra), timeout=60, check=False)


def test_check_command(live):
    fake, env_file, *_ = live
    ok = _check(env_file)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "Logged in to Switch as 'automation'" in ok.stdout and "All checks passed" in ok.stdout
    assert "s3cr3t" not in ok.stdout + ok.stderr, "password must never be printed"
    assert fake.open_sessions == set()

    bad = _check(env_file, SWITCH_PASSWORD="nope")
    assert bad.returncode == 1 and "Wrong user name or password" in bad.stdout

    env_file.chmod(0o644)
    assert "readable by other users" in _check(env_file).stdout

    down = _check(env_file, SWITCH_URL="http://127.0.0.1:9")
    assert down.returncode == 1 and "Could not reach Switch" in down.stdout


def test_check_reports_config_errors(tmp_path):
    env_file = tmp_path / "config.env"
    env_file.write_text("SWITCH_URL=switch.local\nSWITCH_UPLOAD_DIRS=/does/not/exist\n")
    env_file.chmod(0o600)
    r = _check(env_file)
    assert r.returncode == 2
    for expected in ("SWITCH_URL must look like", "SWITCH_USERNAME is not set", "not a folder: /does/not/exist"):
        assert expected in r.stdout


def test_version():
    r = subprocess.run([*_command(), "--version"], capture_output=True, text=True, timeout=30, check=False)
    assert r.returncode == 0 and r.stdout.startswith("enfocus-switch-mcp ")


def test_stdout_is_clean_protocol(live):
    """Nothing but JSON-RPC may go to stdout, or MCP clients disconnect."""
    _, env_file, *_ = live
    cmd = _command()
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    r = subprocess.run([*cmd, "--env-file", str(env_file)], input=json.dumps(init) + "\n", capture_output=True,
                       text=True, env=_clean_env(), timeout=30, check=False)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert lines and all(json.loads(ln)["jsonrpc"] == "2.0" for ln in lines)
    assert "Starting" in r.stderr


def test_digest_command(live):
    _, env_file, *_ = live
    r = subprocess.run([*_command(), "digest", "--env-file", str(env_file)], capture_output=True, text=True,
                       env=_clean_env(DIGEST_STUCK_HOURS="1"), timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    assert "**Switch digest**" in r.stdout and "ORD1001_Brochure.pdf" in r.stdout
    r = subprocess.run([*_command(), "digest", "--post", "--env-file", str(env_file)], capture_output=True,
                       text=True, env=_clean_env(), timeout=60, check=False)
    assert r.returncode == 2 and "DIGEST_WEBHOOK_URL" in r.stderr
