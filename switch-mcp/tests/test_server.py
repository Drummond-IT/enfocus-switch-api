import json

import pytest
from mcp.client import Client

WRITE_TOOLS = {"submit_job", "route_job", "replace_job", "set_job_lock", "rush_job", "approve_proof"}


async def tools(server) -> set[str]:
    async with Client(server) as c:
        return {t.name for t in (await c.list_tools()).tools}


async def call(server, name, args=None):
    async with Client(server) as c:
        return await c.call_tool(name, args or {})


async def test_read_only_by_default(make_server):
    names = await tools(make_server())
    assert "explain_job_report" in names and "find_jobs" in names
    assert not names & WRITE_TOOLS
    assert "set_flow_running" not in names


async def test_write_tools_opt_in(make_server):
    names = await tools(make_server(allow_write=True))
    assert WRITE_TOOLS <= names and "set_flow_running" not in names
    assert "set_flow_running" in await tools(make_server(allow_flow_control=True))


async def test_jobs_needing_attention(make_server):
    r = await call(make_server(), "jobs_needing_attention")
    data = r.structured_content
    assert data["count"] == 1
    job = data["jobs"][0]
    assert job["name"] == "ORD1001_Brochure.pdf" and job["waiting_hours"] > 0
    assert [c["name"] for c in job["outConnections"]] == ["Approve", "Reject"]


async def test_find_jobs_by_order_number(make_server, fake):
    r = await call(make_server(), "find_jobs", {"name_contains": "ORD1002"})
    assert [j["id"] for j in r.structured_content["jobs"]] == ["job-2"]
    jobs_request = next(r for r in fake.requests if r.url.path == "/api/v1/jobs")
    sent = json.loads(jobs_request.url.params["filter"])
    assert sent == {"and": [{"name": {"contains": "ORD1002"}}]}


async def test_explain_job_report_for_customer(make_server, settings):
    r = await call(make_server(), "explain_job_report", {"job_id": "job-1", "audience": "customer"})
    assert not r.is_error
    data = r.structured_content
    assert data["verdict"] == "needs_customer"
    assert data["write_up"].startswith("We checked ORD1001_Brochure.pdf")
    assert (settings.download_dir / "job-1_report.xml").exists()


async def test_list_submit_points_describes_fields(make_server):
    r = await call(make_server(), "list_submit_points")
    [sp] = r.structured_content["result"]
    assert sp["submit_point"] == "1-3"
    paper = next(f for f in sp["metadata_fields"] if f["name"] == "Paper")
    assert paper["options"] == ["Gloss", "Matte", "Uncoated"]


async def test_submit_job_validates_metadata_and_folder(make_server, settings, tmp_path, fake):
    server = make_server(allow_write=True)
    pdf = settings.upload_dirs[0] / "ORD1005.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    r = await call(server, "submit_job", {"submit_point": "Upload PDF", "file_path": str(pdf),
                                          "metadata": {"Paper": "Satin"}})
    assert r.is_error and "Order number' is required" in r.content[0].text and "must be one of" in r.content[0].text

    outside = tmp_path / "elsewhere.pdf"
    outside.write_bytes(b"%PDF-1.4")
    r = await call(server, "submit_job", {"submit_point": "1-3", "file_path": str(outside),
                                          "metadata": {"Order number": "ORD1005"}})
    assert r.is_error and "outside the allowed folders" in r.content[0].text

    r = await call(server, "submit_job", {"submit_point": "upload", "file_path": str(pdf),
                                          "metadata": {"Order number": "ORD1005", "Paper": "Matte"}})
    assert not r.is_error, r.content
    assert r.structured_content == {"job_id": "job-new", "submitted_to": "Upload PDF", "flow": "Customer PDFs"}
    assert b"ORD1005" in fake.submitted[0]["body"]


async def test_route_job_by_connection_name(make_server, fake):
    server = make_server(allow_write=True)
    r = await call(server, "route_job", {"job_id": "job-1", "connection": "approve"})
    assert r.is_error and "Approved by' is required" in r.content[0].text

    r = await call(server, "route_job", {"job_id": "job-1", "connection": "approve",
                                         "metadata": {"Approved by": "CSR Sam"}})
    assert r.structured_content == {"job": "ORD1001_Brochure.pdf", "routed_to": "Approve"}
    assert json.loads(fake.routed[0]["connections"]) == ["11"]
    assert fake.routed[0]["updated"] == "2026-09-22T08:00:00"

    r = await call(server, "route_job", {"job_id": "job-2", "connection": "Approve"})
    assert r.is_error and "not waiting in a checkpoint" in r.content[0].text


async def test_problem_summary_groups_messages(make_server):
    r = await call(make_server(), "problem_summary", {"hours": 12})
    top = r.structured_content["top_errors"][0]
    assert top["count"] == 2 and top["message_pattern"] == "Preflight failed with # errors"


async def test_graphql_rejects_mutations(make_server):
    r = await call(make_server(), "graphql_query", {"query": "mutation { x }"})
    assert r.is_error


@pytest.mark.parametrize("audience", ["customer", "csr", "prepress"])
async def test_explain_pasted_report(make_server, audience):
    r = await call(make_server(), "explain_preflight_report",
                   {"report": "Error: PDF is encrypted", "audience": audience})
    assert r.structured_content["issues"][0]["category"] == "security"


async def test_unreachable_switch_gives_friendly_error(make_server):
    import httpx

    from switch_mcp.client import SwitchClient
    from switch_mcp.config import Settings
    from switch_mcp.server import build_server

    def boom(request):
        raise httpx.ConnectError("refused")

    s = Settings(url="http://switch.test:51088", username="demo", password="demo")
    server = build_server(s, SwitchClient(s, transport=httpx.MockTransport(boom)))
    r = await call(server, "list_flows")
    assert r.is_error and "Can't reach Switch" in r.content[0].text


async def test_tool_annotations(make_server):
    async with Client(make_server(allow_write=True, allow_flow_control=True)) as c:
        tools = {t.name: t.annotations for t in (await c.list_tools()).tools}
    for name in ("find_jobs", "get_job", "explain_job_report", "recent_messages", "graphql_query"):
        assert tools[name].read_only_hint is True and tools[name].open_world_hint is True, name
    for name in ("explain_preflight_report", "quick_check_pdf"):
        assert tools[name].read_only_hint is True and tools[name].open_world_hint is False, name
    for name in ("route_job", "replace_job", "set_flow_running"):
        assert tools[name].read_only_hint is False and tools[name].destructive_hint is True, name
    for name in ("submit_job", "set_job_lock", "rush_job"):
        assert tools[name].read_only_hint is False and tools[name].destructive_hint is False, name


@pytest.mark.parametrize("path", [r"\\attacker.example\share\x.pdf", "//attacker.example/share/x.pdf",
                                  r"\\?\UNC\attacker.example\share\x.pdf", "/\\attacker/x.pdf"])
async def test_network_paths_refused_before_filesystem_access(make_server, path, monkeypatch):
    import pathlib

    def boom(*args, **kwargs):
        raise AssertionError("filesystem touched")

    monkeypatch.setattr(pathlib.Path, "resolve", boom)
    r = await call(make_server(), "quick_check_pdf", {"file_path": path})
    assert r.is_error and "Network paths" in r.content[0].text


async def test_download_does_not_follow_planted_symlink(make_server, settings, tmp_path):
    settings.download_dir.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    (settings.download_dir / "job-1_report.xml").symlink_to(victim)
    r = await call(make_server(), "explain_job_report", {"job_id": "job-1"})
    assert not r.is_error
    assert victim.read_text() == "original"
    assert not (settings.download_dir / "job-1_report.xml").is_symlink()


async def test_non_regular_and_oversized_files_refused(make_server, settings):
    folder = settings.upload_dirs[0] / "folder.pdf"
    folder.mkdir()
    r = await call(make_server(), "quick_check_pdf", {"file_path": str(folder)})
    assert r.is_error and "Not a regular file" in r.content[0].text
    big = settings.upload_dirs[0] / "big.pdf"
    big.write_bytes(b"%PDF-1.4" + b"0" * (2 * 1024 * 1024))
    r = await call(make_server(max_file_mb=1), "quick_check_pdf", {"file_path": str(big)})
    assert r.is_error and "larger than SWITCH_MAX_FILE_MB" in r.content[0].text


async def test_hostile_report_text_is_fast_and_bounded(make_server):
    import time

    hostile = "Warning: x (1x on pages 1-1000000000)\n" + "colorspace not " * 3000 + "\n" + "font " * 20000
    start = time.monotonic()
    r = await call(make_server(), "explain_preflight_report", {"report": hostile})
    assert not r.is_error and time.monotonic() - start < 5
    assert all(len(i["pages"]) <= 10_000 for i in r.structured_content["issues"])


async def test_password_field_defaults_are_not_exposed(make_server, fake):
    fake.submit_points[0]["metadata"].append(
        {"id": "spMF_3", "name": "FTP password", "type": "password", "value": "hunter2", "displayField": True})
    r = await call(make_server(), "list_submit_points")
    assert "hunter2" not in r.content[0].text
