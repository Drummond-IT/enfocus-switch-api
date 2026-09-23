import json

import pytest
from mcp.client import Client

WRITE_TOOLS = {"submit_job", "route_job", "replace_job", "set_job_lock", "rush_job"}


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

    s = Settings(url="http://switch.test:51088", username="demo")
    server = build_server(s, SwitchClient(s, transport=httpx.MockTransport(boom)))
    r = await call(server, "list_flows")
    assert r.is_error and "Can't reach Switch" in r.content[0].text
